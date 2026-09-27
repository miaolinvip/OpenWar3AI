# -*- coding: utf-8 -*-
"""快车道客户端 w3fast：协议常量和头文件逐项对上；用一个"假 DLL"线程驱动完整的提交-排空-取结果流程。

假 DLL 照运行时快车道的规则写：比较 submitSeq/drainSeq -> 执行 Submitted 的槽 ->
结果先写、状态后写 -> 推进 drainSeq -> SetEvent。它不碰游戏，只按操作码写一张假回执：
  q_tech 回 value[0] = code + extra（code = FAULT 时模拟异常）；cam_state 在 scratch+64 写一份镜头状态；
  观战车道发下令类命令回 status 8。
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import struct
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import w3fast as F  # noqa: E402

HEADER_H = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "native", "src", "ui_control_protocol.h")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR]
_k32.CreateFileMappingW.restype = wt.HANDLE
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
_k32.CreateEventW.restype = wt.HANDLE
INVALID_HANDLE = wt.HANDLE(-1)


@unittest.skipUnless(os.path.exists(HEADER_H), "没有 native/ 的头文件")
class HeaderTest(unittest.TestCase):
    def test_constants_match_header(self):
        src = open(HEADER_H, encoding="utf-8").read()

        def const(name):
            m = re.search(rf"{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", src)
            self.assertIsNotNone(m, name)
            return int(m.group(1), 0)
        self.assertEqual(const("kWar3FastMagic"), F.MAGIC)
        self.assertEqual(const("kWar3FastVersion"), F.VERSION)
        self.assertEqual(const("kWar3FastLanes"), F.LANES)
        self.assertEqual(const("kWar3FastSlotsPerLane"), F.SLOTS)
        self.assertEqual(const("kWar3FastScratchBytes"), F.SCRATCH)
        self.assertEqual((const("kWar3FastFree"), const("kWar3FastSubmitted"), const("kWar3FastDone")),
                         (F.FREE, F.SUBMITTED, F.DONE))

    def test_slot_field_order_matches_header(self):
        src = open(HEADER_H, encoding="utf-8").read()
        body = re.search(r"struct War3FastSlot \{(.*?)\n\};", src, re.S).group(1)
        body = re.sub(r"//[^\n]*", "", body)
        fields = re.findall(r"(?:volatile\s+)?(?:LONG|DWORD|unsigned char)\s+(\w+)(\[\w+\])?;", body)
        names = [f[0] for f in fields]
        self.assertEqual(len(names), 14)
        self.assertEqual(names[:6], ["state", "tag", "target", "convention", "argCount", "flags"])
        self.assertEqual(names[9:], ["returnValue", "faulted", "args", "deadlineMs", "scratch"])
        # 偏移按字段顺序算出来（全是 4 字节，args 12 个）；第 7~9 个字语义命令写 0（S_MASK / S_RSV0 / S_RSV1）
        offs, o = [], 0
        for n in names:
            offs.append(o)
            o += 48 if n == "args" else (0 if n == "scratch" else 4)
        self.assertEqual(offs, [F.S_STATE, F.S_TAG, F.S_TARGET, F.S_CONV, F.S_ARGC, F.S_FLAGS, F.S_MASK, F.S_RSV0,
                                F.S_RSV1, F.S_RET, F.S_FAULTED, F.S_ARGS, F.S_DEADLINE, F.S_SCRATCH])


    def test_lane_field_order_matches_header(self):
        src = open(HEADER_H, encoding="utf-8").read()
        body = re.search(r"struct War3FastLane \{(.*?)\n\};", src, re.S).group(1)
        body = re.sub(r"//[^\n]*", "", body)
        names = re.findall(r"(?:volatile\s+)?(?:LONG|DWORD)\s+(\w+)(?:\[\w+\])?;", body)
        self.assertEqual(names, ["ownerPid", "submitSeq", "drainSeq", "served", "role", "player", "refused", "reserved"])
        self.assertEqual((F.L_OWNER, F.L_SUBMIT, F.L_DRAIN, F.L_ROLE, F.L_PLAYER, F.L_REFUSED),
                         (0, 4, 8, 16, 20, 24))
        self.assertEqual(F.LANE_SIZE, 32)


class TextTest(unittest.TestCase):
    def test_chunks_keep_utf8_whole(self):
        s = "我方兵力 12 对 9，去打野" * 20
        parts = F.text_chunks(s, 40)
        self.assertTrue(all(len(p) <= 40 for p in parts))
        self.assertEqual(b"".join(parts).decode("utf-8"), s)
        self.assertEqual(F.clip_utf8_bytes("一二三", 7).decode("utf-8"), "一二")

    def test_command_puts_text_after_receipt(self):
        c = F.command("hud_quest", extra=2, mode=0, text="标题")
        self.assertEqual((c.op, c.conv), (F.OP["hud_quest"], 0))
        self.assertEqual(c.scratch[F.TEXT_OFF:], "标题".encode("utf-8") + b"\0")
        self.assertEqual(c.args[9], 2)
        c = F.command("cam_look", x=1.0, y=2.0, fargs={4: 0.5, 6: 1})
        self.assertEqual(struct.unpack("<f", struct.pack("<I", c.args[4]))[0], 0.5)
        self.assertEqual(c.args[6], 1)


class FakeDll:
    """在本进程里建 Local\\War3Fast_<pid> 并按运行时快车道的规则排空。"""

    def __init__(self, pid, paused=False):
        self.pid = pid
        self.h = _k32.CreateFileMappingW(INVALID_HANDLE, None, 0x04, 0, F.BLOCK_SIZE, f"Local\\War3Fast_{pid}")
        p = F._k32.MapViewOfFile(self.h, F.FILE_MAP_ALL, 0, 0, F.BLOCK_SIZE)   # 用声明过 restype 的那份，否则 64 位地址被截断
        self.p = p
        self.buf = (ctypes.c_char * F.BLOCK_SIZE).from_address(p)
        ctypes.memset(p, 0, F.BLOCK_SIZE)
        self.ev = [_k32.CreateEventW(None, False, False, f"Local\\War3FastDone_{pid}_{i}") for i in range(F.LANES)]
        for off, v in ((F.H_VERSION, F.VERSION), (F.H_LANES, F.LANES), (F.H_SLOTS, F.SLOTS),
                       (F.H_SLOT_SIZE, F.SLOT_SIZE), (F.H_MAGIC, F.MAGIC)):
            struct.pack_into("<I", self.buf, off, v)
        self.paused = paused
        self.stop = False
        self.executed = 0
        self.drains = 0
        self.slots_seen = []           # 每条执行过的槽：(flags, convention, mask, 保留字 0, 保留字 1)
        self.t = threading.Thread(target=self.loop, daemon=True)
        self.t.start()

    def u32(self, off):
        return struct.unpack_from("<I", self.buf, off)[0]

    def put(self, off, v):
        struct.pack_into("<I", self.buf, off, v & 0xFFFFFFFF)

    def loop(self):
        while not self.stop:
            time.sleep(0.002)          # 事件分发每秒数百次
            if self.paused:
                continue
            for lane in range(F.LANES):
                lo = F.HDR_SIZE + F.LANE_SIZE * lane
                sub = self.u32(lo + F.L_SUBMIT)
                if sub == self.u32(lo + F.L_DRAIN):
                    continue
                self.drains += 1
                role = self.u32(lo + F.L_ROLE)
                for s in range(F.SLOTS):
                    off = F.SLOTS_OFF + F.SLOT_SIZE * (lane * F.SLOTS + s)
                    if self.u32(off + F.S_STATE) != F.SUBMITTED:
                        continue
                    dl = self.u32(off + F.S_DEADLINE)
                    if dl and ctypes.c_int32((_k32.GetTickCount() - dl) & 0xFFFFFFFF).value > 0:
                        self.put(off + F.S_FAULTED, 2)
                    else:
                        self.execute(off, role)
                    self.put(off + F.S_STATE, F.DONE)
                self.put(lo + F.L_DRAIN, sub)
                _k32.SetEvent(self.ev[lane])

    def receipt(self, off, status, value0=0):
        struct.pack_into("<5I", self.buf, off + F.S_SCRATCH, status, 1, 0xFFFFFFFF, 0, 0)
        struct.pack_into("<I", self.buf, off + F.S_SCRATCH + 20, value0)

    def execute(self, off, role):
        self.executed += 1
        flags = self.u32(off + F.S_FLAGS)
        self.slots_seen.append((flags, self.u32(off + F.S_CONV), self.u32(off + F.S_MASK),
                                self.u32(off + F.S_RSV0), self.u32(off + F.S_RSV1)))
        if not flags & F.SEMANTIC:
            self.put(off + F.S_FAULTED, 1)             # 客户端只该发语义命令
            return
        op = self.u32(off + F.S_TARGET)
        args = struct.unpack_from("<12I", self.buf, off + F.S_ARGS)
        if role == F.ROLE_OBSERVER and op in ORDER_OPS:
            self.receipt(off, 8)                        # forbidden（车道角色不允许）
        elif op == F.OP["q_tech"] and args[3] == FAULT:
            self.put(off + F.S_FAULTED, 1)              # 执行时异常被接住
            self.receipt(off, 4)
        elif op == F.OP["q_tech"]:
            self.receipt(off, 0, args[3] + args[9])
        elif op == F.OP["cam_state"]:
            self.receipt(off, 0)
            F._CAMSTATE.pack_into(self.buf, off + F.S_SCRATCH + F.CAMSTATE_OFF, *CAM)
        else:
            self.receipt(off, 0)

    def close(self):
        self.stop = True
        self.t.join(1)


FAULT = 0xDEAD
ORDER_OPS = {F.OP[k] for k in ("point", "target", "immediate", "build")}
CAM = (1, 0, 3, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 1650.0, 5000.0, -0.5, 1.2, 0.0, 1.57, 0.0,
       -100.0, 100.0, -200.0, 200.0, 7.0, 8.0, 0xFF000000, 0, 1.0, 3000.0, 5000.0, 2.0)


def q(code, extra=0):
    return F.command("q_tech", code=code, extra=extra)


class LaneTest(unittest.TestCase):
    def setUp(self):
        self.pid = 900000 + os.getpid() % 50000
        self.dll = FakeDll(self.pid)

    def tearDown(self):
        self.dll.close()

    def test_single_command(self):
        lane = F.FastLane(self.pid)
        r = lane.commands([q(5, 2)])[0]
        self.assertTrue(r.ok)
        self.assertEqual((r.op, r.values[0]), (F.OP["q_tech"], 7))
        # 槽里：flags = 语义命令，convention / mask / 两个保留字都是 0
        self.assertEqual(self.dll.slots_seen[-1], (F.SEMANTIC, 0, 0, 0, 0))
        with self.assertRaises(ValueError):
            lane.run([F.Call(F.OP["hud_quest"], args=[0] * 11, scratch=bytes(F.SCRATCH + 1))])
        lane.close()

    def test_batch_runs_all_in_one_submit(self):
        lane = F.FastLane(self.pid)
        before = self.dll.drains
        rs = lane.commands([q(i, 1) for i in range(16)])
        self.assertEqual([r.values[0] for r in rs], [i + 1 for i in range(16)])
        self.assertEqual(self.dll.drains - before, 1)                  # 16 条一次排空
        # 超过 16 条自动拆批
        rs = lane.commands([q(i) for i in range(40)])
        self.assertEqual([r.values[0] for r in rs], list(range(40)))
        lane.close()

    def test_game_writes_back_into_scratch(self):
        """运行时写回本槽 scratch 的数据（镜头状态在 +64）原样读回。"""
        lane = F.FastLane(self.pid)
        r = lane.commands([F.command("cam_state")])[0]
        s = F.parse_cam_state(r.data)
        self.assertEqual((s["target"], s["fields"]["distance"], s["fog"]["k"]), ((1.0, 2.0, 3.0), 1650.0, 2.0))
        lane.close()

    def test_fault_is_reported_not_raised(self):
        lane = F.FastLane(self.pid)
        r = lane.run([q(FAULT)])[0]
        self.assertEqual(r.faulted, 1)
        self.assertFalse(r.ok)
        rec = lane.commands([q(FAULT)])[0]
        self.assertEqual((rec.ok, rec.status), (False, 4))
        lane.close()

    def test_two_clients_get_different_lanes(self):
        # 同一进程开两个对象也是两条车道，角色互不覆盖（09-23 实测：原来复用同一条，player=1 的车道把 dev 车道改了）
        a = F.FastLane(self.pid)
        b = F.FastLane(self.pid, role=F.ROLE_PLAYER, player=1)
        self.assertNotEqual(a.lane, b.lane)
        u32 = lambda off: struct.unpack_from("<I", self.dll.buf, off)[0]  # noqa: E731
        self.assertEqual(u32(F.HDR_SIZE + F.LANE_SIZE * a.lane + F.L_ROLE), F.ROLE_DEV)
        self.assertEqual((u32(F.HDR_SIZE + F.LANE_SIZE * b.lane + F.L_ROLE),
                          u32(F.HDR_SIZE + F.LANE_SIZE * b.lane + F.L_PLAYER)), (F.ROLE_PLAYER, 1))
        b.close()
        self.assertEqual(u32(F.HDR_SIZE + F.LANE_SIZE * a.lane + F.L_OWNER), os.getpid())   # 关 b 不动 a
        c = F.FastLane(self.pid, role=F.ROLE_OBSERVER)
        self.assertEqual(c.lane, b.lane)                                                     # 关掉的车道收回来用
        self.assertEqual(a.commands([q(2, 3)])[0].values[0], 5)
        # 观战车道：下令被拒（status 8），查询照常
        move, look = c.commands([F.command("point", 0x100, 0x000D0012, x=1.0, y=2.0), q(1)])
        self.assertEqual((move.status, look.ok), (8, True))
        c.close()
        a.close()

    def test_paused_game_times_out_and_never_executes_late(self):
        """游戏暂停：车道不排空 -> 超时（run 返回 None、回执 status 99）；恢复后那条过期的槽被判过期、不执行。"""
        self.dll.paused = True
        lane = F.FastLane(self.pid)
        t0 = time.perf_counter()
        self.assertIsNone(lane.run([q(1)], timeout=0.05)[0])
        r = lane.commands([q(1)], timeout=0.05)[0]
        self.assertEqual((r.ok, r.status), (False, 99))
        self.assertLess(time.perf_counter() - t0, 1.0)
        before = self.dll.executed
        self.dll.paused = False
        time.sleep(0.1)
        self.assertEqual(self.dll.executed, before, "过期的命令在恢复后被执行了")
        # 车道还能接着用（过期槽被收回）
        self.assertEqual(lane.commands([q(5, 6)])[0].values[0], 11)
        lane.close()


if __name__ == "__main__":
    unittest.main()
