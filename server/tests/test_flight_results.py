import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_minigame_results as baseline

class FlightRewards(unittest.TestCase):
    setUp=baseline.Results.setUp
    tearDown=baseline.Results.tearDown
    values=baseline.Results.values
    def flight(self,score=14):return {**self.event,'game':'flight','win':False,'score':score,'difficulty':5}
    def test_curve_no_ten_cap(self):
        self.assertEqual([0,10,20,70],[self.store.flight_contribution_points(n) for n in (0,14,56,700)])
    def test_configured_curve(self):
        with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_SCORE':'7','BMC_FLIGHT_REWARD_REFERENCE_POINTS':'8'}):self.assertEqual(8,self.store.flight_contribution_points(7))
    def test_flight_atomic_replay(self):
        event=self.flight()
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:self.store.credit_game_result(event),range(16)))
        self.assertEqual(1,sum(r['credited'] for r in results));self.assertEqual((17,321),self.values())
        with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_POINTS':'99'}):self.assertEqual({'credited':False,'points':10},self.store.credit_game_result(event))
    def test_anti_farm_bounds(self):
        for key,value in [('win',True),('score',10001),('score',-1),('score',True),('difficulty',6)]:
            with self.assertRaises(ValueError):self.store.credit_game_result({**self.flight(),key:value})
        for value in ('0','abc','10001'):
            with patch.dict(os.environ,{'BMC_FLIGHT_REWARD_REFERENCE_SCORE':value}):
                with self.assertRaises(ValueError):self.store.flight_contribution_points(14)
    def test_conflict_and_independent_personal_rewards(self):
        event=self.flight();self.store.credit_game_result(event)
        with self.assertRaises(ValueError):self.store.credit_game_result({**event,'score':56})
        from test_minigame_results import fixture_connect
        with fixture_connect(self.path) as db:db.execute("INSERT INTO player_profiles VALUES('second',10001,9,654,'')")
        self.assertEqual(20,self.store.credit_game_result({**event,'uid':10001,'score':56})['points'])
        with fixture_connect(self.path) as db:self.assertEqual([(10000,17,321),(10001,29,654)],db.execute('SELECT uid,points,balance_cents FROM player_profiles ORDER BY uid').fetchall())

if __name__=='__main__':unittest.main(verbosity=2)
