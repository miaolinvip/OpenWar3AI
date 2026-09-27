"""No game process required: exercise the actual Python/PowerShell file boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import w3lease

NOW = 1_800_000_000_000
ADDR = 0x14109144
UNIT = SimpleNamespace(addr=ADDR, handle_lo=17, handle_hi=22)


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hurt.json"

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self):
        self.assertTrue(w3lease.write_deadlines(self.path, {ADDR: 108.0}, 100.0,
                                               1001, 3, [UNIT], now_ms=NOW))

    def read(self, now=NOW, pid=1001, epoch=3, units=None):
        return w3lease.read(self.path, pid, epoch, units or [UNIT], now_ms=now)

    def test_absolute_expiry_stopped_producer_never_renews(self):
        self.publish()
        original = self.path.read_bytes()
        for offset in (0, 3000, 7999):
            self.assertEqual(self.read(NOW + offset)[ADDR]["expiresAtMs"], NOW + 8000)
        self.assertEqual(self.read(NOW + 8000), {})
        self.assertEqual(self.read(NOW + 100_000), {})
        self.assertEqual(self.path.read_bytes(), original)

    def test_republish_same_monotonic_deadline_does_not_extend(self):
        self.publish()
        w3lease.write_deadlines(self.path, {ADDR: 108.0}, 105.0, 1001, 3, [UNIT], now_ms=NOW + 5000)
        self.assertEqual(self.read(NOW + 5000)[ADDR]["expiresAtMs"], NOW + 8000)
        self.assertEqual(self.read(NOW + 8000), {})

    def test_blocking_command_before_publication_does_not_extend_deadline(self):
        with patch.object(w3lease.time, 'monotonic', return_value=107.0), \
             patch.object(w3lease, 'epoch_ms', return_value=NOW + 7000):
            w3lease.write_deadlines(self.path, {ADDR: 108.0}, 100.0, 1001, 3, [UNIT])
        self.assertEqual(self.read(NOW + 7000)[ADDR]['expiresAtMs'], NOW + 8000)
        with patch.object(w3lease.time, 'monotonic', return_value=109.0), \
             patch.object(w3lease, 'epoch_ms', return_value=NOW + 9000):
            w3lease.write_deadlines(self.path, {ADDR: 108.0}, 100.0, 1001, 3, [UNIT])
        self.assertEqual(self.read(NOW + 9000), {})

    def test_wrong_process_match_and_generation_are_rejected(self):
        self.publish()
        self.assertEqual(self.read(pid=1002), {})
        self.assertEqual(self.read(epoch=4), {})
        reused = SimpleNamespace(addr=ADDR, handle_lo=17, handle_hi=23)
        self.assertEqual(self.read(units=[reused]), {})
        self.assertEqual(w3lease.read(self.path, 1001, 3, [], now_ms=NOW), {})
        self.assertEqual(self.read(units=[SimpleNamespace(addr=ADDR)]), {})

    def test_clear_legacy_partial_unknown_and_invalid_files(self):
        self.publish()
        w3lease.write(self.path, {}, 1001, 3, now_ms=NOW)
        self.assertEqual(self.read(), {})
        for text in ('{"123":8}', '{"schema":1,', '{}', '[]', '{"schema":2}'):
            self.path.write_text(text)
            self.assertEqual(self.read(), {})
        self.path.unlink()
        self.assertEqual(self.read(), {})

    def test_future_clock_and_unbounded_deadlines_fail_open(self):
        for written, expires in ((NOW + 3000, NOW + 8000), (NOW, NOW + 60001)):
            self.path.write_text(json.dumps({"schema": 1, "gamePid": 1001, "epoch": "3",
                "writtenAtMs": written, "units": {str(ADDR): {"expiresAtMs": expires,
                    "handleLo": 17, "handleHi": 22}}}))
            self.assertEqual(self.read(), {})

    def test_atomic_replacement_never_exposes_partial_json(self):
        self.publish()
        errors = []
        completed = []
        def produce():
            for i in range(80):
                completed.append(w3lease.write_deadlines(self.path, {ADDR: 108.0}, 100.0,
                                                         1001, 3, [UNIT], now_ms=NOW))
        thread = threading.Thread(target=produce)
        thread.start()
        while thread.is_alive():
            try:
                with w3lease._open_read(self.path) as stream:
                    self.assertEqual(json.load(stream)["schema"], 1)
            except Exception as exc:
                errors.append(repr(exc))
        thread.join()
        # Windows may reject replace while another handle is opening. The helper
        # returns False after bounded retries; the previous complete file remains.
        self.assertTrue(any(completed))
        self.assertEqual(errors, [])
        self.assertTrue(w3lease.write_deadlines(self.path, {ADDR: 108.0}, 100.0,
                                               1001, 3, [UNIT], now_ms=NOW))
        self.assertEqual(self.read(NOW + 8000), {})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "module imports use Windows API declarations")
    def test_actual_combat_scavenge_publishers_use_same_envelope(self):
        import mod_combat
        import mod_scavenge
        solo = self.path.parent / "solo.json"
        pickup = self.path.parent / "pickup.json"
        with patch.object(mod_combat, "HURTF", str(self.path)), \
             patch.object(mod_combat, "SOLOF", str(solo)), \
             patch.object(mod_scavenge, "PICKF", str(pickup)), \
             patch.object(w3lease.time, "monotonic", return_value=100.0), \
             patch.object(w3lease, "epoch_ms", return_value=NOW):
            mod_combat.publish_hurt({ADDR: 108.0}, 100.0, 1001, 3, [UNIT])
            mod_combat.publish_solo({ADDR: 110.0}, 100.0, 1001, 3, [UNIT])
            # ⚠ busy 的值在 a2bed6e（背包满就别再派）之后从 (item, at) 变成
            #   (item, at, lease) —— 租期改成按距离算，不再是全局那个 LEASE。
            #   这个测试没跟着改，于是 publish_pickup 解包直接 ValueError。
            #   本文件的老账：**同一条规则写在两处，只改一处等于没改。**
            #   这里钉住脚边那档（walk_lease(0) == LEASE == 6.0 秒 = 下面的 6000 毫秒）。
            self.assertEqual(mod_scavenge.walk_lease(0.0), mod_scavenge.LEASE)
            mod_scavenge.publish_pickup({ADDR: (9, 100.0, mod_scavenge.LEASE)},
                                        100.0, 1001, 3, [UNIT])
        for path, ttl in ((self.path, 8000), (solo, 10000), (pickup, 6000)):
            entries = w3lease.read(path, 1001, 3, [UNIT], now_ms=NOW)
            self.assertEqual(entries[ADDR]["expiresAtMs"], NOW + ttl)
            self.assertEqual(w3lease.read(path, 1001, 3, [UNIT], now_ms=NOW + ttl), {})

    # OpenWar3 不带旧的 PowerShell AI（war3lease.ps1 是它的租约读法），这条互通测试只在旧仓库有意义
    @unittest.skip("PowerShell 旧 AI 的租约读法不在 OpenWar3 里")
    def test_powershell_reader_agrees_on_expiry_identity_and_legacy(self):
        self.publish()
        unit = {"addr": f"0x{ADDR:08X}", "handleLo": "0x00000011", "handleHi": "0x00000016"}
        legacy = self.path.parent / "legacy.json"
        legacy.write_text(json.dumps({str(ADDR): 8}))
        cases = []
        def case(name, offset=0, pid=1001, epoch=3, current=None, path=None, probe_offset=None):
            cases.append(dict(name=name, path=str(path or self.path), gamePid=pid,
                epoch=str(epoch), nowMs=NOW + offset, units=[current or unit],
                probe=current or unit, probeAtMs=NOW + (offset if probe_offset is None else probe_offset)))
        case("live")
        case("read_again", 7000)
        case("expires_exactly", 8000)
        case("producer_stopped", 100_000)
        case("cached_entry_expires", probe_offset=8000)
        case("cached_generation")
        cases[-1]["probe"] = {**unit, "handleHi": 23}
        case("old_pid", pid=1002)
        case("old_match", epoch=4)
        case("reused_address", current={**unit, "handleHi": 23})
        case("legacy", path=legacy)
        casesfile = self.path.parent / "cases.json"
        casesfile.write_text(json.dumps(cases))
        run = subprocess.run([shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile",
            "-File", str(Path(__file__).with_name("test_lease.ps1")),
            "-Helper", str(ROOT.parent / "war3-button-hook-lab" / "war3lease.ps1"),
            "-Cases", str(casesfile)], text=True, capture_output=True, timeout=25)
        self.assertEqual(run.returncode, 0, run.stderr)
        actual = json.loads(run.stdout)
        for name in ("live", "read_again"):
            self.assertEqual(actual[name], {"keys": [unit["addr"]], "active": True})
        self.assertEqual(actual["cached_entry_expires"], {"keys": [unit["addr"]], "active": False})
        self.assertEqual(actual["cached_generation"], {"keys": [unit["addr"]], "active": False})
        for name in ("expires_exactly", "producer_stopped", "old_pid", "old_match", "reused_address", "legacy"):
            self.assertEqual(actual[name], {"keys": [], "active": False}, name)


if __name__ == "__main__":
    unittest.main()
