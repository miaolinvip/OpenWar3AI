"""Drive the real observation loop with fake sessions; no game is opened."""

import ctypes
import itertools
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import mod_spy as spy


def snap(epoch=1):
    return SimpleNamespace(in_game=True, epoch=epoch)


class FakeSession:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.pid = 0

    def read(self):
        try:
            self.pid, value = next(self.frames)
            return value
        except StopIteration:
            raise KeyboardInterrupt


class SpySessionTests(unittest.TestCase):
    def run_loop(self, frames, exited=False):
        watch = Mock()
        watch.exited.return_value = exited
        observed, flushed = [], []
        with patch.object(spy, "Session", return_value=FakeSession(frames)), \
             patch.object(spy, "_ProcessWatch", return_value=watch), \
             patch.object(spy, "observe", side_effect=lambda _s, m, _sn, _t: observed.append(m)), \
             patch.object(spy, "flush", side_effect=flushed.append), \
             patch.object(spy.time, "sleep"), \
             patch.object(spy.time, "monotonic", side_effect=itertools.count()), \
             patch.object(sys, "argv", ["mod_spy.py"]), \
             patch("builtins.print"):
            self.assertEqual(spy.main(), 0)
        return observed, flushed, watch

    def test_one_missing_snapshot_does_not_split_a_match(self):
        observed, flushed, watch = self.run_loop([(42, snap()), (42, None), (42, snap())])
        self.assertIs(observed[0], observed[1])
        self.assertEqual(flushed, [observed[0]])
        watch.open.assert_called_once_with(42)

    def test_same_pid_new_epoch_flushes_exactly_once(self):
        observed, flushed, _ = self.run_loop([(42, snap()), (42, None), (42, snap(2))])
        self.assertIsNot(observed[0], observed[1])
        self.assertEqual(flushed, observed)

    def test_new_pid_flushes_exactly_once(self):
        observed, flushed, _ = self.run_loop([(42, snap()), (43, snap())])
        self.assertIsNot(observed[0], observed[1])
        self.assertEqual(flushed, observed)

    def test_confirmed_process_exit_flushes_without_another_snapshot(self):
        observed, flushed, watch = self.run_loop([(42, snap()), (0, None), (0, None)], exited=True)
        self.assertEqual(flushed, observed)
        watch.exited.assert_called_once()

    def test_unknown_process_status_keeps_pending_observations(self):
        observed, flushed, _ = self.run_loop([(42, snap()), (0, None), (42, snap())], exited=None)
        self.assertIs(observed[0], observed[1])
        self.assertEqual(flushed, [observed[0]])


class ProcessWatchTests(unittest.TestCase):
    def test_wait_is_nonblocking_and_bound_to_retained_handle(self):
        api = Mock()
        api.OpenProcess.return_value = 91
        api.WaitForSingleObject.side_effect = [258, 0]
        with patch.object(spy.ctypes, "WinDLL", return_value=api):
            watch = spy._ProcessWatch()
            watch.open(42)
            self.assertFalse(watch.exited())
            self.assertTrue(watch.exited())
            watch.close()
        api.OpenProcess.assert_called_once_with(0x00100000, False, 42)
        self.assertEqual(api.WaitForSingleObject.call_args_list[0].args, (91, 0))
        api.CloseHandle.assert_called_once_with(91)

    def test_access_denied_is_unknown(self):
        api = Mock()
        api.OpenProcess.return_value = None
        with patch.object(spy.ctypes, "WinDLL", return_value=api), \
             patch.object(ctypes, "get_last_error", return_value=5):
            watch = spy._ProcessWatch()
            watch.open(42)
            self.assertIsNone(watch.exited())
        api.WaitForSingleObject.assert_not_called()


if __name__ == "__main__":
    unittest.main()
