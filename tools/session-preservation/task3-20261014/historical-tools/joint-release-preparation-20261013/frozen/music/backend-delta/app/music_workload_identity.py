"""Explicit internal-only OIDC workload provider. No config/secret reads or router.

The deployment owner injects the existing backend signer. Importing this module
does not enable uploads, mint a token, or contact STS. QA sessions are limited to
two preallocated jobs; no permanent cloud credential fallback exists.
"""
import json
import time
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from threading import RLock

from .music_upload import Credentials, MusicError

ISSUER = 'https://account.muxigame.com'
AUDIENCE = 'muxi-music-oss-backend'
SUBJECT = 'service:muxi-auth:music-oss'
ROLE = 'acs:ram::1551167834904293:role/muximusicossbackend'
PROVIDER = 'acs:ram::1551167834904293:oidc-provider/MuxiMusicWorkloadOidc'
BUCKET = 'muxigame-music-cn-hangzhou'
STS_ENDPOINT = 'https://sts.cn-hangzhou.aliyuncs.com/'
SESSION = 'muxi-music-qa-backend'


def qa_session_policy(uid, jobs):
    """UID comes from verified game binding; two UUIDs allocated by QA owner."""
    if (type(uid) is not int or uid <= 0 or not isinstance(jobs, (list, tuple))
            or len(jobs) != 2 or not all(isinstance(job, str) for job in jobs)
            or len(set(jobs)) != 2):
        raise ValueError('invalid_music_qa_scope')
    try:
        if any(str(uuid.UUID(job)) != job or uuid.UUID(job).version != 4 for job in jobs):
            raise ValueError()
    except (ValueError, AttributeError, TypeError):
        raise ValueError('invalid_music_qa_scope') from None
    prefix = f'acs:oss:*:*:{BUCKET}/'
    policy = {'Version': '1', 'Statement': [
        {'Effect': 'Allow', 'Action': ['oss:PutObject', 'oss:GetObject', 'oss:DeleteObject'],
         'Resource': [f'{prefix}incoming/{uid}/{job}.wav' for job in jobs]},
        {'Effect': 'Allow', 'Action': ['oss:PutObject', 'oss:GetObject'],
         'Resource': [f'{prefix}media/{uid}/{job}.mp3' for job in jobs]},
    ]}
    # Reviewed QA uses FFmpeg MP3 output; WAV requires an explicit revised key
    # allocation, never a wildcard extension or silent extra object.
    encoded = json.dumps(policy, separators=(',', ':'))
    if len(encoded.encode()) > 2048:
        raise ValueError('invalid_music_qa_scope')
    return encoded


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class BoundedStsExchange:
    """Unsigned OIDC STS POST, fixed host, no cookies, redirects or URL tokens."""
    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.opener = urllib.request.build_opener(_NoRedirect())

    def __call__(self, fields):
        start = self.monotonic()
        body = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(STS_ENDPOINT, data=body, method='POST',
                                         headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with self.opener.open(request, timeout=5) as response:
                if response.status != 200 or response.geturl() != STS_ENDPOINT:
                    raise ValueError()
                data = bytearray()
                while True:
                    if self.monotonic() - start > 10:
                        raise TimeoutError()
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 32768:
                        raise ValueError()
                return json.loads(data.decode('utf-8'))
        except Exception:
            # HTTP error bodies may contain sensitive parameters. Never expose
            # the response, exception chain, form or temporary credentials.
            raise MusicError(503, 'music_sts_exchange_unavailable') from None


class MusicWorkloadIdentity:
    def __init__(self, signer, verified_uid, qa_jobs, exchange=None, clock=time.time,
                 *, policy_builder=qa_session_policy, session_name=SESSION, minimum_validity=120):
        self.signer, self.clock = signer, clock
        if session_name not in {SESSION,'muxi-music-production-backend'} or type(minimum_validity) is not int or not 120<=minimum_validity<=780:
            raise ValueError('invalid_internal_workload_configuration')
        self.session_name,self.minimum_validity=session_name,minimum_validity
        self.policy = policy_builder(verified_uid, qa_jobs)
        self.exchange = exchange if exchange is not None else BoundedStsExchange()
        self.lock = RLock()
        self.cached = None
        self.stopped = False
        self.retry_after = 0

    def credentials(self):
        with self.lock:
            now = self.clock()
            if self.stopped or now < self.retry_after:
                raise MusicError(503, 'music_workload_identity_unavailable')
            if self.cached and self.cached.expires_at > now + self.minimum_validity:
                return self.cached
            try:
                # Constants are not caller claims; no player token is accepted.
                token = self.signer.id_token({'iss': ISSUER, 'aud': AUDIENCE,
                    'sub': SUBJECT, 'iat': int(now), 'exp': int(now) + 60,
                    'jti': str(uuid.uuid4())})
                if not isinstance(token, str) or not 1 <= len(token) <= 20000:
                    raise ValueError()
                result = self.exchange({'Action': 'AssumeRoleWithOIDC', 'Version': '2015-04-01',
                    'Format': 'JSON', 'RoleArn': ROLE, 'OIDCProviderArn': PROVIDER,
                    'Timestamp': datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'SignatureNonce': str(uuid.uuid4()),
                    'RoleSessionName': self.session_name, 'DurationSeconds': '900',
                    'OIDCToken': token, 'Policy': self.policy})
                c = result['Credentials']
                expiration = datetime.fromisoformat(c['Expiration'].replace('Z', '+00:00'))
                if expiration.tzinfo is None:
                    raise ValueError()
                expires = expiration.timestamp()
                current = self.clock()
                arn = result['AssumedRoleUser']['Arn']
                permitted = (ROLE + '/' + self.session_name,
                    ROLE.replace(':role/', ':assumed-role/') + '/' + self.session_name)
                if arn not in permitted or not current + self.minimum_validity < expires <= now + 930:
                    raise ValueError()
                if not isinstance(c['AccessKeyId'], str) or not c['AccessKeyId'].startswith('STS.'):
                    raise ValueError()
                if not isinstance(c['AccessKeySecret'], str) or not c['AccessKeySecret']:
                    raise ValueError()
                if not isinstance(c['SecurityToken'], str) or not c['SecurityToken']:
                    raise ValueError()
                self.cached = Credentials(c['AccessKeyId'], c['AccessKeySecret'], c['SecurityToken'], expires)
                self.retry_after = 0
                return self.cached
            except Exception:
                self.cached = None
                self.retry_after = self.clock() + 2
                raise MusicError(503, 'music_workload_identity_unavailable') from None

    def invalidate_cached_credentials(self):
        with self.lock:self.cached=None # retain failure cooldown and fixed claims

    def stop(self):
        # Stops local mint/refresh and drops cached references. This does not
        # revoke credentials already issued by STS; cloud-side revocation is
        # an administrator operation followed by an actual denial check.
        with self.lock:
            self.stopped = True
            self.cached = None
