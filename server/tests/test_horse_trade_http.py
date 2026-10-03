from pathlib import Path
import json,sys,tempfile,unittest,uuid,os,hmac
from fastapi import FastAPI,HTTPException,Request
from fastapi.testclient import TestClient
import test_horse_trading as unit

class Router(unittest.TestCase):
    def setUp(self):
        self.db=unit.Commerce();self.db.setUp();self.key=uuid.uuid4().hex+uuid.uuid4().hex
        os.environ['BMC_HORSE_TRADING_ENABLED']='1'
        def guard(request:Request):
            if not hmac.compare_digest(request.headers.get('x-muxi-server-key',''),self.key):raise HTTPException(401,'Invalid game service credential')
        self.app=FastAPI();self.app.include_router(unit.trade.horse_trading_router(self.db.path,guard));self.client=TestClient(self.app);self.headers={'x-muxi-server-key':self.key}
    def tearDown(self):self.client.close();self.db.tearDown()
    def post(self,action,payload,op=None,headers=None):
        return self.client.post('/api/internal/game/horse-trades',headers=self.headers if headers is None else headers,json=dict(uid=10000,realm=self.db.realm,operation=op or unit.fresh(),action=action,payload=payload))
    def test_router_auth_size_schema_default_off_and_recovery(self):
        self.assertEqual(self.post('quote',dict(kind='BUY',sku='winx'),headers={}).status_code,401)
        self.assertEqual(self.client.post('/api/internal/game/horse-trades',headers=self.headers,content='x'*8193).status_code,413)
        self.assertEqual(self.client.post('/api/internal/game/horse-trades',headers=self.headers,content='invalid').status_code,409)
        q=self.post('quote',dict(kind='BUY',sku='winx'));self.assertEqual(q.status_code,200);self.assertEqual(q.json()['amount'],250)
        op=unit.fresh();r=self.post('buy',dict(quote=q.json()['quote']),op);self.assertEqual(r.status_code,200)
        self.assertTrue(self.post('buy',dict(quote=q.json()['quote']),op).json()['replayed'])
        os.environ['BMC_HORSE_TRADING_ENABLED']='0'
        self.assertEqual(self.post('quote',dict(kind='BUY',sku='winx')).status_code,403)
        self.assertEqual(self.post('grant_cancel',dict(asset=r.json()['asset'],parent=op)).status_code,200)
        market=self.client.get('/api/internal/game/horse-market/10000',params={'realm':self.db.realm},headers=self.headers)
        self.assertEqual(market.status_code,200);self.assertFalse(market.json()['enabled']);self.assertEqual(market.json()['balance'],'10000');self.db.assertLedger()

if __name__=='__main__':unittest.main(verbosity=2)
