"""P1 takeover must project resources and ownership without changing the shared local-player view."""
import os
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3cmd
import w3fast
import w3state
import w3world


def world():
    players = [w3world.Player(0, 9999, 999, 90, 100, 0, 0, 1, True),
               w3world.Player(1, 125, 40, 8, 12, 0, 0, 2, True)]
    return w3world.WorldState(1, 0, 10.0, 7, 0, True, 100, 50, 0, 0, 0, players, [], [])


class ControlPlayer(unittest.TestCase):
    def test_cmd_and_legacy_snapshot_use_p1_resources(self):
        raw = world()
        cmd = w3cmd.Cmd.with_backends(None, SimpleNamespace(read=lambda: raw), player=1)
        projected = cmd.snapshot()
        self.assertEqual((projected.me, projected.gold, projected.lumber, projected.food_used, projected.food_cap),
                         (1, 125, 40, 8, 12))
        self.assertEqual((raw.me, raw.gold), (0, 9999))
        session = w3state.Session(player=1)
        session.cmd = cmd
        legacy = w3state.Snapshot(0, 0, True, 1, 9999, 999, 90, 100, [], epoch=7)
        result = session._player_view(legacy)
        self.assertEqual((result.me, result.gold, result.lumber, result.food_used, result.food_cap), (1, 125, 40, 8, 12))
        self.assertIs(result.units, legacy.units)  # full-map observation retained
        self.assertEqual(legacy.me, 0)

    def test_missing_resources_or_different_match_never_falls_back_to_human_money(self):
        raw = world()
        cmd = w3cmd.Cmd.with_backends(None, SimpleNamespace(read=lambda: raw), player=1)
        session = w3state.Session(player=1)
        session.cmd = cmd
        legacy = w3state.Snapshot(0, 0, True, 1, 9999, 999, 90, 100, [], epoch=8)
        self.assertIsNone(session._player_view(legacy))
        raw.players[1].known = False
        self.assertIsNone(session._player_view(replace(legacy, epoch=7)))

    def test_identity_propagates_to_reconnecting_session(self):
        with patch.dict(os.environ, {"WAR3_CONTROL_PLAYER": "1"}), \
                patch.object(w3state, "State", return_value=SimpleNamespace(pid=123)), \
                patch.object(w3cmd, "Cmd") as make:
            session = w3state.Session()
            self.assertTrue(session._connect())
            make.assert_called_with(123, player=1)
            self.assertTrue(session._connect())
            self.assertEqual(session.player, 1)
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(w3state.Session().player)

    def test_shared_player_lane_does_not_repurpose_dev_lane(self):
        def make(pid, role=w3fast.ROLE_DEV, player=0):
            return SimpleNamespace(pid=pid, role=role, player=player, _p=1)
        with patch.dict(w3fast._SHARED, {}, clear=True), patch.object(w3fast, "FastLane", side_effect=make):
            dev = w3fast.shared_lane(123)
            ai = w3fast.shared_lane(123, player=1)
            self.assertIsNot(dev, ai)
            self.assertEqual((dev.role, ai.role, ai.player), (w3fast.ROLE_DEV, w3fast.ROLE_PLAYER, 1))
            self.assertIs(w3fast.shared_lane(123, player=1), ai)
            self.assertIs(w3fast.shared_lane(123), dev)


if __name__ == "__main__":
    unittest.main()
