# -*- coding: utf-8 -*-
"""头顶气泡客户端 w3speak 的打包：必须和 DLL 的 War3BubbleEntry（ui_control_protocol.h）逐字段对上。"""
import os
import re
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import w3speak as W  # noqa: E402

HEADER_H = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        'native', 'src', 'ui_control_protocol.h')
C_TYPES = {'DWORD': 'I', 'float': 'f', 'WORD': 'H', 'short': 'h', 'BYTE': 'B'}


def read_header() -> str:
    with open(HEADER_H, encoding='utf-8') as f:
        return f.read()


def c_struct_format(name: str) -> str:
    """从 C 头文件里读出结构体的字段类型序列，转成 struct 格式串（只认本协议用到的五种类型）。"""
    src = read_header()
    body = re.search(r'struct\s+' + name + r'\s*\{(.*?)\};', src, re.S).group(1)
    fmt = '<'
    for line in body.splitlines():
        code = line.split('//')[0].strip()
        if not code:
            continue
        m = re.match(r'(\w+)\s+(\w+)(?:\[(\d+)\])?\s*;', code)
        fmt += C_TYPES[m.group(1)] * int(m.group(3) or 1)
    return fmt


def entries(payload: bytes):
    magic, ver, count, _, _ = struct.unpack_from(W.HEADER_FMT, payload, 0)
    off, out = W.HEADER_SIZE, []
    for _ in range(count):
        f = struct.unpack_from(W.ENTRY_FMT, payload, off)
        size, name_len, text_len, font_len = f[0], f[-4], f[-3], f[-2]
        s = off + W.ENTRY_SIZE
        name = payload[s:s + name_len].decode('utf-8')
        text = payload[s + name_len:s + name_len + text_len].decode('utf-8')
        font = payload[s + name_len + text_len:s + name_len + text_len + font_len].decode('utf-8')
        out.append((f, name, text, font))
        off += size
    return magic, ver, count, out, off


@unittest.skipUnless(os.path.exists(HEADER_H), "没有 native/ 的头文件")
class LayoutTest(unittest.TestCase):
    def test_entry_matches_c_header(self):
        self.assertEqual(c_struct_format('War3BubbleEntry'), W.ENTRY_FMT.replace(' ', ''))
        self.assertEqual(c_struct_format('War3BubbleBatchHeader'), W.HEADER_FMT.replace(' ', ''))

    def test_constants_match_c_header(self):
        src = read_header()
        for cname, value in [('kWar3BubbleMagic', W.MAGIC), ('kWar3BubbleAnchorWorld', W.F_ANCHOR_WORLD),
                             ('kWar3BubbleAnchorScreen', W.F_ANCHOR_SCREEN), ('kWar3BubbleUpdate', W.F_UPDATE),
                             ('kWar3BubbleRemove', W.F_REMOVE), ('kWar3BubbleNoTail', W.F_NO_TAIL),
                             ('kWar3BubbleNoShadow', W.F_NO_SHADOW), ('kWar3BubbleNoFade', W.F_NO_FADE),
                             ('kWar3BubbleLift', W.F_LIFT), ('kWar3BubbleVersion', W.VERSION),
                             ('kWar3BubbleTuneAvoidOn', W.TUNE_AVOID_ON),
                             ('kWar3BubbleTuneAvoidOff', W.TUNE_AVOID_OFF)]:
            m = re.search(cname + r'\s*=\s*(0x[0-9A-Fa-f]+|\d+)', src)
            self.assertEqual(int(m.group(1), 0), value, cname)


class PackTest(unittest.TestCase):
    def test_unit_bubble_with_style(self):
        b = W.Bubble(unit=0x14DF2614, name='山丘之王', text='跟我冲！', duration_ms=5000,
                     style=W.Style(font_px=26, text_color='#FFE080', bg_color='#C0102040', border_px=1.5))
        [payload] = W.pack_batches([b])
        magic, ver, count, [(f, name, text, font)], end = entries(payload)
        self.assertEqual((magic, ver, count), (W.MAGIC, W.VERSION, 1))
        size, flags, unit, key, x, y, lift, ox, oy, dur, type_ms, font_px, name_px, maxw, pad, radius, border, \
            tc, nc, bg, bc, sc, side, tail_len, opacity, _, _, _, nl, tl, fl, _ = f
        self.assertEqual(size % 4, 0)
        self.assertEqual((side, tail_len, opacity), (0, -1, 255))   # 默认：不偏、尾巴自动、不透明
        self.assertEqual(end, len(payload))
        self.assertEqual(flags, 0)
        self.assertEqual(unit, 0x14DF2614)
        self.assertEqual(dur, 5000)
        self.assertEqual(type_ms, 0xFFFFFFFF)          # 没给 = 用 DLL 默认
        self.assertEqual((font_px, name_px, maxw, pad, radius, border), (26, 0, 0, -1, -1, 15))
        self.assertEqual((tc, nc, bg), (0xFFFFE080, 0, 0xC0102040))
        self.assertEqual((name, text, font), ('山丘之王', '跟我冲！', ''))
        self.assertEqual((nl, tl), (len('山丘之王'.encode()), len('跟我冲！'.encode())))

    def test_anchors_and_flags(self):
        [p] = W.pack_batches([
            W.Bubble(world=(-4750, 2650), key=7, text='这里'),
            W.Bubble(screen=(960, 120), key=1, text='旁白', style=W.Style(tail=False, shadow=False, fade=False, type_ms=0)),
            W.Bubble(unit=0x1000, text='抬高', lift=300, update=True),
            W.Bubble(unit=0x1000, remove=True),
        ])
        _, _, count, rows, _ = entries(p)
        self.assertEqual(count, 4)
        world, screen, lifted, removed = (r[0] for r in rows)
        self.assertEqual(world[1], W.F_ANCHOR_WORLD)
        self.assertEqual((world[3], world[4], world[5]), (7, -4750.0, 2650.0))
        self.assertEqual(screen[1], W.F_ANCHOR_SCREEN | W.F_NO_TAIL | W.F_NO_SHADOW | W.F_NO_FADE)
        self.assertEqual(screen[10], 0)                 # type_ms=0 = 一次出全
        self.assertEqual(lifted[1], W.F_LIFT | W.F_UPDATE)
        self.assertEqual(lifted[6], 300.0)
        self.assertEqual(removed[1], W.F_REMOVE)

    def test_side_tail_opacity(self):
        [p] = W.pack_batches([
            W.Bubble(unit=0x1000, text='右偏', style=W.Style(side_px=120, tail_len_px=30, opacity=0.5)),
            W.Bubble(unit=0x2000, text='左偏', style=W.Style(side_px=-5000, tail_len_px=9999, opacity=0.0)),
            W.Bubble(unit=0x3000, text='自动', style=W.Style(tail_len_px=None, opacity=None)),
        ])
        _, _, _, rows, _ = entries(p)
        right, left, auto = ([r[0][22], r[0][23], r[0][24]] for r in rows)
        self.assertEqual(right, [120, 30, 128])          # 0.5 x 255 = 127.5 -> 128
        self.assertEqual(left, [-1600, 600, 1])          # 夹住；全透明给 1（0 在协议里 = 没填 = 不透明）
        self.assertEqual(auto, [0, -1, 255])

    def test_exactly_one_anchor(self):
        with self.assertRaises(ValueError):
            W.pack_entry(W.Bubble(text='没锚点'))
        with self.assertRaises(ValueError):
            W.pack_entry(W.Bubble(unit=1, screen=(1, 2), text='两个锚点'))
        with self.assertRaises(ValueError):
            W.pack_entry(W.Bubble(unit=1, text=''))

    def test_batches_split_under_scratch(self):
        many = [W.Bubble(unit=0x1000 + i, name='农民%d' % i, text='我是第 %d 个，' % i + '字' * 60) for i in range(40)]
        payloads = W.pack_batches(many)
        self.assertGreater(len(payloads), 1)
        total = 0
        for p in payloads:
            self.assertLessEqual(len(p), W.SCRATCH_BYTES)
            _, _, count, rows, end = entries(p)
            self.assertEqual(end, len(p))
            total += count
            for f, name, text, _ in rows:
                self.assertEqual(name, '农民%d' % (f[2] - 0x1000))
        self.assertEqual(total, 40)


class ParseTest(unittest.TestCase):
    def test_colors(self):
        self.assertEqual(W.color('#fff'), 0xFFFFFFFF)
        self.assertEqual(W.color('#112233'), 0xFF112233)
        self.assertEqual(W.color('#80112233'), 0x80112233)
        self.assertEqual(W.color('rgba(1,2,3,0.5)'), 0x80010203)     # 0.5 x 255 = 127.5 -> 128
        self.assertEqual(W.color('rgb(255, 0, 0)'), 0xFFFF0000)
        self.assertEqual(W.color('gold'), 0xFFFFD060)
        self.assertEqual(W.color(None), 0)
        self.assertEqual(W.color(0x12345678), 0x12345678)
        with self.assertRaises(ValueError):
            W.color('不是颜色')

    def test_from_dict(self):
        b = W.Bubble.from_dict({'unit': '0x14DF2614', 'name': '大法师', 'text': '暴风雪！',
                                'style': {'font_px': 30, 'name_color': 'cyan'}})
        self.assertEqual(b.unit, 0x14DF2614)
        self.assertEqual(b.style.font_px, 30)
        b2 = W.Bubble.from_dict({'screen': [960, 100], 'key': 3, 'text': '旁白'})
        self.assertEqual(b2.screen, (960, 100))
        with self.assertRaises(ValueError):
            W.Bubble.from_dict({'unit': 1, 'text': 'x', 'colour': 'red'})
        with self.assertRaises(ValueError):
            W.Style.from_dict({'fontsize': 20})


if __name__ == '__main__':
    unittest.main()
