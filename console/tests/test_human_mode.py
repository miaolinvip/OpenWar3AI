import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import instances as I, common as C
from test_schemes_switch import st


class HumanMode(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.world = st(5)
        self.stack.enter_context(patch.object(C, "inst_dir", side_effect=lambda n: os.path.join(self.tmp, f"inst{n}")))
        self.stack.enter_context(patch.object(I, "status", side_effect=lambda n: [self.world]))
        self.stack.enter_context(patch.object(I, "game_problem", return_value=None))
        self.spawn = self.stack.enter_context(patch.object(I, "spawn_detached", return_value={"pid": 42, "rc": 0}))

    def select(self):
        return I.write_next_game(5, {"matchMode": I.HUMAN_MODE})

    def test_selection_resets_scheme_speed_and_director(self):
        I.write_next_game(5, {"scheme": "builtin/hello", "speed": 300, "director": True, "difficulty": 4})
        g = self.select()
        self.assertEqual((g["matchMode"], g["speed"], g["difficulty"]), (I.HUMAN_MODE, 100, 2))
        self.assertNotIn("director", g)
        self.assertEqual(I.scheme_of(5), "builtin/xwar3")
        self.assertEqual(I.write_next_game(5, {"race": 2})["matchMode"], I.HUMAN_MODE)

    def test_running_instance_cannot_change_mode(self):
        self.world = st(5, game=123, farm=True)
        with self.assertRaisesRegex(ValueError, "停止实例"):
            self.select()
        self.assertEqual(I.next_game(5), {})

    def test_human_mode_uses_reference_supervisor_with_mode_argument(self):
        self.select()
        I.start_ai(5)
        cmd = self.spawn.call_args.args[0]
        self.assertIn("run_brain.py", cmd)
        self.assertIn("--match-mode human_vs_openwar3ai", cmd)
        self.assertNotIn("run_scheme.py", cmd)

    def test_incompatible_scheme_is_rejected_before_stopping_ai(self):
        self.select()
        with patch.object(I, "stop_ai") as stop:
            with self.assertRaisesRegex(ValueError, "builtin/xwar3"):
                I.switch_scheme(5, "builtin/hello")
        stop.assert_not_called()
        self.spawn.assert_not_called()

    def test_old_ai_or_wrong_supervisor_is_rejected_before_farm(self):
        self.select()
        for running in (st(5, brain=[100]), dict(st(5), oldAi=[{"pid": 100}]), st(5, bot=[100])):
            self.world = running
            self.assertFalse(I.start_test(5)["ok"])
        self.spawn.assert_not_called()

    def test_director_cannot_be_enabled_from_batch_or_settings(self):
        self.select()
        self.assertFalse(I.start_test(5, director=True)["ok"])
        with self.assertRaises(ValueError):
            I.write_next_game(5, {"director": True})
        self.spawn.assert_not_called()

    def test_stale_takeover_status_is_hidden(self):
        self.select()
        path = os.path.join(C.inst_dir(5), "takeover.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": 123, "phase": "running"}, f)
        self.assertIsNone(I._takeover(5, 456))
        self.assertEqual(I._takeover(5, 123)["phase"], "running")
        self.assertIsNone(I._takeover(5, 123, []))
        self.assertIsNone(I._takeover(5, 123, [456]))


if __name__ == "__main__":
    unittest.main()
