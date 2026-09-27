# -*- coding: utf-8 -*-
"""远见界面多语言（09-25）：源码里每个 tx("中文") 键、后端发来的固定标签，在每种语言字典里都有译文、占位符对得上；
i18n.ts 的语言表和 src/i18n/*.json 一一对应（加语言漏了哪一半都会在这里红）；英文游戏名表覆盖中文名表。"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import i18n_check  # noqa: E402


class I18nTest(unittest.TestCase):
    def test_every_key_translated(self):
        keys = i18n_check.all_keys()           # 前端 tx("…") + 后端固定标签（配置 schema、日志源名、导播预设…）
        self.assertGreater(len(keys), 1000)
        self.assertGreater(len(i18n_check.backend_keys()), 100)
        for lang, table in i18n_check.dictionaries().items():
            p = i18n_check.problems(keys, table)
            self.assertEqual(p["missing"], [], f"{lang} 缺译文（python tools/i18n_check.py --todo {lang}）")
            self.assertEqual(p["placeholders"], [], f"{lang} 占位符 {{0}}… 和原文对不上")
            self.assertEqual(p["han"], [], f"{lang} 译文里还夹着中文")

    def test_languages_registered(self):
        src = _read(os.path.join(i18n_check.WEB_SRC, "lib", "i18n.ts"))
        listed = set(re.findall(r'\{ code: "(\w+)"', src)) - {"zh"}
        self.assertEqual(listed, set(i18n_check.dictionaries()), "LANGS 里的语言和 src/i18n/*.json 对不上")
        for code in listed:
            self.assertIn(f'import {code} from "../i18n/{code}.json"', src)

    def test_no_raw_chinese_in_jsx_text(self):
        """JSX 里直接写的中文文字（没包 tx）会在英文界面露出来。只查 JSX 文本节点这种最明显的写法。"""
        rx = re.compile(r">\s*([^<>{}\n]*[一-鿿][^<>{}\n]*)\s*<")
        bad = []
        for path, rel in _sources():
            text = _read(path)
            code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
            code = re.sub(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`', '""', code)   # 字符串里的不算
            bad += [f"{rel}: {m.group(1).strip()[:40]}" for m in rx.finditer(code)]
        self.assertEqual(bad, [])


class GameNamesTest(unittest.TestCase):
    def test_english_names_cover_chinese(self):
        sys.path.insert(0, os.path.join(ROOT, "console"))
        from server import common as C  # noqa: E402
        zh, en = C.names(), C.names_en()
        self.assertEqual(set(zh) - set(en), set(), "names_en.json 少了 names.json 里的码")
        han = re.compile(r"[一-鿿]")
        self.assertEqual([c for c, v in en.items() if han.search(v)], [])
        self.assertEqual((en["hfoo"], en["Hamg"], en["AHbz"]), ("Footman", "Archmage", "Blizzard"))
        m = C.meta("en")
        self.assertEqual(m["names"]["hfoo"], "Footman")
        self.assertEqual(m["nameMap"]["步兵"], "Footman")
        self.assertEqual(C.meta("zh")["names"]["hfoo"], "步兵")
        self.assertNotIn("nameMap", C.meta("zh"))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _sources():
    for base, _dirs, files in os.walk(i18n_check.WEB_SRC):
        for f in files:
            if f.endswith(".tsx"):
                p = os.path.join(base, f)
                yield p, os.path.relpath(p, i18n_check.WEB_SRC)


if __name__ == "__main__":
    unittest.main()
