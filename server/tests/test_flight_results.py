"""Flight training reuses the existing result ledger; no reviewed reward policy exists yet."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_minigame_results as baseline

class FlightResults(unittest.TestCase):
    setUp=baseline.Results.setUp
    tearDown=baseline.Results.tearDown
    values=baseline.Results.values
    def event_for_flight(self):return {**self.event,'game':'flight','win':False,'score':0,'difficulty':0}
    def test_training_uses_existing_ledger_idempotently_with_zero_points(self):
        event=self.event_for_flight()
        self.assertEqual({'credited':True,'points':0},self.store.credit_game_result(event,self.policy))
        self.assertEqual({'credited':False,'points':0},self.store.credit_game_result(event,self.policy))
        self.assertEqual((7,321),self.values())
    def test_training_cannot_claim_win_score_difficulty_or_reward_policy(self):
        for key,value in [('win',True),('score',1),('difficulty',1)]:
            with self.assertRaises(ValueError):self.store.credit_game_result({**self.event_for_flight(),key:value},self.policy)
        with self.assertRaises(ValueError):self.store.credit_game_result(self.event_for_flight(),{'flight':{'0':100}})
    def test_training_and_existing_game_sessions_remain_independent(self):
        event=self.event_for_flight();self.store.credit_game_result(event)
        self.store.credit_game_result({**self.event,'session':event['session']},self.policy)
        self.assertEqual((10,321),self.values())
if __name__=='__main__':unittest.main(verbosity=2)
