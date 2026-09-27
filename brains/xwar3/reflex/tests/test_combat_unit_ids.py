# -*- coding: utf-8 -*-
"""毫秒层日志带血条编号：大脑发布的 unit_ids.json 里有的单位，「hfoo@14D4E0E4」打印成「#23 hfoo@14D4E0E4」。
补编号只有一份实现（war3-brain/brain/unitids.annotate），这里核对 mod_combat.note 真的用上了它。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mod_combat as C  # noqa: E402


class NoteCarriesUnitId(unittest.TestCase):
    def test_note_is_annotated_for_this_match_only(self):
        self.assertIsNotNone(C._annotate_ids, "brain.unitids 没导入成功 —— 日志不会带编号")
        with tempfile.TemporaryDirectory() as d:
            ids = os.path.join(d, "unit_ids.json")
            with open(ids, "w", encoding="utf-8") as fh:
                json.dump({"pid": 4242, "ids": {"14D4E0E4": 23}}, fh)
            log = os.path.join(d, "ai_run.log")
            out = io.StringIO()
            with mock.patch.object(C, "IDSF", ids), mock.patch.object(C, "LOGF", log), redirect_stdout(out):
                C._ids_cache.update(pid=4242, at=-1e9, ids={})
                C.note("hfoo@14D4E0E4 188/420 —— 拉出来")
                C._ids_cache.update(pid=999, at=-1e9, ids={})           # 换了一局：上一局的编号不认
                C.note("hfoo@14D4E0E4 188/420 —— 拉出来")
            lines = out.getvalue().splitlines()
            self.assertTrue(lines[0].endswith("#23 hfoo@14D4E0E4 188/420 —— 拉出来"), lines)
            self.assertTrue(lines[1].endswith(" hfoo@14D4E0E4 188/420 —— 拉出来") and "#23" not in lines[1], lines)


if __name__ == "__main__":
    unittest.main()
