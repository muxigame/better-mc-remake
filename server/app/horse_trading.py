"""Horse commerce uses the existing website balance, never game-result debits.

The Minecraft service supplies verified identities and custody proofs. Quotes, stock,
cashflow and immutable receipts commit together under SQLite BEGIN IMMEDIATE.
New trading is disabled by default; recovery of existing orders remains available.
"""
from pathlib import Path
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
import json
import math
import os
import re
import time
from .player_platform import PlatformStore
from .game_identity import offline_uuid

POLICY = json.loads(Path(__file__).with_name('horse_trade_catalog_v1.json').read_text(encoding='utf-8'))
CATALOG = {v['sku']: v for v in POLICY['catalog']}
if len(CATALOG) != 5 or CATALOG['equinox']['rent'] <= POLICY['maxRacePlayerReward']:
    raise RuntimeError('Invalid horse commerce policy')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def uuid_text(value):
    if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).version not in (3, 4, 5):
        raise ValueError('Canonical UUID required')
    return value


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError('Invalid commerce fields')


class HorseTradeStore:
    def __init__(self, path, clock=time.time, enabled=None):
        self.platform = PlatformStore(path)
        self.clock = clock
        self.enabled = enabled or (lambda: os.getenv('BMC_HORSE_TRADING_ENABLED') == '1')
        with self.platform.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS horse_trade_assets (
                asset TEXT PRIMARY KEY, realm TEXT NOT NULL, owner_uid INTEGER,
                body TEXT NOT NULL, state TEXT NOT NULL,
                origin TEXT NOT NULL, origin_operation TEXT NOT NULL,
                custody_hash TEXT, updated_at INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS horse_asset_owner ON horse_trade_assets(realm,owner_uid,state);
              CREATE TABLE IF NOT EXISTS horse_trade_quotes (
                quote TEXT PRIMARY KEY, uid INTEGER NOT NULL, realm TEXT NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL, amount INTEGER NOT NULL,
                expires INTEGER NOT NULL, consumed_operation TEXT);
              CREATE TABLE IF NOT EXISTS horse_trade_ledger (
                operation TEXT PRIMARY KEY, uid INTEGER NOT NULL, realm TEXT NOT NULL,
                action TEXT NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL,
                delta INTEGER NOT NULL, created_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS horse_trade_sales (
                sale TEXT PRIMARY KEY, uid INTEGER NOT NULL, realm TEXT NOT NULL,
                asset TEXT NOT NULL, amount INTEGER NOT NULL, snapshot TEXT NOT NULL,
                state TEXT NOT NULL, previous_state TEXT NOT NULL, previous_body TEXT,
                quote TEXT NOT NULL, created_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS horse_trade_rentals (
                rental TEXT PRIMARY KEY, uid INTEGER NOT NULL, realm TEXT NOT NULL,
                asset TEXT NOT NULL, race TEXT NOT NULL, amount INTEGER NOT NULL,
                state TEXT NOT NULL, expires INTEGER NOT NULL,
                end_reason TEXT, refunded INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL);
              CREATE UNIQUE INDEX IF NOT EXISTS horse_one_rental_per_member
                ON horse_trade_rentals(realm,uid,race)
                WHERE state IN ('RESERVED','LAUNCHING','RACING');
            ''')

    @staticmethod
    def source(body, uid):
        fields(body, ('asset','name','speed','jump','health','variant','markings','saddled','armored','owner','fingerprint'))
        uuid_text(body['asset'])
        if body['owner'] != offline_uuid(uid):
            raise PermissionError('Horse must belong to the verified Minecraft owner')
        if not isinstance(body['name'], str) or not 1 <= len(body['name']) <= 64 or any(ord(v) < 32 for v in body['name']):
            raise ValueError('Invalid horse name')
        for name, low, high in (('speed', .05, .6), ('jump', .2, 1.5), ('health', 7, 100)):
            value = body[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Invalid horse attribute')
        if type(body['variant']) is not int or not 0 <= body['variant'] <= 6 or type(body['markings']) is not int or not 0 <= body['markings'] <= 4:
            raise ValueError('Invalid horse appearance')
        if type(body['saddled']) is not bool or type(body['armored']) is not bool or not isinstance(body['fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', body['fingerprint']):
            raise ValueError('Invalid custody descriptor')
        tier = 1 + sum(body['speed'] >= cutoff for cutoff in (.19, .23, .27, .315))
        template = list(CATALOG.values())[tier-1]
        quality = min(2.0, max(.5, .65*body['speed']/template['speed'] + .25*body['jump']/template['jump'] + .1*body['health']/template['health']))
        purchase = math.ceil(template['purchase']*quality/10)*10
        rent = max(38 if tier == 5 else 1, math.ceil(template['rent']*quality))
        traits = ['疾驰' if tier == 5 else '迅捷' if tier >= 3 else '稳步', '跃栏' if body['jump'] >= .9 else '稳栏', '健壮' if body['health'] >= 28 else '轻巧']
        return {**body, 'tier':tier, 'traits':traits, 'purchase':purchase, 'rent':rent, 'sku':None, 'policy':POLICY['version']}

    @staticmethod
    def house(realm, sku):
        return str(uuid5(NAMESPACE_URL, 'muxi-horse-house-v1:'+realm+':'+sku))

    def _asset(self, db, realm, asset):
        uuid_text(asset)
        row = db.execute('SELECT * FROM horse_trade_assets WHERE asset=?', (asset,)).fetchone()
        if row:
            if row['realm'] != realm:
                raise PermissionError('Asset belongs to a different Minecraft world')
            return dict(row)
        for sku, template in CATALOG.items():
            if asset == self.house(realm, sku):
                return {'asset':asset,'realm':realm,'owner_uid':None,'body':canonical({**template,'asset':asset,'saddled':True,'armored':False,'policy':POLICY['version']}),'state':'POOL','origin':'HOUSE','origin_operation':'house','custody_hash':None}
        raise LookupError('Unknown horse stock')

    def _put(self, db, row):
        db.execute('INSERT INTO horse_trade_assets VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(asset) DO UPDATE SET owner_uid=excluded.owner_uid,body=excluded.body,state=excluded.state,custody_hash=excluded.custody_hash,updated_at=excluded.updated_at',
                   (row['asset'],row['realm'],row['owner_uid'],row['body'],row['state'],row['origin'],row['origin_operation'],row.get('custody_hash'),int(self.clock())))

    def _points(self, db, uid, delta):
        subject = self.platform._player(db, uid)
        old = db.execute('SELECT points FROM player_profiles WHERE subject=?', (subject,)).fetchone()[0]
        if type(old) is not int or not 0 <= old+delta <= 2**63-1:
            raise ValueError('Insufficient points or invalid balance')
        if delta:
            db.execute('UPDATE player_profiles SET points=points+?,updated_at=CURRENT_TIMESTAMP WHERE subject=?', (delta,subject))
        return str(old+delta)

    def _active(self, db, uid):
        if not self.enabled():
            raise PermissionError('Horse trading is disabled')
        if db.execute('SELECT 1 FROM game_bans WHERE uid=?',(uid,)).fetchone():
            raise PermissionError('Participation is restricted')

    def _quote(self, db, uid, realm, kind, body, amount):
        quote = str(uuid4());expires = int(self.clock())+POLICY['quoteSeconds']
        db.execute('INSERT INTO horse_trade_quotes VALUES(?,?,?,?,?,?,?,NULL)',(quote,uid,realm,kind,canonical(body),amount,expires))
        return {'quote':quote,'kind':kind,'body':body,'amount':amount,'expires':expires,'policy':POLICY['version']}

    def _consume(self, db, uid, realm, quote, kind, operation):
        uuid_text(quote)
        row = db.execute('SELECT * FROM horse_trade_quotes WHERE quote=?',(quote,)).fetchone()
        if not row or row['uid'] != uid or row['realm'] != realm or row['kind'] != kind:
            raise PermissionError('Quote owner, world or action mismatch')
        if row['consumed_operation']:
            old = db.execute('SELECT result FROM horse_trade_ledger WHERE operation=?',(row['consumed_operation'],)).fetchone()
            if not old:
                raise RuntimeError('Consumed quote has no durable receipt')
            return dict(row), json.loads(old['result'])
        if row['expires'] <= self.clock():
            raise ValueError('Quote expired; refresh before confirming')
        db.execute('UPDATE horse_trade_quotes SET consumed_operation=? WHERE quote=?',(operation,quote))
        return dict(row), None

    def _expire(self, db, realm):
        rows = db.execute("SELECT * FROM horse_trade_rentals WHERE realm=? AND state='RESERVED' AND expires<=?",(realm,int(self.clock()))).fetchall()
        for row in rows:
            self._end(db, row['uid'], realm, row['rental'], 'reservation_expired')

    def _end(self, db, uid, realm, rental, reason):
        uuid_text(rental)
        row = db.execute('SELECT * FROM horse_trade_rentals WHERE rental=?',(rental,)).fetchone()
        if not row or row['uid'] != uid or row['realm'] != realm:
            raise PermissionError('Rental owner or world mismatch')
        if reason not in ('completed','voluntary','disconnect','prestart_cancel','room_cancelled','build_failure','server_failure','reservation_expired'):
            raise ValueError('Invalid rental end reason')
        if row['state'] == 'ENDED':
            if reason != row['end_reason']:
                raise ValueError('Rental end reason is already immutable')
            return {'rental':rental,'asset':row['asset'],'state':'ENDED','refunded':bool(row['refunded']),'amount':row['amount']}, 0
        if reason == 'completed' and row['state'] != 'RACING':
            raise ValueError('Rental never entered a physical race')
        if reason == 'reservation_expired' and (row['state'] != 'RESERVED' or row['expires'] > self.clock()):
            raise ValueError('Rental has not expired')
        if reason == 'prestart_cancel' and row['state'] == 'RACING':
            raise ValueError('Physical race has already started')
        refund = reason in ('build_failure','server_failure','reservation_expired','prestart_cancel','room_cancelled') or (row['state'] != 'RACING' and reason in ('voluntary','disconnect'))
        delta = row['amount'] if refund else 0
        self._points(db, uid, delta)
        asset = self._asset(db, realm, row['asset'])
        if asset['state'] != 'RENTED':
            raise RuntimeError('Rental stock provenance mismatch')
        asset['state'] = 'POOL';self._put(db, asset)
        db.execute("UPDATE horse_trade_rentals SET state='ENDED',end_reason=?,refunded=? WHERE rental=?",(reason,int(refund),rental))
        # A separate business key also guards expiry/refund when the caller uses new request IDs.
        internal = str(uuid5(NAMESPACE_URL, 'muxi-horse-rental-end:'+rental))
        result = {'rental':rental,'asset':row['asset'],'state':'ENDED','refunded':refund,'amount':row['amount'],'reason':reason}
        db.execute('INSERT OR IGNORE INTO horse_trade_ledger VALUES(?,?,?,?,?,?,?,?)',(internal,uid,realm,'rental_end_business',canonical({'rental':rental,'reason':reason}),canonical(result),delta,int(self.clock())))
        return result, delta

    def execute(self, event):
        fields(event, ('uid','realm','operation','action','payload'))
        uid, realm, op, action, data = event['uid'],uuid_text(event['realm']),uuid_text(event['operation']),event['action'],event['payload']
        self.platform.validate_uid(uid)
        if not isinstance(action,str) or not isinstance(data,dict) or len(canonical(event)) > 8192:
            raise ValueError('Invalid commerce request')
        raw = canonical(event)
        with self.platform.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT request,result FROM horse_trade_ledger WHERE operation=?',(op,)).fetchone()
            if old:
                if old['request'] != raw:
                    raise ValueError('Conflicting commerce replay')
                return {**json.loads(old['result']),'replayed':True}
            self._points(db,uid,0)
            delta = 0
            if action in ('quote','buy','rent','sale_prepare'):
                self._active(db,uid)
                self._expire(db,realm)
            if action == 'quote':
                kind = data.get('kind')
                if kind == 'BUY':
                    fields(data,('kind','sku'));template = CATALOG.get(data['sku'])
                    if not template:raise ValueError('Unknown catalog SKU')
                    result = self._quote(db,uid,realm,kind,{**template,'saddled':True,'armored':False,'policy':POLICY['version']},template['purchase'])
                elif kind == 'RENT':
                    fields(data,('kind','asset','race'));uuid_text(data['race']);asset = self._asset(db,realm,data['asset'])
                    if asset['state'] != 'POOL':raise ValueError('Horse is already reserved or rented')
                    body = json.loads(asset['body']);body.update(asset=asset['asset'],race=data['race'])
                    result = self._quote(db,uid,realm,kind,body,body['rent'])
                elif kind == 'SELL':
                    fields(data,('kind','source'));body = self.source(data['source'],uid)
                    for sku in CATALOG:
                        if body['asset'] == self.house(realm,sku):raise ValueError('Party rental stock cannot be sold')
                    existing = db.execute('SELECT * FROM horse_trade_assets WHERE asset=?',(body['asset'],)).fetchone()
                    if existing and (existing['realm'] != realm or existing['owner_uid'] != uid or existing['state'] != 'OWNED'):
                        raise PermissionError('Only owned horses can be sold; rental resale forbidden')
                    result = self._quote(db,uid,realm,kind,body,body['purchase']*POLICY['buybackPercent']//100)
                else:raise ValueError('Unknown quote action')
            elif action in ('buy','rent','sale_prepare'):
                fields(data,('quote','snapshot') if action=='sale_prepare' else ('quote',))
                kind={'buy':'BUY','rent':'RENT','sale_prepare':'SELL'}[action]
                quote, prior = self._consume(db,uid,realm,data['quote'],kind,op)
                if prior is not None:
                    if action == 'sale_prepare' and data['snapshot'] != prior['snapshot']:
                        raise ValueError('Conflicting native custody snapshot')
                    result={**prior,'businessReplay':True}
                else:
                    body=json.loads(quote['body']);amount=quote['amount']
                    if action == 'buy':
                        if db.execute("SELECT count(*) FROM horse_trade_assets WHERE realm=? AND owner_uid=? AND state IN ('OWNED','GRANT_PENDING','ESCROW_PENDING')",(realm,uid)).fetchone()[0]>=64:raise ValueError('Owned stable is full')
                        asset_id=str(uuid5(NAMESPACE_URL,'muxi-horse-purchase:'+op));body['asset']=asset_id
                        delta=-amount;self._points(db,uid,delta)
                        self._put(db,{'asset':asset_id,'realm':realm,'owner_uid':uid,'body':canonical(body),'state':'GRANT_PENDING','origin':'CATALOG','origin_operation':op,'custody_hash':None})
                        result={'asset':asset_id,'state':'GRANT_PENDING','body':body,'amount':amount}
                    elif action == 'rent':
                        asset=self._asset(db,realm,body['asset'])
                        if asset['state']!='POOL':raise ValueError('Horse was taken before confirmation')
                        delta=-amount;self._points(db,uid,delta);asset['state']='RENTED';self._put(db,asset)
                        expires=int(self.clock())+POLICY['reservationSeconds']
                        db.execute('INSERT INTO horse_trade_rentals VALUES(?,?,?,?,?,?,?, ?,NULL,0,?)',(op,uid,realm,asset['asset'],body['race'],amount,'RESERVED',expires,int(self.clock())))
                        result={'rental':op,'asset':asset['asset'],'race':body['race'],'state':'RESERVED','body':body,'amount':amount,'expires':expires}
                    else:
                        snapshot=data['snapshot']
                        if not isinstance(snapshot,str) or not re.fullmatch('[0-9a-f]{64}',snapshot):raise ValueError('Invalid native custody snapshot hash')
                        existing=db.execute('SELECT * FROM horse_trade_assets WHERE asset=?',(body['asset'],)).fetchone()
                        if existing and (existing['realm']!=realm or existing['owner_uid']!=uid or existing['state']!='OWNED'):raise ValueError('Horse is not available for sale')
                        asset=dict(existing) if existing else {'asset':body['asset'],'realm':realm,'owner_uid':uid,'body':canonical(body),'state':'NATIVE','origin':'NATIVE','origin_operation':op}
                        previous=asset['state'];previous_body=asset['body']
                        asset.update(body=canonical(body),state='ESCROW_PENDING',custody_hash=snapshot);self._put(db,asset)
                        db.execute('INSERT INTO horse_trade_sales VALUES(?,?,?,?,?,?,?,?,?,?,?)',(op,uid,realm,asset['asset'],amount,snapshot,'PREPARED',previous,previous_body,data['quote'],int(self.clock())))
                        result={'sale':op,'asset':asset['asset'],'state':'PREPARED','body':body,'amount':amount,'snapshot':snapshot}
            elif action in ('grant_ack','grant_cancel'):
                fields(data,('asset','parent','proof') if action=='grant_ack' else ('asset','parent'))
                asset=self._asset(db,realm,data['asset'])
                if asset['owner_uid']!=uid or asset['origin_operation']!=data['parent']:raise PermissionError('Grant provenance mismatch')
                if action=='grant_ack':
                    if asset['state'] not in ('GRANT_PENDING','OWNED'):raise ValueError('Grant is unavailable')
                    if not isinstance(data['proof'],str) or not re.fullmatch('[0-9a-f]{64}',data['proof']):raise ValueError('Invalid native delivery proof')
                    if asset['state']=='OWNED' and asset['custody_hash']!=data['proof']:raise ValueError('Conflicting delivery proof')
                    asset.update(state='OWNED',custody_hash=data['proof']);self._put(db,asset);result={'asset':asset['asset'],'state':'OWNED'}
                else:
                    if asset['state'] not in ('GRANT_PENDING','REVOKED'):raise ValueError('An acknowledged owned horse cannot be refunded')
                    if asset['state']=='GRANT_PENDING':
                        parent=db.execute('SELECT result FROM horse_trade_ledger WHERE operation=?',(data['parent'],)).fetchone()
                        if not parent:raise RuntimeError('Purchase receipt missing')
                        delta=json.loads(parent['result'])['amount'];self._points(db,uid,delta);asset['state']='REVOKED';self._put(db,asset)
                    result={'asset':asset['asset'],'state':'REVOKED'}
            elif action in ('sale_commit','sale_cancel'):
                fields(data,('sale','proof'))
                sale=db.execute('SELECT * FROM horse_trade_sales WHERE sale=?',(data['sale'],)).fetchone()
                if not sale or sale['uid']!=uid or sale['realm']!=realm or sale['snapshot']!=data['proof']:raise PermissionError('Sale custody provenance mismatch')
                asset=self._asset(db,realm,sale['asset'])
                wanted='COMMITTED' if action=='sale_commit' else 'CANCELLED'
                if sale['state'] not in ('PREPARED',wanted):raise ValueError('Sale decision is already immutable')
                if sale['state']=='PREPARED':
                    if asset['state']!='ESCROW_PENDING':raise RuntimeError('Escrow inventory mismatch')
                    if action=='sale_commit':
                        delta=sale['amount'];self._points(db,uid,delta);asset.update(state='POOL',owner_uid=None)
                    else:
                        asset.update(state='OWNED',owner_uid=uid,body=sale['previous_body'])
                    self._put(db,asset);db.execute('UPDATE horse_trade_sales SET state=? WHERE sale=?',(wanted,sale['sale']))
                result={'sale':sale['sale'],'asset':asset['asset'],'state':wanted,'amount':sale['amount']}
            elif action in ('rental_launch','rental_running'):
                fields(data,('rental','race'));uuid_text(data['race'])
                rental=db.execute('SELECT * FROM horse_trade_rentals WHERE rental=?',(data['rental'],)).fetchone()
                if not rental or rental['uid']!=uid or rental['realm']!=realm or rental['race']!=data['race']:raise PermissionError('Rental session mismatch')
                target='LAUNCHING' if action=='rental_launch' else 'RACING'
                allowed=('RESERVED','LAUNCHING','RACING') if target=='LAUNCHING' else ('LAUNCHING','RACING')
                if rental['state'] not in allowed:raise ValueError('Rental cannot enter this race')
                if target=='LAUNCHING' and rental['state']=='RESERVED' and rental['expires']<=self.clock():raise ValueError('Rental reservation expired')
                state='RACING' if rental['state']=='RACING' else target
                db.execute('UPDATE horse_trade_rentals SET state=? WHERE rental=?',(state,rental['rental']))
                result={'rental':rental['rental'],'asset':rental['asset'],'state':state,'race':rental['race']}
            elif action=='rental_end':
                fields(data,('rental','reason'));result,delta=self._end(db,uid,realm,data['rental'],data['reason'])
            elif action=='expire':
                fields(data,());self._expire(db,realm);result={'state':'EXPIRY_CHECKED'}
            else:raise ValueError('Unknown horse commerce action')
            result={**result,'ok':True,'operation':result.get('operation',op),'delta':delta,'balanceAfter':self._points(db,uid,0),'policy':POLICY['version']}
            # Refund money is journalled once under the rental business key. The
            # public request receipt is an alias, not a second money movement.
            ledger_delta = 0 if action == 'rental_end' else delta
            db.execute('INSERT INTO horse_trade_ledger VALUES(?,?,?,?,?,?,?,?)',(op,uid,realm,action,raw,canonical(result),ledger_delta,int(self.clock())))
            return result

    def market(self, uid, realm):
        self.platform.validate_uid(uid);uuid_text(realm)
        with self.platform.connect() as db:
            balance=self._points(db,uid,0)
            owned=[{**dict(row),'body':json.loads(row['body'])} for row in db.execute('SELECT * FROM horse_trade_assets WHERE realm=? AND owner_uid=? ORDER BY updated_at DESC LIMIT 64',(realm,uid))]
            pool=[]
            for sku in CATALOG:
                asset=self._asset(db,realm,self.house(realm,sku));pool.append({**asset,'body':json.loads(asset['body'])})
            pool.extend({**dict(row),'body':json.loads(row['body'])} for row in db.execute("SELECT * FROM horse_trade_assets WHERE realm=? AND origin!='HOUSE' AND state IN ('POOL','RENTED') ORDER BY updated_at DESC LIMIT 20",(realm,)))
            rentals=[dict(row) for row in db.execute("SELECT * FROM horse_trade_rentals WHERE realm=? AND uid=? AND state!='ENDED' ORDER BY created_at DESC LIMIT 16",(realm,uid))]
            sales=[dict(row) for row in db.execute("SELECT * FROM horse_trade_sales WHERE realm=? AND uid=? AND state='PREPARED' ORDER BY created_at DESC LIMIT 16",(realm,uid))]
        return {'enabled':bool(self.enabled()),'balance':balance,'policy':POLICY,'catalog':list(CATALOG.values()),'owned':owned,'pool':pool,'rentals':rentals,'sales':sales}


def horse_trading_router(database_path, service_key):
    """The shared owner adds one import and one include_router call in main.py."""
    from fastapi import APIRouter, Depends, HTTPException, Request
    router=APIRouter(prefix='/api/internal/game',dependencies=[Depends(service_key)])
    stores={}
    def store():
        path=Path(database_path() if callable(database_path) else database_path)
        return stores.setdefault(str(path),HorseTradeStore(path)) if str(path) not in stores else stores[str(path)]
    @router.get('/horse-market/{uid}',include_in_schema=False)
    def market(uid:int,realm:str):
        try:return store().market(uid,realm)
        except PermissionError as error:raise HTTPException(403,str(error)) from None
        except LookupError as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError) as error:raise HTTPException(400,str(error)) from None
    @router.post('/horse-trades',include_in_schema=False)
    async def trade(request:Request):
        raw=await request.body()
        if len(raw)>8192:raise HTTPException(413,'Commerce request too large')
        try:return store().execute(json.loads(raw))
        except PermissionError as error:raise HTTPException(403,str(error)) from None
        except LookupError as error:raise HTTPException(409,str(error)) from None
        except (ValueError,TypeError,KeyError,OverflowError) as error:raise HTTPException(409,str(error)) from None
    return router
