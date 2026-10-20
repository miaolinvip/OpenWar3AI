import os
import contextlib
import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_brain as R
import openwar3


class HumanTakeover(unittest.TestCase):
    def setUp(self):
        active = patch.object(R, "human_game_active", return_value=True)
        active.start()
        self.addCleanup(active.stop)

    def game(self):
        dev = Mock()
        dev.snapshot.return_value = SimpleNamespace(in_game=True, clock=2.0)
        dev.player_slots.return_value = [
            dict(me=True, human=True, state="playing", controller="user", ally=True),
            dict(me=False, human=False, state="playing", controller="computer", ally=False),
        ]
        dev.jass.Player.return_value = 42
        return dev

    def test_every_new_game_pauses_computer_and_releases_dev_lane(self):
        dev = self.game()
        with patch.object(openwar3, "Game", return_value=dev), patch.object(R, "pid_alive", return_value=True), \
                patch.object(R, "read_pid", side_effect=[101, 102]), patch.object(R, "takeover_status") as status, \
                patch.object(R.os.path, "exists", return_value=False):
            R.prepare_human_match(5, 101)
            R.prepare_human_match(5, 102)
        self.assertEqual(dev.jass.PauseCompAI.call_count, 2)
        dev.jass.PauseCompAI.assert_called_with(42, True)
        self.assertEqual(dev.close.call_count, 2)
        self.assertEqual([c.args[2] for c in status.call_args_list], ["waiting", "pausing", "paused"] * 2)

    def test_wrong_slots_and_pause_failure_are_explicit(self):
        for wrong_slots in (True, False):
            with self.subTest(wrong_slots=wrong_slots):
                dev = self.game()
                if wrong_slots:
                    dev.player_slots.return_value[1]["controller"] = "user"
                else:
                    dev.jass.PauseCompAI.side_effect = RuntimeError("native rejected")
                with patch.object(openwar3, "Game", return_value=dev), patch.object(R, "pid_alive", return_value=True), \
                        patch.object(R, "read_pid", return_value=101), patch.object(R, "takeover_status"), \
                        patch.object(R.os.path, "exists", return_value=False):
                    with self.assertRaises(RuntimeError):
                        R.prepare_human_match(5, 101)
                dev.close.assert_called_once()
                if wrong_slots:
                    dev.jass.PauseCompAI.assert_not_called()

    def test_stop_or_changed_pid_cancels_before_pause(self):
        for stopped in (True, False):
            with patch.object(openwar3, "Game") as make, patch.object(R, "pid_alive", return_value=True), \
                    patch.object(R, "read_pid", return_value=102), patch.object(R, "takeover_status"), \
                    patch.object(R.os.path, "exists", return_value=stopped):
                with self.assertRaises(RuntimeError):
                    R.prepare_human_match(5, 101)
                make.assert_not_called()

    def test_child_environment_resets_between_modes(self):
        with patch.dict(os.environ):
            self.assertEqual(R.set_env("example", 101, player=1)["WAR3_CONTROL_PLAYER"], "1")
            self.assertNotIn("WAR3_CONTROL_PLAYER", R.set_env("example", 102))
            self.assertNotIn("WAR3_CONTROL_PLAYER", os.environ)

    def test_pause_failure_does_not_start_reflex_or_close_human_game(self):
        from brain import ops
        import modlog
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ), patch.object(R, "BIN", d), \
                patch.object(R, "prepare_human_match", side_effect=RuntimeError("pause failed")), \
                patch.object(R, "start_modules") as start, patch.object(R, "takeover_status") as state, \
                patch.object(ops, "load", return_value=ops.DEFAULTS), patch.object(ops, "kill_game") as kill, \
                patch.object(R, "pid_alive", return_value=True), patch.object(modlog, "tee_stdout"):
            self.assertEqual(R.play_match(5, 101, 1, 40, R.HUMAN_MODE), "crash")
            start.assert_not_called()
            kill.assert_not_called()
            state.assert_called_with(5, 101, "failed", "pause failed")

    def test_source_reload_keeps_mode(self):
        args = SimpleNamespace(inst=5, max_minutes=40, hold_since=1, match_mode=R.HUMAN_MODE)
        with patch.object(R, "newest_source", return_value=0), patch.object(R, "held_since", return_value=False), \
                patch.object(R._k32, "ReleaseMutex"), patch.object(R._k32, "CloseHandle"), \
                patch.object(R.subprocess, "Popen") as spawn:
            R.respawn(1, args, R.time.monotonic() + 100, 2, 101)
        cmd = spawn.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--match-mode") + 1], R.HUMAN_MODE)


import test_play_match_smoke as smoke


class HumanMatchSmoke(smoke.TestPlayMatchSmoke):
    def setUp(self):
        super().setUp()
        active = patch.object(R, "human_game_active", return_value=True)
        active.start()
        self.addCleanup(active.stop)

    def test_one_match_end_to_end(self):
        # Run the real strategy loop as P1; the human buildings belong to P0.
        for u in self.game.mine + self.game.foe_town + self.game.foe_peons:
            if u.owner < 12:
                u.owner = 1 - u.owner
        original = self.game.snap
        def snap():
            s = original()
            s.me, s.gold, s.lumber = 1, 250, 75
            return s
        self.game.snap = snap
        order = []
        out = io.StringIO()
        with patch.object(R, "set_env", return_value={}), \
                patch.object(R, "prepare_human_match", side_effect=lambda *a: order.append("pause")), \
                patch.object(R, "start_modules", side_effect=lambda *a: order.append("reflex") or []), \
                patch.object(R, "takeover_status") as state, contextlib.redirect_stdout(out):
            outcome = R.play_match(77, smoke.FAKE_PID, 1, 40, R.HUMAN_MODE)
        self.assertEqual(order, ["pause", "reflex"])
        self.assertEqual(outcome, "victory", out.getvalue()[-3000:])
        self.assertNotIn("一局中途出错", out.getvalue())
        self.assertEqual([c.args[2] for c in state.call_args_list], ["running", "ended"])
        folder = os.path.join(self.inst_dir, "matches")
        path = os.path.join(folder, next(f for f in os.listdir(folder) if f.endswith(".jsonl")))
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        result = next(r["data"] for r in rows if r["kind"] == "result")
        experiment = next(r["data"] for r in rows if r["kind"] == "experiment")
        self.assertEqual(result["enemy"], "human")
        self.assertEqual(experiment["controlledPlayer"], 1)

    def test_long_background_wait_keeps_same_match_and_resumes(self):
        polls = []
        def active(pid):
            if self.game.t < 5:
                return True
            polls.append(pid)
            return len(polls) > 35
        with patch.object(R, "human_game_active", side_effect=active):
            # Same real play loop as the P1 smoke test; 35 inactive polls outlast the old 15s watchdog.
            self.test_one_match_end_to_end()
        self.assertGreater(len(polls), 35)

    def test_frozen_clock_and_stale_reads_do_not_end_human_match(self):
        original = smoke.FakeClock(self.game).read
        reads = []
        def clock_read():
            if self.game.t >= 10 and len(reads) < 35:
                reads.append(self.game.t)
                return self.game.t
            return original()
        with patch.object(smoke.fightodds, "GameClock", return_value=SimpleNamespace(read=clock_read)):
            self.test_one_match_end_to_end()
        self.assertEqual(len(reads), 35)

    def test_missing_snapshots_do_not_end_human_match(self):
        blank = []
        def read():
            if self.game.t >= 10 and len(blank) < 35:
                blank.append(self.game.t)
                return None
            return self.game.snap()
        session = SimpleNamespace(cmd=self.game.cmd, read=read)
        with patch.object(smoke.w3state, "Session", return_value=session):
            self.test_one_match_end_to_end()
        self.assertEqual(len(blank), 35)

    def test_strategy_failure_does_not_close_human_game(self):
        from brain.runtime import Brain
        from brain import ops
        with open(os.path.join(self.inst_dir, "ops.json"), "w", encoding="utf-8") as f:
            json.dump({"kill_game_after_end": True}, f)
        with patch.object(R, "prepare_human_match"), patch.object(R, "set_env", return_value={}), \
                patch.object(R, "takeover_status"), patch.object(Brain, "step", side_effect=RuntimeError("lane suspended")), \
                patch.object(self.game.cmd, "action") as end, patch.object(ops, "kill_game") as kill, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(R.play_match(77, smoke.FAKE_PID, 1, 40, R.HUMAN_MODE), "crash")
        end.assert_not_called()
        kill.assert_not_called()

    def test_stall_requires_actual_defeat_or_victory_evidence(self):
        self.assertIsNone(R.confirmed_human_end(None))
        self.assertIsNone(R.confirmed_human_end(dict(my_buildings=2, enemy_buildings=1, my_halls=0, own=1, foe=100)))
        self.assertEqual(R.confirmed_human_end(dict(my_buildings=0, enemy_buildings=1)), "defeated")
        self.assertEqual(R.confirmed_human_end(dict(my_buildings=1, enemy_buildings=0)), "victory")


if __name__ == "__main__":
    unittest.main()
