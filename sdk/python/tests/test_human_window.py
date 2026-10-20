import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import w3fullscreen as F
import w3window as W


class HumanWindowTest(unittest.TestCase):
    def test_native_fullscreen_only_removes_two_window_arguments(self):
        source = Path(__file__).resolve().parents[3] / "prebuilt" / "war3_launch.exe"
        original = source.read_bytes()
        image = F.fullscreen_image(original)
        self.assertEqual(len(image), len(original))
        expected = set()
        for command in F.COMMANDS:
            position = original.index((command + "\0").encode("utf-16le"))
            start = position + command.index("-window") * 2
            expected.update(range(start, start + 14))
            self.assertIn((command.replace("-window", "       ") + "\0").encode("utf-16le"), image)
        actual = {i for i, (a, b) in enumerate(zip(original, image)) if a != b}
        self.assertTrue(actual)
        self.assertTrue(actual <= expected)
        self.assertEqual(hashlib.sha256(original).hexdigest(), F.LAUNCH_SHA256)

    def test_unknown_launcher_is_rejected_before_writing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "war3_launch.exe"
            source.write_bytes(b"unknown-release")
            with self.assertRaisesRegex(RuntimeError, "不支持此启动器版本"):
                F.fullscreen_launcher(str(source))
            self.assertFalse((Path(temp) / "human-fullscreen").exists())

    def test_separate_copy_keeps_original_and_runtime_and_reuses_existing_files(self):
        release = Path(__file__).resolve().parents[3] / "prebuilt" / "war3_launch.exe"
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "war3_launch.exe"
            source.write_bytes(release.read_bytes())
            runtime = Path(temp) / "render_hook.dll"
            runtime.write_bytes(b"unchanged-runtime")
            target = Path(F.fullscreen_launcher(str(source)))
            self.assertEqual(source.read_bytes(), release.read_bytes())
            self.assertEqual((target.parent / runtime.name).read_bytes(), runtime.read_bytes())
            self.assertEqual(target.read_bytes(), F.fullscreen_image(source.read_bytes()))
            stamp = target.stat().st_mtime_ns
            F.fullscreen_launcher(str(source))
            self.assertEqual(target.stat().st_mtime_ns, stamp)
            target.write_bytes(b"corrupt-copy")
            F.fullscreen_launcher(str(source))
            self.assertEqual(target.read_bytes(), F.fullscreen_image(source.read_bytes()))

    def test_minimized_or_background_game_is_inactive(self):
        def owner(_hwnd, result):
            result._obj.value = 123
        with patch.object(W._u32, "GetForegroundWindow", return_value=1), \
                patch.object(W._u32, "GetWindowThreadProcessId", side_effect=owner), \
                patch.object(W._u32, "IsIconic", return_value=0) as iconic:
            self.assertTrue(W.game_active(123))
            self.assertFalse(W.game_active(456))
            iconic.return_value = 1
            self.assertFalse(W.game_active(123))


if __name__ == "__main__":
    unittest.main()
