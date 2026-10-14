"""Music-only bounded OSS SDK transport, enabled only by explicit QA owner."""
import json
import logging
import time
from threading import RLock
from urllib.parse import urlsplit, unquote

from .music_upload import MusicError, ManagedOssStorage
from .music_workload_identity import BUCKET, qa_session_policy

HOST = BUCKET + '.oss-cn-hangzhou.aliyuncs.com'
MIB = 1024 * 1024


class QaOssBudget:
    def __init__(self, verified_uid, jobs):
        scope = json.loads(qa_session_policy(verified_uid, jobs))
        self.incoming = frozenset(r.split(BUCKET + '/', 1)[1] for r in scope['Statement'][0]['Resource'])
        self.media = frozenset(r.split(BUCKET + '/', 1)[1] for r in scope['Statement'][1]['Resource'])
        self.lock = RLock()
        self.calls = self.incoming_bytes = self.media_bytes = self.read_bytes = 0
        self.put_keys = set()
        self.closed = False

    def reserve(self, method, key, size=0):
        with self.lock:
            allowed = {'HEAD','GET','PUT','DELETE'} if key in self.incoming else {'HEAD','GET','PUT'} if key in self.media else set()
            if self.closed or method not in allowed or self.calls >= 60:
                raise MusicError(503, 'music_qa_scope_or_request_limit')
            if method == 'PUT':
                used = self.incoming_bytes if key in self.incoming else self.media_bytes
                if type(size) is not int or not 1 <= size <= MIB or used + size > MIB or key in self.put_keys:
                    raise MusicError(503, 'music_qa_upload_limit')
                self.put_keys.add(key)
                if key in self.incoming: self.incoming_bytes += size
                else: self.media_bytes += size
            self.calls += 1

    def received(self, count):
        with self.lock:
            if self.closed or self.read_bytes + count > 2*MIB:
                raise MusicError(503, 'music_qa_read_limit')
            self.read_bytes += count

    def stop(self):
        with self.lock: self.closed = True

    def snapshot(self):
        with self.lock:
            return {'requests':self.calls, 'incomingBytesAuthorized':self.incoming_bytes,
                    'mediaBytesAuthorized':self.media_bytes,'responseBytesRead':self.read_bytes,
                    'objectKeysAuthorized':len(self.put_keys),'closed':self.closed}


class _BoundResponse:
    def __init__(self, response, budget, start, monotonic, limit=MIB, read_deadline_seconds=15, read_chunk_bytes=4096):
        self.response, self.budget = response, budget
        self.status, self.headers = response.status_code, response.headers
        self.request_id = self.headers.get('x-oss-request-id','')
        self.start, self.monotonic = start, monotonic
        self.iterator = response.iter_content(read_chunk_bytes)
        self.pending = bytearray()
        self.eof = False
        self.limit=limit
        self.read_deadline_seconds=read_deadline_seconds

    def _chunk(self):
        if self.monotonic() - self.start > self.read_deadline_seconds:
            self.close(); raise MusicError(503, 'music_oss_read_deadline')
        try: chunk = next(self.iterator)
        except StopIteration:
            self.eof = True; return b''
        self.budget.received(len(chunk))
        return chunk

    def read(self, amount=None):
        # Error XML and media responses remain bounded even when SDK requests
        # an unbounded read. QA uses only two <=1MiB published/source payloads.
        target = self.limit+1 if amount is None else min(amount, self.limit+1)
        while len(self.pending) < target and not self.eof:
            self.pending.extend(self._chunk())
        take = min(target,len(self.pending))
        result = bytes(self.pending[:take]);del self.pending[:take]
        if amount is None and (len(result)>self.limit or self.pending or not self.eof):
            self.close();raise MusicError(503, 'music_oss_response_limit')
        return result

    def __iter__(self):
        while not self.eof:
            data=self.read(4096)
            if data: yield data

    def close(self): self.response.close()


class BoundedOssSession:
    def __init__(self, budget, http=None, monotonic=time.monotonic, max_body_bytes=MIB, read_deadline_seconds=15, read_chunk_bytes=4096):
        import requests
        self.budget,self.monotonic = budget,monotonic
        self.http = http if http is not None else requests.Session()
        self.http.trust_env = False
        self.http.cookies.clear()
        adapter = requests.adapters.HTTPAdapter(max_retries=0,pool_connections=2,pool_maxsize=2)
        self.http.mount('https://',adapter)
        self.lock = RLock()
        self.receipts = []
        if type(max_body_bytes) is not int or not 1<=max_body_bytes<=32*MIB:raise ValueError('invalid_music_body_limit')
        self.max_body_bytes=max_body_bytes
        if type(read_deadline_seconds) is not int or not 1<=read_deadline_seconds<=60:raise ValueError('invalid_music_read_deadline')
        self.read_deadline_seconds=read_deadline_seconds
        if type(read_chunk_bytes) is not int or read_chunk_bytes not in (4096,65536):raise ValueError('invalid_music_read_chunk')
        self.read_chunk_bytes=read_chunk_bytes

    def do_request(self, req, timeout):
        url=urlsplit(req.url)
        key=unquote(url.path.lstrip('/'))
        if (url.scheme!='https' or url.netloc!=HOST or url.username or url.password
                or url.query or url.fragment or req.params or req.proxies):
            raise MusicError(503,'music_oss_origin_refused')
        if any(k.lower()=='cookie' for k in req.headers):
            raise MusicError(503,'music_oss_cookie_refused')
        size=0
        payload=req.data
        if req.method=='PUT':
            if not isinstance(payload,(bytes,bytearray)):
                # Official oss2 wraps immutable bytes to calculate CRC64. Read
                # that exact adapter once, preserving its CRC check; refuse
                # file handles, streams and arbitrary duck-typed adapters.
                from oss2.utils import _BytesAndFileAdapter
                if (type(payload) is not _BytesAndFileAdapter or not isinstance(payload.data,bytes)
                        or payload.offset!=0 or payload.size!=len(payload.data)
                        or not 1<=payload.size<=self.max_body_bytes):
                    raise MusicError(503,'music_oss_payload_refused')
                payload=payload.read(self.max_body_bytes+1)
            size=len(payload)
            if (req.headers.get('x-oss-forbid-overwrite')!='true'
                    or req.headers.get('x-oss-object-acl')!='private'):
                raise MusicError(503,'music_oss_write_guard')
        self.budget.reserve(req.method,key,size)
        start=self.monotonic()
        try:
            with self.lock:
                self.http.cookies.clear()
                response=self.http.request(req.method,req.url,data=payload,params={},
                    headers=req.headers,stream=True,timeout=(5,10),allow_redirects=False,
                    verify=True,proxies={})
                self.http.cookies.clear()
            if 300 <= response.status_code < 400:
                response.close();raise MusicError(503,'music_oss_redirect_refused')
            if response.headers.get('Content-Encoding','identity').lower()!='identity':
                response.close();raise MusicError(503,'music_oss_encoding_refused')
            self.receipts.append({'method':req.method,'key':key,'status':response.status_code,
                                  'requestId':response.headers.get('x-oss-request-id','')})
            return _BoundResponse(response,self.budget,start,self.monotonic,self.max_body_bytes,self.read_deadline_seconds,self.read_chunk_bytes)
        except MusicError: raise
        except Exception:
            raise MusicError(503,'music_oss_transport_unavailable') from None

    def stop(self):
        self.budget.stop()
        with self.lock: self.http.close()


class QaOssStorage(ManagedOssStorage):
    def __init__(self, sdk, identity, session, budget):
        super().__init__(sdk,'cn-hangzhou',identity.credentials)
        self.identity,self.session,self.budget=identity,session,budget

    def ticket(self,key,size,now):
        # Count direct POST admission conservatively; QA caller sends once
        # without automatic retries. This prevents a 32MiB normal-service
        # ticket from bypassing the stricter 1MiB cumulative QA source limit.
        self.budget.reserve('PUT',key,size)
        return super().ticket(key,size,now)

    def stop(self):
        self.session.stop()
        self.identity.stop()


def create_qa_storage(identity, verified_uid, jobs, http=None):
    """Called in a trusted backend process; no .env or signing-key file reads."""
    import oss2
    # SDK DEBUG logging includes signed headers/security tokens. Music uses
    # explicit safe request receipts instead, regardless of parent log level.
    for name in list(logging.Logger.manager.loggerDict)+['oss2']:
        if name=='oss2' or name.startswith('oss2.'):
            logging.getLogger(name).disabled=True
    class Provider(oss2.CredentialsProvider):
        def get_credentials(self):
            value=identity.credentials()
            return oss2.credentials.Credentials(value.access_id,value.secret,value.security_token)
    budget=QaOssBudget(verified_uid,jobs)
    session=BoundedOssSession(budget,http=http)
    sdk=oss2.Bucket(oss2.ProviderAuthV4(Provider()),'https://oss-cn-hangzhou.aliyuncs.com',
                    BUCKET,session=session,connect_timeout=5,region='cn-hangzhou')
    storage=QaOssStorage(sdk,identity,session,budget)
    return storage,session,budget
