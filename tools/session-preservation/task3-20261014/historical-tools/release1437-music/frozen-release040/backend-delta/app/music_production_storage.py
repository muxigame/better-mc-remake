"""Explicit production provider: fixed bucket/role, per-job STS, persisted caps.

Constructor is internal only. No .env, root key, cloud writes at import/start,
HTTP configuration setter, IAM changes or anonymous policy modification.
"""
from pathlib import Path
import json,re,sqlite3,time,uuid,logging
from threading import RLock
from dataclasses import dataclass
from contextlib import closing
from .music_upload import MusicError,ManagedOssStorage,post_policy,MAX_BYTES
from .music_workload_identity import MusicWorkloadIdentity,BUCKET
from .music_oss_transport import BoundedOssSession

def parse_key(key):
    match=re.fullmatch(r'(incoming|media)/([1-9][0-9]{4,15})/([0-9a-f-]{36})\.(wav|mp3)',key or '')
    if not match:raise MusicError(503,'music_object_scope_refused')
    prefix,uid,job,extension=match.groups();parsed=uuid.UUID(job)
    if parsed.version!=4 or str(parsed)!=job or prefix=='incoming' and extension!='wav' or prefix=='media' and extension!='mp3':raise MusicError(503,'music_object_scope_refused')
    return prefix,int(uid),job,extension

def production_session_policy(uid,jobs):
    if type(uid) is not int or not 10000<=uid<=9999999999999999 or not isinstance(jobs,(list,tuple)) or len(jobs)!=1:raise ValueError('invalid_music_production_scope')
    job=jobs[0];parse_key(f'incoming/{uid}/{job}.wav')
    prefix=f'acs:oss:*:*:{BUCKET}/'
    return json.dumps({'Version':'1','Statement':[
        {'Effect':'Allow','Action':['oss:PutObject','oss:GetObject','oss:DeleteObject'],'Resource':[prefix+f'incoming/{uid}/{job}.wav']},
        {'Effect':'Allow','Action':['oss:PutObject','oss:GetObject'],'Resource':[prefix+f'media/{uid}/{job}.mp3']}]},separators=(',',':'))

@dataclass(frozen=True)
class ProductionLimits:
    requests:int|None
    source_bytes:int|None
    media_bytes:int|None
    read_bytes:int|None
    def __post_init__(self):
        if any(x is not None and (type(x) is not int or x<1) for x in (self.requests,self.source_bytes,self.media_bytes,self.read_bytes)):raise ValueError('explicit_music_limits_required')

class ProductionBudget:
    def __init__(self,database,limits,allowed_uids=None,allowed_jobs=None,initial=(0,0,0,0)):
        self.path=Path(database);self.path.parent.mkdir(parents=True,exist_ok=True);self.limits=limits
        self.allowed_uids=frozenset(allowed_uids) if allowed_uids is not None else None
        self.allowed_jobs=frozenset(allowed_jobs) if allowed_jobs is not None else None
        self.closed=False;self.lock=RLock()
        if len(initial)!=4 or any(type(x) is not int or x<0 for x in initial):raise ValueError('invalid_initial_music_usage')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS music_oss_usage(id INTEGER PRIMARY KEY CHECK(id=1),calls INTEGER NOT NULL,source INTEGER NOT NULL,media INTEGER NOT NULL,reads INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS music_oss_put_keys(key TEXT PRIMARY KEY)')
            db.execute('INSERT OR IGNORE INTO music_oss_usage VALUES(1,?,?,?,?)',initial)
    def reserve(self,method,key,size=0,*,reissue_source=False):
        prefix,uid,job,_=parse_key(key)
        if self.allowed_uids is not None and uid not in self.allowed_uids or self.allowed_jobs is not None and job not in self.allowed_jobs:raise MusicError(503,'music_object_scope_refused')
        if method not in ({'HEAD','GET','PUT','DELETE'} if prefix=='incoming' else {'HEAD','GET','PUT'}):raise MusicError(503,'music_object_scope_refused')
        with self.lock,closing(sqlite3.connect(self.path,timeout=15)) as db,db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT calls,source,media,reads FROM music_oss_usage WHERE id=1').fetchone()
            if self.closed or self.limits.requests is not None and row[0]>=self.limits.requests:raise MusicError(503,'music_operation_limit')
            if method=='PUT':
                index=1 if prefix=='incoming' else 2;maximum=self.limits.source_bytes if index==1 else self.limits.media_bytes
                exists=db.execute('SELECT 1 FROM music_oss_put_keys WHERE key=?',(key,)).fetchone()
                retry=bool(exists and reissue_source and prefix=='incoming')
                if type(size) is not int or not 1<=size<=MAX_BYTES or not retry and (maximum is not None and row[index]+size>maximum or exists):raise MusicError(503,'music_payload_limit')
                if not retry:
                    db.execute('INSERT INTO music_oss_put_keys VALUES(?)',(key,))
                    db.execute('UPDATE music_oss_usage SET '+('source' if index==1 else 'media')+'='+('source' if index==1 else 'media')+'+? WHERE id=1',(size,))
            db.execute('UPDATE music_oss_usage SET calls=calls+1 WHERE id=1')
    def received(self,count):
        with self.lock,closing(sqlite3.connect(self.path,timeout=15)) as db,db:
            db.execute('BEGIN IMMEDIATE');used=db.execute('SELECT reads FROM music_oss_usage WHERE id=1').fetchone()[0]
            if self.closed or type(count) is not int or count<0 or self.limits.read_bytes is not None and used+count>self.limits.read_bytes:raise MusicError(503,'music_read_limit')
            db.execute('UPDATE music_oss_usage SET reads=reads+? WHERE id=1',(count,))
    def signed_read(self,key,size):
        self.reserve('GET',key);self.received(size) # conservatively admit one complete client GET
    def stop(self):self.closed=True
    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:row=db.execute('SELECT calls,source,media,reads FROM music_oss_usage WHERE id=1').fetchone()
        return dict(zip(['requests','sourceBytes','mediaBytes','readBytes'],row))

class ProductionStorage:
    bucket,region=BUCKET,'cn-hangzhou'
    def __init__(self,signer,budget,*,clock=time.time,identity_factory=None,bucket_factory=None,http=None):
        self.clock,self.budget=clock,budget;self.lock=RLock();self.identities={};self.buckets={}
        self.session=BoundedOssSession(budget,http=http,max_body_bytes=MAX_BYTES)
        self.identity_factory=identity_factory or (lambda uid,job:MusicWorkloadIdentity(signer,uid,[job],clock=clock,
            policy_builder=production_session_policy,session_name='muxi-music-production-backend',minimum_validity=750))
        self.bucket_factory=bucket_factory or self._bucket
        if bucket_factory is None:
            import oss2 # import loggers before disabling SDK debug output
        for name in list(logging.Logger.manager.loggerDict)+['oss2']:
            if name=='oss2' or name.startswith('oss2.'):logging.getLogger(name).disabled=True
    def _bucket(self,identity):
        import oss2
        class Provider(oss2.CredentialsProvider):
            def get_credentials(self):
                c=identity.credentials();return oss2.credentials.Credentials(c.access_id,c.secret,c.security_token)
        return oss2.Bucket(oss2.ProviderAuthV4(Provider()),'https://oss-cn-hangzhou.aliyuncs.com',BUCKET,session=self.session,connect_timeout=5,region='cn-hangzhou')
    def pair(self,key):
        _,uid,job,_=parse_key(key)
        if self.budget.allowed_uids is not None and uid not in self.budget.allowed_uids or self.budget.allowed_jobs is not None and job not in self.budget.allowed_jobs:raise MusicError(503,'music_object_scope_refused')
        with self.lock:
            tag=(uid,job)
            if tag not in self.identities:
                if len(self.identities)>=128:
                    old=next(iter(self.identities));self.identities.pop(old).stop();self.buckets.pop(old,None)
                identity=self.identity_factory(uid,job);self.identities[tag]=identity;self.buckets[tag]=self.bucket_factory(identity)
            return self.identities[tag],self.buckets[tag]
    def ticket(self,key,size,now):
        self.budget.reserve('PUT',key,size,reissue_source=self.budget.limits.requests is None);identity,_=self.pair(key)
        return post_policy(BUCKET,self.region,key,size,identity.credentials(),now)
    def read(self,key,max_bytes):
        identity,sdk=self.pair(key);return ManagedOssStorage(sdk,self.region,identity.credentials).read(key,max_bytes)
    def publish(self,key,audio):
        identity,sdk=self.pair(key);return ManagedOssStorage(sdk,self.region,identity.credentials).publish(key,audio)
    def delete(self,key):
        if parse_key(key)[0]!='incoming':raise MusicError(503,'published_media_delete_disabled')
        _,sdk=self.pair(key);sdk.delete_object(key)
    def signed_read(self,key,duration,size,*,refresh=False):
        if parse_key(key)[0]!='media' or type(duration) is not int or not 1<=duration<=600 or type(size) is not int or not 1<=size<=MAX_BYTES:raise MusicError(503,'invalid_verified_media')
        identity,sdk=self.pair(key);ttl=duration+120
        if type(refresh) is not bool:raise MusicError(400,'invalid_music_refresh')
        self.budget.signed_read(key,size)
        if refresh:identity.invalidate_cached_credentials()
        credential=identity.credentials()
        if credential.expires_at<=self.clock()+ttl+20:raise MusicError(503,'music_identity_too_short')
        url=sdk.sign_url('GET',key,ttl,slash_safe=True)
        return {'url':url,'expiresAt':int(self.clock()+ttl),'maxBytes':size}
    def stop(self):
        for identity in self.identities.values():identity.stop()
        self.session.stop()
