import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_brain as R


class PersistentGameNumber(unittest.TestCase):
    def test_recovers_largest_number_from_counter_history_and_argument(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            matches = root / "matches"
            matches.mkdir()
            (matches / "match-20260919-120000-011-aabbccdd.jsonl").touch()
            (matches / "match-20260920-130000-1007-11223344.jsonl").touch()
            (root / R.GAME_SEQUENCE_FILE).write_text(json.dumps({"last": 900}), encoding="utf-8")
            self.assertEqual(R.initial_game_no(d), 1007)
            self.assertEqual(R.initial_game_no(d, 1200), 1200)

    def test_reservation_is_persisted_before_match(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(R.reserve_game_no(d, 41), 42)
            saved = json.loads((pathlib.Path(d) / R.GAME_SEQUENCE_FILE).read_text(encoding="utf-8"))
            self.assertEqual(saved["last"], 42)
            self.assertEqual(R.reserve_game_no(d, 0), 43)


if __name__ == "__main__":
    unittest.main()
