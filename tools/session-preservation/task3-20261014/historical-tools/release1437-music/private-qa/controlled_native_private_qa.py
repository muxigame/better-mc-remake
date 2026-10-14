"""Prepared QA-only service for release's single startup owner; never auto-runs.
No listener/proxy/task registration, policy changes, new IAM identity or keys.
Original backend authority must be supplied in-process; nothing is exported.
Never mount this as afternoon production: persistent scope is one approved job.
"""
from pathlib import Path
import re,tempfile
from .music_upload import MusicUploads,MusicError
from .music_audio import AudioNormalizer
from .music_receipt import BurnReceipts
from .music_production_storage import ProductionStorage,ProductionBudget,ProductionLimits
from .music_upload_identity import music_identities,music_live_identity
from .music_upload import music_upload_router

UID=10000
JOB='ea8bbe98-43f0-462c-bd51-fec644838448'
PREVIOUS=(45,313632,97888,411520)

class ScopedUploads(MusicUploads):
    def enabled(self):
        if not getattr(self,'qa_owner',None) or not self.qa_owner.preflight_passed:raise MusicError(503,'music_qa_preflight_required')
        return super().enabled()
    def create(self,uid,session,request_id,size,digest,license_id,credit,title,burn_nonce=''):
        owner=self.qa_owner
        if not owner.preflight_passed:raise MusicError(503,'music_qa_preflight_required')
        if uid!=UID:raise MusicError(401,'music_qa_verified_uid_mismatch')
        if (size,digest)!=(owner.expected_bytes,owner.expected_sha256):raise MusicError(400,'music_qa_audio_scope_refused')
        return super().create(uid,session,request_id,size,digest,license_id,credit,title,burn_nonce)
    def playback(self,caller_uid,session,reference,refresh=False):
        if caller_uid!=UID:raise MusicError(401,'music_qa_verified_uid_mismatch')
        return super().playback(caller_uid,session,reference,refresh)

class ControlledNativeQa:
    def __init__(self,baseline,data_directory,expected_bytes,expected_sha256,ffmpeg,*,storage_factory=ProductionStorage):
        if baseline.store.account_by_uid(UID) is None:raise ValueError('approved_uid_is_not_a_real_account')
        if type(expected_bytes) is not int or not 1<=expected_bytes<=2097152 or not isinstance(expected_sha256,str) or not re.fullmatch('[0-9a-f]{64}',expected_sha256):raise ValueError('exact_qa_audio_required')
        if not Path(ffmpeg).is_file():raise ValueError('reviewed_qa_ffmpeg_required')
        self.directory=Path(data_directory).resolve()
        if self.directory.parent!=Path(tempfile.gettempdir()).resolve() or not self.directory.name.startswith('muxi-music-private-native-20261013-'):raise ValueError('isolated_owned_qa_directory_required')
        self.directory.mkdir(parents=True,exist_ok=True)
        self.expected_bytes,self.expected_sha256=expected_bytes,expected_sha256
        self.marker=self.directory/'private-native-20261013-consumed';self.preflight_passed=False
        # At most10 conservative native admissions after45, leaving at least5
        # global slots for the separately owned exact cleanup/stop contingency.
        self.budget=ProductionBudget(self.directory/'usage.db',ProductionLimits(55,1048576,1048576,2097152),[UID],[JOB],PREVIOUS)
        self.storage=storage_factory(baseline.signer,self.budget)
        self.service=ScopedUploads(self.directory/'uploads.db',self.storage,
            normalizer=AudioNormalizer(str(ffmpeg)),receipts=BurnReceipts(baseline.signer,baseline.settings.issuer),job_factory=self.allocate)
        self.service.qa_owner=self
        launcher,server=music_identities(lambda:baseline.settings,lambda:baseline.store,lambda:baseline.terminal_access)
        self.router=music_upload_router(lambda:self.service,launcher,server,
            music_live_identity(lambda:baseline.settings,lambda:baseline.store,lambda:baseline.terminal_access))
    def allocate(self):
        with self.service.connect() as db:
            if db.execute('SELECT 1 FROM music_uploads LIMIT 1').fetchone():raise ValueError('single_approved_job_already_allocated')
        return JOB
    def preflight(self):
        # Never clear the previous legacy consumed marker or this new marker.
        with self.marker.open('x',encoding='utf-8') as marker:marker.write('Release single-owner private QA 20261013\n')
        incoming=f'incoming/{UID}/{JOB}.wav';media=f'media/{UID}/{JOB}.mp3'
        _,sdk=self.storage.pair(incoming)
        for key in [incoming,media]:
            try:sdk.head_object(key)
            except Exception as failed:
                if getattr(failed,'status',None)==404:continue
                raise MusicError(503,'music_qa_absence_preflight_failed') from None
            raise MusicError(409,'music_qa_object_already_exists')
        self.preflight_passed=True
        return {'preflightAbsent':True,'uid':UID,'jobId':JOB,'budget':self.budget.snapshot()}
    def stop(self):
        self.preflight_passed=False;self.storage.stop()

# Release caller owns mounting into its existing QA ingress, native startup,
# normal shutdown and separately counted exact media cleanup. This module does
# not switch production config/DB, instantiate app.main or read a .env/key.
