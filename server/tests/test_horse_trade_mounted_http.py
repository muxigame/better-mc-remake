"""Real main.app horse-commerce routes over loopback HTTP; disposable balances only."""
import json,os,sys,unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_horse_results as fixture
from app.game_identity import offline_uuid
from app.horse_trading import HorseTradeStore

def fresh():return str(uuid4())
class MountedHorseTradeHTTP(unittest.TestCase):
    setUpClass=classmethod(fixture.HorseResults.setUpClass.__func__)
    tearDownClass=classmethod(fixture.HorseResults.tearDownClass.__func__)
    request=fixture.HorseResults.request
    def setUp(self):
        fixture.HorseResults.setUp(self)
        os.environ.pop('BMC_HORSE_TRADING_ENABLED',None)
        self.realm=fresh()
        with fixture.fixture_connect(fixture.DB) as db:
            # Every test resets both sides of the disposable wallet/ledger equation.
            existing={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('horse_trade_rentals','horse_trade_sales','horse_trade_quotes','horse_trade_assets','horse_trade_ledger'):
                if table in existing:db.execute('DELETE FROM '+table)
            db.execute('UPDATE player_profiles SET points=10000,balance_cents=321')
    def trade(self,action,payload,uid=10000,operation=None,realm=None,key=fixture.KEY,cookie=None):
        event=dict(uid=uid,realm=realm or self.realm,operation=operation or fresh(),action=action,payload=payload)
        return self.request(event,key=key,cookie=cookie,path='/api/internal/game/horse-trades')
    def market(self,uid=10000,realm=None,key=fixture.KEY):
        return self.request(key=key,path=f'/api/internal/game/horse-market/{uid}?realm={realm or self.realm}')
    def quote(self,kind,uid=10000,**values):
        status,data=self.trade('quote',dict(kind=kind,**values),uid)
        self.assertEqual(200,status,data);return data
    def ledger(self):
        with fixture.fixture_connect(fixture.DB) as db:
            for uid,points,cents in db.execute('SELECT uid,points,balance_cents FROM player_profiles'):
                delta=db.execute('SELECT coalesce(sum(delta),0) FROM horse_trade_ledger WHERE uid=?',(uid,)).fetchone()[0]
                self.assertEqual(points,10000+delta);self.assertEqual(cents,321)
            self.assertEqual(0,db.execute('SELECT count(*) FROM minigame_result_ledger').fetchone()[0])
    def source(self):
        return dict(asset=fresh(),name='Synthetic raised horse',speed=.3375,jump=1.,health=30,variant=2,markings=1,saddled=True,armored=True,owner=offline_uuid(10000),fingerprint='a'*64)
    def test_real_main_mount_and_default_off(self):
        paths=[r.path for r in fixture.main.app.routes]
        self.assertEqual(1,paths.count('/api/internal/game/horse-trades'))
        self.assertEqual(1,paths.count('/api/internal/game/horse-market/{uid}'))
        self.assertNotIn('BMC_HORSE_TRADING_ENABLED',os.environ)
        status,market=self.market();self.assertEqual(200,status);self.assertFalse(market['enabled'])
        self.assertEqual('10000',market['balance'])
        self.assertEqual(403,self.trade('quote',dict(kind='BUY',sku='winx'))[0]);self.ledger()
    def test_actual_platform_guard_cookie_and_disabled_integration(self):
        for key in (None,'wrong-synthetic-key'):
            self.assertEqual(401,self.market(key=key)[0])
            self.assertEqual(401,self.trade('quote',dict(kind='BUY',sku='winx'),key=key,cookie=self.cookies[10000])[0])
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'0','BMC_HORSE_TRADING_ENABLED':'1'}):
            self.assertEqual(503,self.market()[0]);self.assertEqual(503,self.trade('quote',dict(kind='BUY',sku='winx'))[0])
        with fixture.fixture_connect(fixture.DB) as db:self.assertEqual(10000,db.execute('SELECT points FROM player_profiles WHERE uid=10000').fetchone()[0])
    def test_quote_and_purchase_concurrent_lost_ack_debits_once(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            quote=self.quote('BUY',sku='winx');self.assertEqual(250,quote['amount']);operation=fresh()
            with ThreadPoolExecutor(max_workers=8) as pool:
                replies=list(pool.map(lambda _:self.trade('buy',dict(quote=quote['quote']),operation=operation),range(16)))
            self.assertTrue(all(status==200 for status,_ in replies));self.assertEqual(1,sum(not body.get('replayed',False) for _,body in replies))
            assets={body['asset'] for _,body in replies};self.assertEqual(1,len(assets))
            status,alias=self.trade('buy',dict(quote=quote['quote']));self.assertEqual(200,status);self.assertTrue(alias['businessReplay'])
            status,market=self.market();self.assertEqual((200,'9750'),(status,market['balance']));self.assertEqual(1,len(market['owned']));self.ledger()
    def test_delivery_receipt_is_immutable_and_cannot_refund_owned_horse(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            q=self.quote('BUY',sku='seabiscuit');status,purchase=self.trade('buy',dict(quote=q['quote']));self.assertEqual(200,status)
            ack=dict(asset=purchase['asset'],parent=purchase['operation'],proof='c'*64)
            self.assertEqual(200,self.trade('grant_ack',ack)[0]);self.assertEqual(200,self.trade('grant_ack',ack)[0])
            self.assertEqual(409,self.trade('grant_ack',{**ack,'proof':'d'*64})[0])
            self.assertEqual(409,self.trade('grant_cancel',dict(asset=purchase['asset'],parent=purchase['operation']))[0])
            self.assertEqual('9880',self.market()[1]['balance']);self.ledger()
    def test_synthetic_sale_custody_commit_and_pool_receipt(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            self.assertEqual(403,self.trade('quote',dict(kind='SELL',source={**self.source(),'owner':offline_uuid(10001)}))[0])
            quote=self.quote('SELL',source=self.source());status,sale=self.trade('sale_prepare',dict(quote=quote['quote'],snapshot='b'*64));self.assertEqual(200,status)
            self.assertEqual('10000',self.market()[1]['balance'])
            self.assertEqual(403,self.trade('sale_commit',dict(sale=sale['sale'],proof='c'*64))[0])
            commit=dict(sale=sale['sale'],proof='b'*64)
            self.assertEqual(200,self.trade('sale_commit',commit)[0]);self.assertEqual(200,self.trade('sale_commit',commit)[0])
            self.assertEqual(409,self.trade('sale_cancel',commit)[0])
            market=self.market()[1];self.assertEqual(str(10000+sale['amount']),market['balance'])
            self.assertTrue(any(asset['asset']==sale['asset'] and asset['state']=='POOL' for asset in market['pool']));self.ledger()
    def test_rental_before_start_refund_and_completed_no_refund(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            asset=HorseTradeStore.house(self.realm,'equinox')
            for started,reason,refunded in [(False,'prestart_cancel',True),(True,'completed',False)]:
                before=int(self.market()[1]['balance']);race=fresh();quote=self.quote('RENT',asset=asset,race=race)
                status,rental=self.trade('rent',dict(quote=quote['quote']));self.assertEqual(200,status);self.assertEqual(45,rental['amount'])
                if started:
                    payload=dict(rental=rental['rental'],race=race)
                    self.assertEqual(200,self.trade('rental_launch',payload)[0]);self.assertEqual(200,self.trade('rental_running',payload)[0])
                end=dict(rental=rental['rental'],reason=reason);status,receipt=self.trade('rental_end',end)
                self.assertEqual(200,status);self.assertEqual(refunded,receipt['refunded']);self.assertEqual(200,self.trade('rental_end',end)[0])
                self.assertEqual(str(before-(0 if refunded else 45)),self.market()[1]['balance'])
                if started:self.assertEqual(409,self.trade('rental_end',{**end,'reason':'server_failure'})[0])
            self.ledger()
    def test_concurrent_rental_stock_has_only_one_buyer(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            asset=HorseTradeStore.house(self.realm,'equinox')
            quotes=[self.quote('RENT',uid=uid,asset=asset,race=fresh()) for uid in (10000,10001)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                replies=list(pool.map(lambda pair:self.trade('rent',dict(quote=pair[1]['quote']),uid=pair[0]),zip((10000,10001),quotes)))
            self.assertEqual([200,409],sorted(status for status,_ in replies));self.ledger()
    def test_disabled_new_trading_retains_existing_recovery(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            quote=self.quote('BUY',sku='winx');status,purchase=self.trade('buy',dict(quote=quote['quote']));self.assertEqual(200,status)
        self.assertEqual(403,self.trade('quote',dict(kind='BUY',sku='winx'))[0])
        cancel=dict(asset=purchase['asset'],parent=purchase['operation'])
        self.assertEqual(200,self.trade('grant_cancel',cancel)[0]);self.assertEqual(200,self.trade('grant_cancel',cancel)[0])
        market=self.market()[1];self.assertFalse(market['enabled']);self.assertEqual('10000',market['balance']);self.ledger()
    def test_uid_quote_and_world_ownership_cannot_be_changed(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            for uid in (True,'10000',10000.0,9999,10000000000000000):
                self.assertEqual(409,self.trade('quote',dict(kind='BUY',sku='winx'),uid=uid)[0])
            self.assertEqual(409,self.trade('quote',dict(kind='BUY',sku='winx'),uid=10002)[0])
            q=self.quote('BUY',sku='winx')
            self.assertEqual(403,self.trade('buy',dict(quote=q['quote']),uid=10001)[0])
            self.assertEqual(403,self.trade('buy',dict(quote=q['quote']),realm=fresh())[0]);self.ledger()
    def test_schema_amount_injection_and_size_rejected(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            self.assertEqual(409,self.trade('quote',dict(kind='BUY',sku='winx',amount=1))[0])
            body=dict(uid=10000,realm=self.realm,operation=fresh(),action='quote',payload=dict(kind='BUY',sku='winx'),points=999)
            self.assertEqual(409,self.request(body,path='/api/internal/game/horse-trades')[0])
            self.assertEqual(409,self.request(raw=b'invalid',path='/api/internal/game/horse-trades')[0])
            self.assertEqual(413,self.request(raw=b'x'*8193,path='/api/internal/game/horse-trades')[0])
            self.ledger()
    def test_atomic_stock_failure_leaves_quote_wallet_and_receipt_retryable(self):
        with patch.dict(os.environ,{'BMC_HORSE_TRADING_ENABLED':'1'}):
            quote=self.quote('BUY',sku='winx');operation=fresh()
            with fixture.fixture_connect(fixture.DB) as db:db.execute("CREATE TRIGGER mounted_trade_qa_fail BEFORE INSERT ON horse_trade_assets BEGIN SELECT RAISE(ABORT,'synthetic QA'); END")
            try:
                self.assertEqual(500,self.trade('buy',dict(quote=quote['quote']),operation=operation)[0])
                self.assertEqual('10000',self.market()[1]['balance'])
                with fixture.fixture_connect(fixture.DB) as db:self.assertIsNone(db.execute('SELECT consumed_operation FROM horse_trade_quotes WHERE quote=?',(quote['quote'],)).fetchone()[0])
            finally:
                with fixture.fixture_connect(fixture.DB) as db:db.execute('DROP TRIGGER mounted_trade_qa_fail')
            self.assertEqual(200,self.trade('buy',dict(quote=quote['quote']),operation=operation)[0]);self.assertEqual('9750',self.market()[1]['balance']);self.ledger()

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MountedHorseTradeHTTP))
    fixture.TEMP.cleanup()
    receipt={'success':result.wasSuccessful(),'tests':result.testsRun,'actualMainAppRoutes':True,'actualLoopbackHTTP':True,'httpRequests':len(fixture.REQUESTS),'responseStatuses':{str(code):sum(r['status']==code for r in fixture.REQUESTS) for code in sorted({r['status'] for r in fixture.REQUESTS})},'syntheticDatabaseRemoved':not fixture.DB.exists(),'ownHTTPServerExited':not MountedHorseTradeHTTP.thread.is_alive(),'featureDefaultOffVerified':True,'productionMutation':False,'scope':'Actual shared backend routing and real service guard; synthetic local balances/custody descriptors. No new native horse delivery or Minecraft identity test.'}
    output=os.environ.get('HORSE_MOUNT_QA_RECEIPT')
    if output:Path(output).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt));sys.exit(0 if result.wasSuccessful() else 1)
