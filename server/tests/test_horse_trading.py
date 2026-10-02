from pathlib import Path
import importlib.util, types, sys, sqlite3, tempfile, unittest, uuid
from concurrent.futures import ThreadPoolExecutor

SHARED=Path(r'C:\Users\ranzh\Documents\Codex\2026-10-02\task\dev-sync-20261002\better-mc-remake\server\app')
OWN=Path(__file__).parents[1]/'app'
pkg=types.ModuleType('horse_trade_test');pkg.__path__=[str(OWN),str(SHARED)];sys.modules[pkg.__name__]=pkg
def load(name,path):
    spec=importlib.util.spec_from_file_location('horse_trade_test.'+name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
load('game_identity',SHARED/'game_identity.py');load('player_platform',SHARED/'player_platform.py')
trade=load('horse_trading',OWN/'horse_trading.py')
def fresh():return str(uuid.uuid4())

class Commerce(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'qa.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE player_profiles(subject TEXT PRIMARY KEY,uid INTEGER,points INTEGER,balance_cents INTEGER,updated_at TEXT)')
            db.executemany("INSERT INTO player_profiles VALUES(?,?,10000,321,'')",[(str(v),v) for v in (10000,10001,10002)])
        self.now=1000;self.enabled=True;self.realm=fresh()
        self.store=trade.HorseTradeStore(self.path,lambda:self.now,lambda:self.enabled)
    def tearDown(self):
        import gc;gc.collect();self.folder.cleanup()
    def call(self,action,payload,uid=10000,operation=None):
        return self.store.execute(dict(uid=uid,realm=self.realm,operation=operation or fresh(),action=action,payload=payload))
    def quote(self,kind,**values):return self.call('quote',dict(kind=kind,**values))
    def buy(self,sku='seabiscuit'):
        return self.call('buy',{'quote':self.quote('BUY',sku=sku)['quote']})
    def rent(self,uid=10000):
        race=fresh();q=self.call('quote',dict(kind='RENT',asset=self.store.house(self.realm,'equinox'),race=race),uid)
        return self.call('rent',dict(quote=q['quote']),uid)
    def source(self):
        return dict(asset=fresh(),name='自养马甲',speed=.3375,jump=1.,health=30,variant=2,markings=1,saddled=True,armored=True,owner=trade.offline_uuid(10000),fingerprint='a'*64)
    def sale(self):
        q=self.quote('SELL',source=self.source())
        return self.call('sale_prepare',dict(quote=q['quote'],snapshot='b'*64))
    def balance(self,uid=10000):return int(self.store.platform.point_balance(uid))
    def assertLedger(self):
        with sqlite3.connect(self.path) as db:
            for uid,points,cash in db.execute('SELECT uid,points,balance_cents FROM player_profiles'):
                delta=db.execute('SELECT coalesce(sum(delta),0) FROM horse_trade_ledger WHERE uid=?',(uid,)).fetchone()[0]
                self.assertEqual(points,10000+delta);self.assertEqual(cash,321)
            self.assertEqual(db.execute('SELECT count(*) FROM minigame_result_ledger').fetchone()[0],0)
    def test_buy_lost_ack_quote_alias_proof_and_refund(self):
        q=self.quote('BUY',sku='equinox');op=fresh();r=self.call('buy',dict(quote=q['quote']),operation=op)
        self.assertEqual(r['amount'],2400);self.assertEqual(self.balance(),7600)
        self.assertTrue(self.call('buy',dict(quote=q['quote']),operation=op)['replayed'])
        alias=self.call('buy',dict(quote=q['quote']));self.assertTrue(alias['businessReplay']);self.assertEqual(alias['asset'],r['asset'])
        self.assertEqual(self.balance(),7600)
        with self.assertRaises(ValueError):self.call('buy',dict(quote=self.quote('BUY',sku='winx')['quote']),operation=op)
        ack=dict(asset=r['asset'],parent=op,proof='c'*64)
        self.call('grant_ack',ack);self.call('grant_ack',ack)
        with self.assertRaises(ValueError):self.call('grant_ack',{**ack,'proof':'d'*64})
        with self.assertRaises(ValueError):self.call('grant_cancel',dict(asset=r['asset'],parent=op))
        pending=self.buy();cancel=dict(asset=pending['asset'],parent=pending['operation'])
        self.call('grant_cancel',cancel);self.call('grant_cancel',cancel);self.assertEqual(self.balance(),7600);self.assertLedger()
    def test_sale_custody_cancel_commit_resale_and_stock(self):
        s=self.sale();self.assertEqual(self.balance(),10000)
        with self.assertRaises(PermissionError):self.call('sale_commit',dict(sale=s['sale'],proof='c'*64))
        commit=dict(sale=s['sale'],proof='b'*64);self.call('sale_commit',commit);self.call('sale_commit',commit)
        self.assertEqual(self.balance(),10000+s['amount'])
        with self.assertRaises(ValueError):self.call('sale_cancel',commit)
        body=s['body'];source={k:body[k] for k in self.source()}
        with self.assertRaises(PermissionError):self.quote('SELL',source=source)
        stock=self.store.market(10000,self.realm)['pool'];self.assertTrue(any(v['asset']==s['asset'] and v['state']=='POOL' for v in stock))
        s2=self.sale();self.call('sale_cancel',dict(sale=s2['sale'],proof='b'*64));self.assertEqual(self.balance(),10000+s['amount']);self.assertLedger()
    def test_rental_reasons_immutable_refund_ledger(self):
        for reason,running,refund in [('prestart_cancel',False,True),('disconnect',False,True),('voluntary',True,False),('disconnect',True,False),('server_failure',True,True),('build_failure',False,True),('completed',True,False)]:
            before=self.balance();r=self.rent();p=dict(rental=r['rental'],race=r['race'])
            if running:self.call('rental_launch',p);self.call('rental_running',p)
            end=dict(rental=r['rental'],reason=reason);receipt=self.call('rental_end',end);self.call('rental_end',end)
            self.assertEqual(receipt['refunded'],refund);self.assertEqual(self.balance(),before-(0 if refund else r['amount']))
            with self.assertRaises(ValueError):self.call('rental_end',dict(rental=r['rental'],reason='server_failure' if reason!='server_failure' else 'completed'))
        self.assertLedger()
    def test_concurrent_stock_no_double_rent(self):
        asset=self.store.house(self.realm,'equinox')
        quotes=[self.call('quote',dict(kind='RENT',asset=asset,race=fresh()),uid)['quote'] for uid in (10000,10001)]
        def confirm(pair):
            uid,q=pair
            try:return self.call('rent',dict(quote=q),uid)
            except ValueError:return None
        with ThreadPoolExecutor(2) as pool:out=list(pool.map(confirm,zip((10000,10001),quotes)))
        self.assertEqual(sum(v is not None for v in out),1);self.assertLedger()
    def test_rollback_db_fault_and_insufficient_balance(self):
        q=self.quote('BUY',sku='winx');op=fresh()
        with sqlite3.connect(self.path) as db:db.execute("CREATE TRIGGER fail_trade BEFORE INSERT ON horse_trade_assets BEGIN SELECT RAISE(ABORT,'injected stock fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.call('buy',dict(quote=q['quote']),operation=op)
        self.assertEqual(self.balance(),10000)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT consumed_operation FROM horse_trade_quotes WHERE quote=?',(q['quote'],)).fetchone()[0]);db.execute('DROP TRIGGER fail_trade')
        self.call('buy',dict(quote=q['quote']),operation=op);self.assertEqual(self.balance(),9750);self.assertLedger()
    def test_expiry_default_off_recovery_and_owner_rejection(self):
        r=self.rent();self.now+=601;self.call('expire',{});self.call('expire',{});self.assertEqual(self.balance(),10000)
        q=self.quote('BUY',sku='winx');self.now+=121
        with self.assertRaises(ValueError):self.call('buy',dict(quote=q['quote']))
        pending=self.buy();self.enabled=False
        with self.assertRaises(PermissionError):self.quote('BUY',sku='winx')
        self.call('grant_cancel',dict(asset=pending['asset'],parent=pending['operation']));self.assertEqual(self.balance(),10000)
        self.enabled=True
        with self.assertRaises(PermissionError):self.quote('SELL',source={**self.source(),'owner':trade.offline_uuid(10001)})
        with self.assertRaises(ValueError):self.quote('SELL',source={**self.source(),'speed':float('nan')})
        self.assertLedger()
    def test_concurrent_delivery_or_refund_has_one_immutable_winner(self):
        purchase=self.buy();payload=dict(asset=purchase['asset'],parent=purchase['operation'])
        def decide(action):
            try:return self.call(action,{**payload,**({'proof':'c'*64} if action=='grant_ack' else {})})
            except ValueError:return None
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(decide,('grant_ack','grant_cancel')))
        self.assertEqual(sum(v is not None for v in results),1)
        self.assertIn(self.balance(),(10000,9880));self.assertLedger()
    def test_sale_repeated_custody_hash_and_cross_realm_are_rejected(self):
        q=self.quote('SELL',source=self.source());first=self.call('sale_prepare',dict(quote=q['quote'],snapshot='b'*64))
        with self.assertRaises(ValueError):self.call('sale_prepare',dict(quote=q['quote'],snapshot='c'*64))
        other=trade.HorseTradeStore(self.path,lambda:self.now,lambda:True)
        with self.assertRaises(PermissionError):other.execute(dict(uid=10000,realm=fresh(),operation=fresh(),action='sale_commit',payload=dict(sale=first['sale'],proof='b'*64)))
        self.assertEqual(self.balance(),10000);self.assertLedger()

if __name__=='__main__':unittest.main(verbosity=2)
