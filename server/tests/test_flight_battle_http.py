"""Actual loopback HTTP flight battle settlement with disposable website profiles."""
import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from math import isqrt
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_horse_results as fixture

class FlightBattleHTTP(unittest.TestCase):
    setUpClass=classmethod(fixture.HorseResults.setUpClass.__func__)
    tearDownClass=classmethod(fixture.HorseResults.tearDownClass.__func__)
    request=fixture.HorseResults.request
    values=fixture.HorseResults.values
    def setUp(self):
        fixture.HorseResults.setUp(self)
        with fixture.fixture_connect(fixture.DB) as db:
            db.execute('UPDATE player_profiles SET points=9,balance_cents=654 WHERE uid=10001')
        self.event={**self.event,'game':'flight','score':14,'difficulty':5}
    def test_winning_and_losing_reports_use_the_same_personal_curve(self):
        for win in (True,False):
            for difficulty in range(6):
                for score in (0,1,14,56,10000):
                    event={**self.event,'session':str(uuid4()),'win':win,'score':score,'difficulty':difficulty}
                    self.assertEqual((200,{'ok':True,'credited':True,'points':isqrt(score*100//14)}),self.request(event))
        self.assertEqual((7+12*(0+2+10+20+267),321,60),self.values())
    def test_winning_concurrent_retries_credit_once_and_changed_win_conflicts(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.request(self.event),range(16)))
        self.assertTrue(all(status==200 and body['points']==10 for status,body in results))
        self.assertEqual(1,sum(body['credited'] for _,body in results))
        self.assertEqual(400,self.request({**self.event,'win':False})[0])
        self.assertEqual((17,321,1),self.values())
    def test_personal_rewards_and_profiles_are_independent(self):
        self.assertEqual(10,self.request(self.event)[1]['points'])
        self.assertEqual(20,self.request({**self.event,'uid':10001,'win':False,'score':56})[1]['points'])
        self.assertEqual((17,321,1),self.values());self.assertEqual((29,654,1),self.values(10001))
        status,body=self.request(path='/api/internal/game/admission/10000')
        self.assertEqual((200,'17'),(status,body['points']))
        self.assertEqual({'platformAdmin':False,'gameOpLevel':0},fixture.main.platform_store.permissions(10000))
    def test_service_credential_required_even_for_an_authenticated_browser(self):
        for win in (True,False):
            for key in (None,'wrong-qa-key'):
                self.assertEqual(401,self.request({**self.event,'win':win},key=key,cookie=self.cookies[10000])[0])
                self.assertEqual(401,self.request({**self.event,'uid':10001,'win':win},key=key,cookie=self.cookies[10000])[0])
        with patch.dict(os.environ,{'BMC_GAME_PLATFORM_ENABLED':'0'}):self.assertEqual(503,self.request(self.event)[0])
        self.assertEqual((7,321,0),self.values());self.assertEqual((9,654,0),self.values(10001))
    def test_authoritative_uid_types_bounds_and_existing_profile(self):
        for uid in (True,'10000',10000.0,None,9999,10000000000000000):
            self.assertEqual(400,self.request({**self.event,'uid':uid})[0])
        self.assertEqual(409,self.request({**self.event,'uid':10002})[0])
        self.assertEqual(200,self.request({**self.event,'uid':9999999999999999})[0])
        self.assertEqual((17,321,1),self.values(9999999999999999));self.assertEqual((7,321,0),self.values())
    def test_strict_win_score_difficulty_and_time_bounds(self):
        for key,values in [('win',[1,'true',None]),('score',[True,-1,10001,1.5,'14',None]),('difficulty',[True,-1,6,1.5,'5',None]),('seconds',[True,-1,1000000001,1.5,'60',None])]:
            for value in values:self.assertEqual(400,self.request({**self.event,key:value})[0],(key,value))
        self.assertEqual((7,321,0),self.values())
        for seconds in (0,1000000000):self.assertEqual(200,self.request({**self.event,'seconds':seconds,'session':str(uuid4())})[0])
    def test_no_injected_reward_identity_or_permissions_fields(self):
        for field in ('points','subject','winner','actorUid','balance_cents','platformAdmin','gameOpLevel'):
            self.assertEqual(400,self.request({**self.event,field:999})[0])
        for session in (uuid4().hex,str(uuid4()).upper(),'00000000-0000-1000-8000-000000000000',None):
            self.assertEqual(400,self.request({**self.event,'session':session})[0])
        self.assertEqual(413,self.request(raw=b'x'*4097)[0])
        self.assertEqual((7,321,0),self.values())
    def test_configured_curve_no_win_bonus_no_ten_cap_and_bounded_maximum(self):
        with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_SCORE':'7','BMC_FLIGHT_REWARD_REFERENCE_POINTS':'8'}):
            for win in (True,False):
                self.assertEqual(8,self.request({**self.event,'win':win,'score':7,'session':str(uuid4())})[1]['points'])
        with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_SCORE':'1','BMC_FLIGHT_REWARD_REFERENCE_POINTS':'100'}):
            self.assertEqual(10000,self.request({**self.event,'score':10000,'session':str(uuid4())})[1]['points'])
        self.assertEqual((10023,321,3),self.values())
    def test_ledger_reopen_and_curve_change_keep_original_reward(self):
        self.assertEqual(200,self.request(self.event)[0])
        from app.player_platform import PlatformStore
        previous=fixture.main.platform_store;fixture.main.platform_store=PlatformStore(fixture.DB)
        try:
            with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_POINTS':'99'}):
                self.assertEqual((200,{'ok':True,'credited':False,'points':10}),self.request(self.event))
        finally:fixture.main.platform_store=previous
        self.assertEqual((17,321,1),self.values())
    def test_other_changed_replays_are_rejected(self):
        self.request(self.event)
        for key,value in [('score',56),('difficulty',4),('seconds',61)]:
            self.assertEqual(400,self.request({**self.event,key:value})[0])
        self.assertEqual((17,321,1),self.values())
    def test_zero_contribution_winner_and_loser_are_recorded_without_points(self):
        for win in (True,False):
            self.assertEqual((200,{'ok':True,'credited':True,'points':0}),self.request({**self.event,'score':0,'win':win,'session':str(uuid4())}))
        self.assertEqual((7,321,2),self.values())
    def test_atomic_failure_retries_without_partial_credit(self):
        with fixture.fixture_connect(fixture.DB) as db:db.execute("CREATE TRIGGER flight_qa_reject BEFORE UPDATE ON player_profiles BEGIN SELECT RAISE(ABORT,'synthetic QA'); END")
        try:
            self.assertEqual(500,self.request(self.event)[0]);self.assertEqual((7,321,0),self.values())
        finally:
            with fixture.fixture_connect(fixture.DB) as db:db.execute('DROP TRIGGER flight_qa_reject')
        self.assertEqual(200,self.request(self.event)[0]);self.assertEqual((17,321,1),self.values())

if __name__=='__main__':
    if '--reproduce' in sys.argv:
        suite=unittest.TestSuite([FlightBattleHTTP('test_winning_and_losing_reports_use_the_same_personal_curve')])
    else:suite=unittest.defaultTestLoader.loadTestsFromTestCase(FlightBattleHTTP)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    fixture.TEMP.cleanup()
    receipt={'success':result.wasSuccessful(),'tests':result.testsRun,'actualLoopbackHTTP':True,'actualCurrentAppAndSQLite':True,'productionMutation':False,'httpRequests':len(fixture.REQUESTS),'responseStatuses':{str(code):sum(r['status']==code for r in fixture.REQUESTS) for code in sorted({r['status'] for r in fixture.REQUESTS})},'temporaryDatabaseRemoved':not fixture.DB.exists(),'ownHTTPServerExited':not FlightBattleHTTP.thread.is_alive(),'scope':'Synthetic local HTTP settlement; server credential plus existing website UID. No new game-side identity admission test.'}
    output=os.environ.get('FLIGHT_BATTLE_QA_RECEIPT')
    if output:Path(output).write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt));sys.exit(0 if result.wasSuccessful() else 1)
