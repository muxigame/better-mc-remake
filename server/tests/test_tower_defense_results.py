"""Tower defense uses the existing authenticated result endpoint and reward ledger."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from tests import test_horse_results as fixture


class TowerDefenseResults(unittest.TestCase):
    setUpClass = classmethod(fixture.HorseResults.setUpClass.__func__)
    tearDownClass = classmethod(fixture.HorseResults.tearDownClass.__func__)
    request = fixture.HorseResults.request
    values = fixture.HorseResults.values

    def setUp(self):
        fixture.HorseResults.setUp(self)
        self.event.update(game='tower_defense', score=210, difficulty=1, win=True)

    def test_default_records_without_inventing_points(self):
        self.assertEqual((200, {'ok': True, 'credited': True, 'points': 0}), self.request(self.event))
        self.assertEqual((7, 321, 1), self.values())

    def test_configured_reward_and_replay(self):
        with patch.dict(os.environ, {'BMC_MINIGAME_REWARDS_JSON': '{"tower_defense":{"1":4,"2":6,"3":8}}'}):
            self.assertEqual((200, {'ok': True, 'credited': True, 'points': 4}), self.request(self.event))
            self.assertEqual((200, {'ok': True, 'credited': False, 'points': 4}), self.request(self.event))
            self.assertEqual(400, self.request({**self.event, 'score': 205})[0])
            self.assertEqual(200, self.request({**self.event, 'session': str(uuid4()), 'win': False, 'score': 20})[0])
        self.assertEqual((11, 321, 2), self.values())

    def test_authentication_and_server_owned_score_bounds(self):
        self.assertEqual(401, self.request(self.event, key=None, cookie=self.cookies[10000])[0])
        for field, value in [('difficulty', 0), ('difficulty', 4), ('difficulty', True), ('score', 331), ('score', -1), ('score', True), ('points', 999)]:
            self.assertEqual(400, self.request({**self.event, field: value})[0], (field, value))
        self.assertEqual((7, 321, 0), self.values())

    def test_three_difficulties_use_existing_policy(self):
        with patch.dict(os.environ, {'BMC_MINIGAME_REWARDS_JSON': '{"tower_defense":{"1":4,"2":6,"3":8},"outbreak":{"1":2}}'}):
            for difficulty in (1, 2, 3):
                event = {**self.event, 'session': str(uuid4()), 'difficulty': difficulty, 'score': 270 + 20*difficulty}
                self.assertEqual((200, {'ok': True, 'credited': True, 'points': 2+2*difficulty}), self.request(event))
        self.assertEqual((25, 321, 3), self.values())


if __name__ == '__main__':
    unittest.main()
