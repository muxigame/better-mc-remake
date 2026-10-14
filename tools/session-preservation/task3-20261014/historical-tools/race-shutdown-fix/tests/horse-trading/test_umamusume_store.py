"""Exercise actual shared SQLite commerce with private data, no wallet substitute."""
from pathlib import Path
import importlib.util, json, sqlite3, unittest, os
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('existing_horse_tests',ROOT/'better-mc-remake/server/tests/test_horse_trading.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
trade=base.trade

class Companions(unittest.TestCase):
    def setUp(self):
        self.previous_registration=os.environ.get('BMC_UMAMUSUME_REGISTRATION_VERIFIED')
        os.environ['BMC_UMAMUSUME_REGISTRATION_VERIFIED']='1'
        base.Commerce.setUp(self)
    def tearDown(self):
        if self.previous_registration is None:os.environ.pop('BMC_UMAMUSUME_REGISTRATION_VERIFIED',None)
        else:os.environ['BMC_UMAMUSUME_REGISTRATION_VERIFIED']=self.previous_registration
        base.Commerce.tearDown(self)
    def test_registration_gate_stops_new_debits_but_preserves_recovery(self):
        quote=self.quote('BUY',sku='uma_special_week')
        pending=self.buy('uma_oguri_cap')
        os.environ.pop('BMC_UMAMUSUME_REGISTRATION_VERIFIED')
        with self.assertRaises(PermissionError):self.quote('BUY',sku='uma_special_week')
        with self.assertRaises(PermissionError):self.call('buy',{'quote':quote['quote']})
        self.assertFalse(self.store.market(10000,self.realm)['maidCatalogReady'])
        self.call('grant_cancel',{'asset':pending['asset'],'parent':pending['operation']})
        self.assertEqual(self.balance(),10000)
        # Ordinary horse products remain independent of companion registration.
        self.buy('seabiscuit');self.assertEqual(self.balance(),9880);self.assertLedger()
    call=base.Commerce.call
    quote=base.Commerce.quote
    buy=base.Commerce.buy
    balance=base.Commerce.balance
    assertLedger=base.Commerce.assertLedger
    source=base.Commerce.source

    def test_six_manifest_products_share_points_and_fixed_receipts(self):
        manifest=json.loads((ROOT/'muxi-ysm-tools/build/release-six-approved-20261007/display-metadata-update-handoff.json').read_text(encoding='utf-8'))
        native=json.loads((ROOT/'muxi-horse-racing/src/main/resources/umamusume_catalog_v1.json').read_text(encoding='utf-8'))
        self.assertEqual(native,trade.MAID_POLICY)
        self.assertEqual({p['modelId'] for p in trade.MAID_CATALOG.values()},{m['runtimeModelId'] for m in manifest['models']})
        total=0
        for product in trade.MAID_CATALOG.values():
            quote=self.quote('BUY',sku=product['sku'])
            self.assertEqual(quote['amount'],product['purchase'])
            result=self.call('buy',{'quote':quote['quote']})
            for key in ('entityKind','modelId','texture','championTier','affixes','traits','modelFilesSha256'):
                self.assertEqual(result['body'][key],product[key])
            self.assertEqual(result['state'],'GRANT_PENDING');total+=product['purchase']
        self.assertEqual(self.balance(),10000-total)
        self.assertEqual(len(self.store.market(10000,self.realm)['maidCatalog']),6)
        self.assertEqual(len(self.store.market(10000,self.realm)['catalog']),5)
        self.assertEqual(len(self.store.market(10000,self.realm)['pool']),5)
        self.assertTrue(trade.valid_maid_catalog());self.assertLedger()

    def test_packet_cannot_choose_price_skin_tier_or_affixes(self):
        for key,value in [('purchase',1),('modelId','unauthorized'),('tier',5),('affixes',[])]:
            with self.assertRaises(ValueError):self.call('quote',{'kind':'BUY','sku':'uma_special_week',key:value})
        q=self.quote('BUY',sku='uma_tokai_teio')
        with self.assertRaises(ValueError):self.call('buy',{'quote':q['quote'],'amount':1})
        self.assertEqual(self.balance(),10000);self.assertLedger()

    def test_simultaneous_confirm_and_restart_has_one_asset_and_one_debit(self):
        q=self.quote('BUY',sku='uma_tokai_teio');op=base.fresh()
        def confirm(i):return self.call('buy',{'quote':q['quote']},operation=op if i<2 else None)
        with ThreadPoolExecutor(4) as pool:receipts=list(pool.map(confirm,range(4)))
        self.assertEqual(len({r['asset'] for r in receipts}),1);self.assertEqual(self.balance(),7600)
        self.store=trade.HorseTradeStore(self.path,lambda:self.now,lambda:True)
        replay=self.call('buy',{'quote':q['quote']},operation=op)
        self.assertTrue(replay['replayed']);self.assertEqual(replay['asset'],receipts[0]['asset'])
        self.assertEqual(len(self.store.market(10000,self.realm)['owned']),1)
        self.assertLedger()

    def test_quote_keeps_advertised_product_when_catalog_changes(self):
        q=self.quote('BUY',sku='uma_special_week');product=trade.MAID_CATALOG['uma_special_week']
        before=json.loads(json.dumps(product))
        try:
            product['purchase']=2400;product['affixes']=[];product['modelId']='changed'
            receipt=self.call('buy',{'quote':q['quote']})
            self.assertEqual(receipt['amount'],120);self.assertEqual(receipt['body']['affixes'],before['affixes'])
            self.assertEqual(receipt['body']['modelId'],before['modelId'])
        finally:product.clear();product.update(before)
        self.assertLedger()

    def test_insufficient_balance_and_storage_failure_are_atomic(self):
        q=self.quote('BUY',sku='uma_tokai_teio');op=base.fresh()
        with sqlite3.connect(self.path) as db:db.execute('UPDATE player_profiles SET points=1 WHERE uid=10000')
        with self.assertRaises(ValueError):self.call('buy',{'quote':q['quote']},operation=op)
        with sqlite3.connect(self.path) as db:
            self.assertIsNone(db.execute('SELECT consumed_operation FROM horse_trade_quotes WHERE quote=?',(q['quote'],)).fetchone()[0])
            db.execute('UPDATE player_profiles SET points=10000 WHERE uid=10000')
            db.execute("CREATE TRIGGER fail_maid BEFORE INSERT ON horse_trade_assets BEGIN SELECT RAISE(ABORT,'private injected fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.call('buy',{'quote':q['quote']},operation=op)
        self.assertEqual(self.balance(),10000)
        with sqlite3.connect(self.path) as db:db.execute('DROP TRIGGER fail_maid')
        self.call('buy',{'quote':q['quote']},operation=op);self.assertLedger()

    def test_delivery_or_refund_requires_owner_world_and_original_order(self):
        purchase=self.buy('uma_oguri_cap');payload={'asset':purchase['asset'],'parent':purchase['operation']}
        with self.assertRaises(PermissionError):self.call('grant_cancel',payload,uid=10001)
        with self.assertRaises(PermissionError):self.store.execute(dict(uid=10000,realm=base.fresh(),operation=base.fresh(),action='grant_cancel',payload=payload))
        with self.assertRaises(PermissionError):self.call('grant_cancel',{**payload,'parent':base.fresh()})
        self.call('grant_ack',{**payload,'proof':'a'*64});self.call('grant_ack',{**payload,'proof':'a'*64})
        with self.assertRaises(ValueError):self.call('grant_cancel',payload)
        pending=self.buy('uma_special_week');cancel={'asset':pending['asset'],'parent':pending['operation']}
        self.call('grant_cancel',cancel);self.call('grant_cancel',cancel)
        self.assertEqual(self.balance(),8800);self.assertLedger()

    def test_companion_cannot_enter_horse_resale_or_rental_pool(self):
        purchase=self.buy('uma_gold_ship');payload={'asset':purchase['asset'],'parent':purchase['operation'],'proof':'a'*64}
        self.call('grant_ack',payload)
        source={**self.source(),'asset':purchase['asset']}
        with self.assertRaises(ValueError):self.quote('SELL',source=source)
        # Even invalid legacy pool state cannot turn a companion into a rental horse.
        with sqlite3.connect(self.path) as db:db.execute("UPDATE horse_trade_assets SET state='POOL' WHERE asset=?",(purchase['asset'],))
        with self.assertRaises(ValueError):self.quote('RENT',asset=purchase['asset'],race=base.fresh())
        self.assertLedger()

if __name__=='__main__':unittest.main(verbosity=2)
