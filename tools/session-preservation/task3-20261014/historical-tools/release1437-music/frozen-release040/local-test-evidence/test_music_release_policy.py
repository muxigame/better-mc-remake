"""Offline release-policy regressions against real application classes.

No network, MC, cloud SDK requests, production settings or actual credentials.
Storage/auth fixtures are explicit; VIP entitlement is not tested here.
"""
import concurrent.futures
import hashlib
import io
import tempfile
import threading
import unittest
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.music_intent_guard import MusicIntentGuard
from app.music_production import configure, configure_from_environment
from app.music_production_storage import ProductionBudget, ProductionLimits
from app.music_upload import Credentials, MAX_BYTES, MusicError, MusicUploads, music_upload_router, post_policy


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def tone():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\x00\x01' * 8000)
    return output.getvalue()


class OfflineStorage:
    bucket = 'muxigame-music-cn-hangzhou'
    region = 'cn-hangzhou'

    def __init__(self):
        self.objects, self.leases, self.tickets = {}, [], []

    def ticket(self, key, size, now):
        self.tickets.append((key, size))
        return post_policy(self.bucket, self.region, key, size,
                           Credentials('OFFLINE_TEST_ID', 'OFFLINE_TEST_VALUE', 'OFFLINE_TEST_TOKEN', now + 900), now)

    def read(self, key, limit):
        return self.objects[key]

    def publish(self, key, audio):
        self.objects[key] = audio

    def delete(self, key):
        self.objects.pop(key, None)

    def signed_read(self, key, duration, size, *, refresh=False):
        self.leases.append((key, duration, size, refresh))
        return {'url': f'https://{self.bucket}.oss-{self.region}.aliyuncs.com/{key}?offline=fixture',
                'expiresAt': 9999999999, 'maxBytes': size}


class ReleasePolicyFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / 'music.db'
        self.now = utc('2026-10-13T15:59:59')
        self.storage = OfflineStorage()
        self.service = self.instance()
        self.uid = 10077  # Deliberately not the old isolated UID 10000.
        self.session = str(uuid.uuid4())
        self.data = tone()

    def tearDown(self):
        self.temp.cleanup()

    def instance(self):
        return MusicUploads(self.database, self.storage, clock=lambda: self.now)

    def intent(self, service=None, **changes):
        values = dict(uid=self.uid, session=self.session, request_id=str(uuid.uuid4()),
                      size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest(),
                      license_id='CC0-1.0', credit='Offline generated fixture, CC0', title='Offline policy fixture')
        values.update(changes)
        return (service or self.service).create(**values)

    def fill(self, amount, *, service=None, uid=None, session=None, size=None):
        service = service or self.service
        uid = self.uid if uid is None else uid
        session = self.session if session is None else session
        for _ in range(amount):
            options = dict(uid=uid, session=session)
            if size is not None:
                options['size'] = size
            result = self.intent(service, **options)
            service.cancel(uid, session, result['jobId'])

    def counts(self):
        with self.service.connect() as db:
            return {(row['uid'], row['day']): row['count'] for row in db.execute('SELECT uid,day,count FROM music_daily_intents')}

    def error(self, code, action):
        with self.assertRaises(MusicError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def redeemed_track(self):
        result = self.intent()
        self.storage.objects[result['upload']['fields']['key']] = self.data
        receipt = self.service.complete(self.uid, self.session, result['jobId'])
        self.service.consume(self.uid, self.session, result['jobId'], receipt['receiptId'])
        return receipt

class DailyIntentPolicyTests(ReleasePolicyFixture, unittest.TestCase):
    def test_per_uid_100_new_intents_cancel_does_not_refund_and_other_uid_is_independent(self):
        self.fill(100)
        self.assertEqual(429, self.error('music_daily_upload_limit', self.intent).status)
        other = self.uid + 1
        self.fill(100, uid=other)
        self.error('music_daily_upload_limit', lambda: self.intent(uid=other))
        self.assertEqual({(self.uid, '2026-10-13'): 100, (other, '2026-10-13'): 100}, self.counts())

    def test_counter_persists_across_service_restart(self):
        self.fill(99)
        restarted = self.instance()
        self.fill(1, service=restarted)
        self.error('music_daily_upload_limit', lambda: self.intent(self.instance()))
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])

    def test_shanghai_midnight_resets_new_day_utc_midnight_does_not(self):
        self.fill(100)
        self.error('music_daily_upload_limit', self.intent)
        self.now = utc('2026-10-13T16:00:00')  # Shanghai 2026-10-14 00:00:00.
        self.fill(100, service=self.instance())
        self.error('music_daily_upload_limit', self.intent)
        self.now = utc('2026-10-14T00:00:00')  # Shanghai 08:00, still same day.
        self.error('music_daily_upload_limit', lambda: self.intent(self.instance()))
        self.assertEqual({(self.uid, '2026-10-13'): 100, (self.uid, '2026-10-14'): 100}, self.counts())

    def test_two_instances_compete_for_the_100th_intent_exactly_one_admitted(self):
        self.fill(99)
        instances = [self.instance(), self.instance()]
        start = threading.Barrier(2)

        def attempt(index):
            start.wait(timeout=5)
            try:
                return 'accepted', self.intent(instances[index])['jobId']
            except MusicError as error:
                return error.code, error.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(['accepted', 'music_daily_upload_limit'], sorted(value[0] for value in results))
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])
        with self.service.connect() as db:
            self.assertEqual(100, db.execute('SELECT count(*) FROM music_uploads WHERE uid=?', (self.uid,)).fetchone()[0])

    def test_same_task_retry_at_daily_limit_and_after_restart_does_not_charge_twice(self):
        self.fill(99)
        request = str(uuid.uuid4())
        first = self.intent(request_id=request)
        second = self.intent(self.instance(), request_id=request)
        self.assertEqual(first['jobId'], second['jobId'])
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])
        self.error('music_daily_upload_limit', self.intent)
        self.error('request_id_reused', lambda: self.intent(request_id=request, sha256='0' * 64))
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])

    def test_two_instances_same_task_retry_reserve_only_one_daily_intent(self):
        self.fill(99)
        request = str(uuid.uuid4())
        instances = [self.instance(), self.instance()]
        start = threading.Barrier(2)

        def attempt(index):
            start.wait(timeout=5)
            return self.intent(instances[index], request_id=request)['jobId']

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])

    def test_new_game_session_shares_uid_daily_quota_and_cannot_claim_old_task(self):
        old = self.intent()
        new_session = str(uuid.uuid4())
        for action in (lambda: self.service.complete(self.uid, new_session, old['jobId']),
                       lambda: self.service.cancel(self.uid, new_session, old['jobId']),
                       lambda: self.service.consume(self.uid, new_session, old['jobId'], str(uuid.uuid4()))):
            self.error('job_not_found', action)
        self.service.cancel(self.uid, self.session, old['jobId'])
        self.fill(99, session=new_session)
        self.error('music_daily_upload_limit', lambda: self.intent(session=str(uuid.uuid4())))
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])

    def test_playback_and_refresh_after_limit_do_not_create_or_charge_intents(self):
        receipt = self.redeemed_track()
        self.fill(99)
        before = self.counts()
        with self.service.connect() as db:
            job_count = db.execute('SELECT count(*) FROM music_uploads').fetchone()[0]
        for refresh in (False, True, True, False):
            result = self.service.playback(self.uid + 1, str(uuid.uuid4()), receipt['url'], refresh=refresh)
            self.assertEqual(receipt['url'], result['reference'])
        self.assertEqual(before, self.counts())
        with self.service.connect() as db:
            self.assertEqual(job_count, db.execute('SELECT count(*) FROM music_uploads').fetchone()[0])
        self.assertEqual([False, True, True, False], [lease[3] for lease in self.storage.leases])
        self.error('music_daily_upload_limit', self.intent)

    def test_rejected_validation_and_ticket_failure_do_not_issue_intent(self):
        self.error('invalid_size', lambda: self.intent(size=MAX_BYTES + 1))
        self.error('redistribution_permission_required', lambda: self.intent(license_id='INVALID'))
        with patch.object(self.storage, 'ticket', side_effect=MusicError(503, 'offline_ticket_failure')):
            self.error('offline_ticket_failure', self.intent)
        self.assertEqual({}, self.counts())
        with self.service.connect() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM music_uploads').fetchone()[0])

    def test_production_does_not_apply_old_256mib_cumulative_source_trial_gate(self):
        self.fill(100, size=MAX_BYTES)
        self.assertEqual(100, self.counts()[(self.uid, '2026-10-13')])
        with self.service.connect() as db:
            self.assertEqual(100 * MAX_BYTES, db.execute('SELECT sum(size) FROM music_uploads').fetchone()[0])


class ProductionAndQaBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_production_ignores_old_uid_allowlist_and_300_trial_limits(self):
        ffmpeg = self.root / 'offline-unused-ffmpeg'
        ffmpeg.write_bytes(b'fixture, never executed')
        service = SimpleNamespace(normalizer=None, cumulative_quotas=True)
        installed, budgets = [], []
        runtime = SimpleNamespace(get=lambda: service, configure_storage=installed.append)
        marker = OfflineStorage()

        def offline_configure(runtime, signer, data_directory, **options):
            def storage_factory(_signer, budget):
                budgets.append(budget)
                return marker
            return configure(runtime, signer, data_directory, storage_factory=storage_factory, **options)

        old_values = {'MUSIC_OSS_ENABLE': '1', 'MUSIC_OSS_DEPLOYMENT_APPROVED': 'PRIVATE_SIGNED_V1',
                      'MUSIC_OSS_ALLOWED_UIDS': '10000', 'MUSIC_OSS_REQUEST_LIMIT': '300',
                      'MUSIC_OSS_SOURCE_BYTES_LIMIT': '134217728', 'MUSIC_OSS_MEDIA_BYTES_LIMIT': '134217728',
                      'MUSIC_OSS_READ_BYTES_LIMIT': '536870912', 'MUSIC_FFMPEG_EXECUTABLE': str(ffmpeg)}
        with patch('app.music_production.configure', side_effect=offline_configure):
            result = configure_from_environment(runtime, None, self.root, old_values)
        self.assertIs(result, marker)
        self.assertEqual([marker], installed)
        self.assertFalse(service.cumulative_quotas)
        budget = budgets[0]
        self.assertIsNone(budget.allowed_uids)
        self.assertIsNone(budget.allowed_jobs)
        self.assertEqual(ProductionLimits(None, None, None, None), budget.limits)
        key = f'incoming/10077/{uuid.uuid4()}.wav'
        for _ in range(301):
            budget.reserve('HEAD', key)
        budget.received(536870913)
        restarted = ProductionBudget(self.root / 'music-oss-usage.db', budget.limits)
        restarted.reserve('HEAD', key)
        self.assertEqual(302, restarted.snapshot()['requests'])
        self.assertEqual(536870913, restarted.snapshot()['readBytes'])

    def test_unlimited_production_budget_preserves_per_file_and_object_scope(self):
        budget = ProductionBudget(self.root / 'production.db', ProductionLimits(None, None, None, None))
        job = str(uuid.uuid4())
        incoming, media = f'incoming/10077/{job}.wav', f'media/10077/{job}.mp3'
        budget.reserve('PUT', incoming, MAX_BYTES)
        with self.assertRaises(MusicError):
            budget.reserve('PUT', incoming, MAX_BYTES + 1, reissue_source=True)
        with self.assertRaises(MusicError):
            budget.reserve('DELETE', media)
        with self.assertRaises(MusicError):
            budget.reserve('GET', 'published-static/launcher.jar')

    def test_explicit_qa_55_limit_and_fixed_uid_jobs_survive_production_policy_change(self):
        jobs = [str(uuid.uuid4()), str(uuid.uuid4())]
        database = self.root / 'qa-usage.db'
        limits = ProductionLimits(55, 1048576, 1048576, 2097152)
        qa = ProductionBudget(database, limits, [10000], jobs, initial=(54, 0, 0, 0))
        with self.assertRaises(MusicError):
            qa.reserve('HEAD', f'incoming/10077/{jobs[0]}.wav')
        with self.assertRaises(MusicError):
            qa.reserve('HEAD', f'incoming/10000/{uuid.uuid4()}.wav')
        self.assertEqual(54, qa.snapshot()['requests'])
        qa.reserve('HEAD', f'incoming/10000/{jobs[0]}.wav')
        with self.assertRaises(MusicError):
            qa.reserve('GET', f'media/10000/{jobs[1]}.mp3')
        restarted = ProductionBudget(database, limits, [10000], jobs, initial=(0, 0, 0, 0))
        with self.assertRaises(MusicError):
            restarted.reserve('HEAD', f'incoming/10000/{jobs[0]}.wav')
        self.assertEqual(55, restarted.snapshot()['requests'])


class IntentMiddlewarePolicyTests(ReleasePolicyFixture, unittest.TestCase):
    def make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.music_upload_identity import music_identities
        account = SimpleNamespace(id=77, uid=self.uid)
        settings = SimpleNamespace(terminal_sso_enabled=True, bmc_launcher_client_id='launcher',
                                   terminal_sso_server_key='OFFLINE_TEST_AUTHORITY_32_CHARACTERS',
                                   minecraft_profile_key='OFFLINE_SEPARATE_PROFILE_AUTHORITY')
        store = SimpleNamespace(access_token=lambda token: (account, 'launcher') if token == 'OFFLINE_TEST' else None,
                                account_by_uid=lambda uid: account if uid == self.uid else None)
        binding = SimpleNamespace(active=lambda account_id, session, key:
                                  account_id == account.id and session == self.session and key == settings.terminal_sso_server_key)
        launcher, server = music_identities(lambda: settings, lambda: store, lambda: binding)
        app = FastAPI()
        app.include_router(music_upload_router(lambda: self.service, launcher, server))
        app.add_middleware(MusicIntentGuard)
        self.headers = {'Authorization': 'Bearer OFFLINE_TEST'}
        return TestClient(app)

    def body(self):
        return {'gameSession': self.session, 'burnNonce': str(uuid.uuid4()), 'requestId': str(uuid.uuid4()),
                'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest(),
                'license': 'CC0-1.0', 'credit': 'Offline generated audio, CC0', 'title': 'Offline middleware fixture'}

    def test_100_valid_intents_are_not_mistaken_for_50_malformed_requests(self):
        with self.make_client() as client:
            for _ in range(100):
                response = client.post('/api/launcher/music/uploads', json=self.body(), headers=self.headers)
                self.assertEqual(200, response.status_code, response.text)
                job = response.json()['jobId']
                response = client.post(f'/api/launcher/music/uploads/{job}/cancel',
                                       json={'gameSession': self.session}, headers=self.headers)
                self.assertEqual(200, response.status_code, response.text)
            response = client.post('/api/launcher/music/uploads', json=self.body(), headers=self.headers)
            self.assertEqual(429, response.status_code)
            self.assertEqual('music_daily_upload_limit', response.json()['error'])

    def test_50_malformed_limit_cannot_block_playback_refresh_or_complete_retry(self):
        receipt = self.redeemed_track()
        before = self.counts()
        with self.make_client() as client:
            for _ in range(50):
                self.assertEqual(422, client.post('/api/launcher/music/uploads', json={}, headers=self.headers).status_code)
            blocked = client.post('/api/launcher/music/uploads', json=self.body(), headers=self.headers)
            self.assertEqual(429, blocked.status_code)
            self.assertEqual('music_intent_rate_limit', blocked.json()['error'])
            self.assertEqual('60', blocked.headers['retry-after'])
            self.assertEqual('no-store', blocked.headers['cache-control'])
            for refresh in (False, True):
                response = client.post('/api/launcher/music/playback',
                                       json={'gameSession': self.session, 'reference': receipt['url'], 'refresh': refresh},
                                       headers=self.headers)
                self.assertEqual(200, response.status_code, response.text)
            response = client.post(f'/api/launcher/music/uploads/{receipt["jobId"]}/complete',
                                   json={'gameSession': self.session}, headers=self.headers)
            self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(before, self.counts())
        self.assertEqual([False, True], [lease[3] for lease in self.storage.leases])


if __name__ == '__main__':
    unittest.main(verbosity=2)
