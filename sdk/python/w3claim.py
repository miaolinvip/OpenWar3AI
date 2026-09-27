"""w3claim.py —— 单位归属登记：同一时刻一个单位只能有一个主人。

    from w3claim import Claims
    cl = Claims("scavenge")                  # 我是哪个模块
    if cl.take(unit.addr, seconds=6):        # 抢到了才准下命令
        issue_order(unit)
    cl.give_back(unit.addr)                  # 干完还回去

为什么必须有这一层
------------------
今晚最贵的几个 bug 全是同一个形状：**两条规则在抢同一个单位**。

  撤退把残血小兵拉走 -> 它任务完成变空闲 -> 进攻规则看它空闲又派回去
  -> 再撤 -> 再派 ...  同一个 ogru 被连拉六次，血 242 掉到 94，最后死了。

  捡装备每秒重下一次 getitem -> 每次都把上一次的寻路清零
  -> 英雄在装备旁边空转 30 秒，一步没走成。

在单进程里这已经是最难查的一类错；**拆成多进程之后会更糟** ——
两个模块互相看不见，谁也不知道对方刚给这个单位下过令。

所以并发的前提不是"通道够快"，而是**"所有权说得清"**：
    一个单位在同一时刻只能被一个模块指挥。

★ 为什么用带超时的租约，而不是永久锁
    模块会崩、会被 kill、会卡住。永久锁遇到这些情况就是**死锁**，
    而且是那种"一切看起来正常、就是没人动"的死锁 —— 今晚已经见过一次
    （伤员锁没有钥匙，把英雄按了 96 秒）。
    租约到期自动释放：最坏情况是几秒钟的指挥权真空，不是永久瘫痪。

★ 为什么用共享内存 + 命名互斥量
    登记表要跨进程，而且写很稀疏（一秒几次），读也很便宜。
    互斥量在这里没有性能问题 —— 它锁的是**模块之间**，
    不像游戏线程那条路，卡住谁都不会拖慢游戏画面。

⚠ 这张表挂在页面文件上，**最后一个句柄关闭时整张表就没了**。
  这不是 bug，是想要的行为（所有模块都退出了，残留的租约本来就该清掉）。
  但它有一个测试陷阱：拿两个 `python -c` 先后跑是**验不出来**的 ——
  第一个进程退出时表就销毁了，第二个创建的是全新的一份，
  于是"抢不到"的用例会假阳性通过。**必须让两个进程同时活着才算数。**
  （我第一次就是这么误判的，结论正好相反。）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import struct
import time

SLOTS = 512
# 每条：unitAddr(u32) owner(u32) expiresAt(u32, 毫秒, GetTickCount64 低 32 位)
#   owner 低 8 位 = 模块 id，第 8~15 位 = 优先级（09-16 统一仲裁）。
# ★ 09-16 时钟从 time.monotonic 改成 GetTickCount64：策略层（PowerShell）要读写同一张表，
#   .NET 的 Environment.TickCount64 就是它；Python 的 monotonic 在 Windows 上是另一个钟，两边比到期会差出天去。
#   w3claim.py 在模块重载的依赖名单里，同一实例的模块会在同一个局间一起换新，不会新旧钟混用。
_REC = "<3I"
_REC_SIZE = struct.calcsize(_REC)
_HDR_SIZE = 16                      # magic + version + count + reserved
_SIZE = _HDR_SIZE + SLOTS * _REC_SIZE
_MAGIC = 0x4D4C4333                 # '3CLM'

PAGE_READWRITE = 0x04
FILE_MAP_ALL = 0x000F001F

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileMappingW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                    wt.DWORD, wt.DWORD, wt.LPCWSTR]
_k32.CreateFileMappingW.restype = wt.HANDLE
_k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
_k32.CreateMutexW.restype = wt.HANDLE
_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_k32.ReleaseMutex.argtypes = [wt.HANDLE]

# 模块名 -> 数字 id。加新模块就往后加，**不要改已有的数字**
# （跨进程的登记表里存的是数字，改了含义就全错了）。
MODULES = {
    "strategy": 1,     # war3ai.ps1 那个老的单体，仍然是默认主人
    "scavenge": 2,     # 捡装备 + 空转收队
    "combat": 3,
    "economy": 4,
    "build": 5,
    "console": 6,      # Farsight 网页控制台（人手动下令）
    "bot": 7,          # OpenWar3 SDK（openwar3.Game）写的 bot
}

# ★★ 09-16 统一仲裁：同一个单位，**优先级高的能抢，同级和低级抢不走**（到期的谁都能接）。
#   「统一仲裁」是必须先解决的硬前提。原来：模块之间先到先得、只有撤退能 steal；
#   策略层根本不读这张表（它只读 hurt/solo/pickup/reserved 几个 JSON），派工两层同一秒各派一个。
#   数字越大越优先；调用方按「这条令是为了什么」给，不按模块给（同一个模块里保命和补攻击令差很远）：
PRIO = {
    "manual": 95,      # 人在 Farsight 控制台上手动下的令：租约期内所有 AI 层都让开
    "safety": 90,      # 保命：拉残血、整队撤退、孤身英雄撤回（mod_combat）
    "dodge": 85,       # 躲暴风雪
    "cast": 80,        # 英雄施法（引导期由 cast_hold 另管）
    "pickup": 70,      # 捡装备（mod_scavenge）
    "rotate": 60,      # 打野轮换
    "strategy_hold": 58,  # 策略层回防聚拢：去集合点等齐的兵（war3ai W3-Defend），补攻击令别抢
    "refill": 55,      # 补攻击令
    "strategy": 50,    # 策略层：交战/出击/防守/移动
    "strategy_eco": 45,  # 策略层派工（建造、伐木、调人回矿）
    "economy": 40,     # mod_worker 派工
}
DEFAULT_PRIO = {"strategy": PRIO["strategy"], "scavenge": PRIO["pickup"], "combat": PRIO["refill"],
                "economy": PRIO["economy"], "build": PRIO["strategy_eco"]}


_k32.GetTickCount64.restype = ctypes.c_uint64
_ID_MASK = 0xFFFF00FF            # owner 里「是谁」的部分：低 8 位模块号 + 高 16 位客户端标记（中间 8 位是优先级）


def _now_ms() -> int:
    return int(_k32.GetTickCount64()) & 0xFFFFFFFF


class Claims:
    def __init__(self, module: str, pid: int | None = None):
        if module not in MODULES:
            raise ValueError(f"未知模块 {module}，先在 MODULES 里登记")
        self.me = MODULES[module]
        self.name = module
        # ★ 09-26：SDK 写的程序都登记成 "bot"（7）—— 模组、MCP、网关每个会话、别人的 bot 同时在跑时，
        #   互相把对方的租约当成自己的，同一个单位两边都能下令。owner 的高 16 位原来空着，给 "bot" 加一个客户端标记：
        #   同是 bot 也分得清。别的模块（单进程）标记为 0，行为不变；PowerShell 那份只看低 16 位，也不受影响。
        if module == "bot":
            import itertools
            Claims._serial = getattr(Claims, "_serial", None) or itertools.count(1)
            self.tag = ((os.getpid() * 131 + next(Claims._serial)) % 0xFFFF) + 1
        else:
            self.tag = 0
        self.ident = self.me | (self.tag << 16)
        pid = pid or int(os.environ.get("WAR3_PID", "0"))
        suffix = str(pid) if pid else "shared"
        # CreateFileMapping 同名时返回已有的那一个 —— 谁先起谁创建，其余的接上。
        self._h = _k32.CreateFileMappingW(wt.HANDLE(-1), None, PAGE_READWRITE,
                                          0, _SIZE, f"Local\\War3Claims_{suffix}")
        if not self._h:
            raise RuntimeError("CreateFileMapping 失败")
        p = _k32.MapViewOfFile(self._h, FILE_MAP_ALL, 0, 0, _SIZE)
        if not p:
            raise RuntimeError("MapViewOfFile 失败")
        self._buf = (ctypes.c_char * _SIZE).from_address(p)
        self._mx = _k32.CreateMutexW(None, False, f"Local\\War3ClaimsMx_{suffix}")
        with self._lock():
            magic = struct.unpack_from("<I", self._buf, 0)[0]
            if magic != _MAGIC:
                ctypes.memset(ctypes.addressof(self._buf), 0, _SIZE)
                struct.pack_into("<II", self._buf, 0, _MAGIC, 1)

    class _Lock:
        def __init__(self, mx): self.mx = mx
        def __enter__(self):
            # 5 秒还拿不到就当没锁 —— 宁可短暂并发，也不要整个模块挂死在
            # 一个已经崩掉的进程留下的互斥量上。
            _k32.WaitForSingleObject(self.mx, 5000)
        def __exit__(self, *a):
            _k32.ReleaseMutex(self.mx)

    def _lock(self):
        return Claims._Lock(self._mx)

    def _slot(self, addr: int) -> int:
        return (addr >> 4) % SLOTS

    # ★★ 09-17 探测窗口必须**扫满 8 格**，不能遇到空格/过期格就停。
    #   原来 take 探到第 i 格「空或已过期」就地写入返回、lease_of/give_back 遇到空格就停 ——
    #   可同一个单位的**有效租约可能在后面一格**（它登记时前一格正被别的单位占着）。两条路都会绕过它：
    #     ① 前一格那个单位的租约过期（补攻击令只租 1.5 秒），策略层来抢，在前一格看到「过期」直接写入；
    #     ② 前一格那个单位被 give_back 清零（补攻击令下完令立刻归还），探测链被截断，后面的记录谁也看不见。
    #   实测（cmdclash resend_after_pull，09-17 00:0x 四个实例）：mod_combat 拉残血握 8 秒保命租约（90），
    #   而策略层（50）3~6 秒后照样把它 move_to 回去 —— 2 号 35 次拉残血里 18 次，1 号 7 次里 4 次。
    #   现在：先在 8 格里找这个单位的**全部**记录（旧版本可能留下重复的），有一条有效且挡着就让出；
    #   都不挡就写第一条、其余作废；没有记录才找空位。归还写墓碑（地址留着、到期 0），不挖洞。
    #   ⚠ war3drv.ps1 W3-ClaimTake / W3-ClaimLease 是同一条规则的 PowerShell 那一份，两边必须一起改。
    def _window(self, addr: int):
        """[(偏移, 地址, owner, 到期)] × 8，按探测顺序。调用方持锁。"""
        out = []
        for i in range(8):
            off = _HDR_SIZE + ((self._slot(addr) + i) % SLOTS) * _REC_SIZE
            out.append((off,) + struct.unpack_from(_REC, self._buf, off))
        return out

    def take(self, addr: int, seconds: float = 5.0, steal: bool = False, prio: int | None = None) -> bool:
        """抢这个单位的指挥权。抢到返回 True，被**同级或更高优先级**的别人占着返回 False。

        自己已经持有的可以续租（同一个模块反复调用是安全的，优先级按这次给的改写）。
        prio：见 PRIO；不给就按模块默认（DEFAULT_PRIO）。

        `steal=True` = prio 取保命级（90）。**只给撤退用** ——
        09-07 晚对局里总有一两个单位留在原地顶着一堆敌人阵亡，查下来一部分是这样：
        撤退时那个单位正被捡装备/打野轮换占着，`take` 返回 False，撤退就把它跳过了，
        于是整队走光、它留在原地。捡一件装备和一条命不是一个量级，撤退优先。
        """
        p = PRIO["safety"] if steal else int(prio if prio is not None else DEFAULT_PRIO.get(self.name, 50))
        now = _now_ms()
        exp = (now + int(seconds * 1000)) & 0xFFFFFFFF
        mine = self.ident | (max(0, min(255, p)) << 8)
        with self._lock():
            window = self._window(addr)              # 开放寻址，扫满 8 格（见 _window 上面那段）
            same = [r for r in window if r[1] == addr]
            for _off, _a, owner, expires in same:
                # ⚠ 到期的租约谁都能接手 —— 模块崩了不能把单位永远锁死。
                if (owner & _ID_MASK) != self.ident and expires > now and ((owner >> 8) & 0xFF) >= p:
                    self.yielded = getattr(self, "yielded", 0) + 1
                    return False
            if same:
                struct.pack_into(_REC, self._buf, same[0][0], addr, mine, exp)
                for off, *_ in same[1:]:              # 旧版本留下的重复记录作废（墓碑）
                    struct.pack_into(_REC, self._buf, off, addr, 0, 0)
                return True
            for off, a, _owner, expires in window:
                if a == 0 or expires <= now:
                    struct.pack_into(_REC, self._buf, off, addr, mine, exp)
                    return True
        return False                                  # 八格都满：让给别人，不硬抢

    def owner_of(self, addr: int) -> int:
        lease = self.lease_of(addr)
        return lease[0] if lease else 0

    def lease_of(self, addr: int) -> tuple[int, int, int] | None:
        """(模块 id, 优先级, 剩余毫秒)；没人占或已到期返回 None。"""
        now = _now_ms()
        best = None
        with self._lock():
            for _off, a, owner, expires in self._window(addr):   # 扫满 8 格；有重复取优先级最高的
                if a == addr and expires > now:
                    cand = ((owner & 0xFF), (owner >> 8) & 0xFF, expires - now)
                    if best is None or cand[1] > best[1]:
                        best = cand
        return best

    def mine(self, addr: int) -> bool:
        """这个单位现在是不是我（这个客户端，不只是同一个模块）持有。"""
        now = _now_ms()
        best = None
        with self._lock():
            for _off, a, owner, expires in self._window(addr):
                if a == addr and expires > now and (best is None or ((owner >> 8) & 0xFF) > ((best >> 8) & 0xFF)):
                    best = owner
        return best is not None and (best & _ID_MASK) == self.ident

    def give_back(self, addr: int) -> None:
        with self._lock():
            for off, a, owner, _exp in self._window(addr):
                if a == addr and (owner & _ID_MASK) == self.ident:
                    struct.pack_into(_REC, self._buf, off, addr, 0, 0)   # 墓碑：不挖洞（见 _window）

    def held(self) -> list[tuple[int, int, int]]:
        """当前所有有效租约，调试用。"""
        now = _now_ms()
        out = []
        with self._lock():
            for s in range(SLOTS):
                a, owner, expires = struct.unpack_from(
                    _REC, self._buf, _HDR_SIZE + s * _REC_SIZE)
                if a and expires > now:
                    out.append((a, owner & 0xFF, expires - now))
        return out


if __name__ == "__main__":
    a = Claims("scavenge")
    b = Claims("combat")
    U = 0x14109144
    print("scavenge 抢 U:", a.take(U, 2))          # True
    print("combat  再抢 U:", b.take(U, 2))          # False —— 被占着
    print("U 的主人:", a.owner_of(U), "(scavenge=2)")
    a.give_back(U)
    print("还回去之后 combat 抢 U:", b.take(U, 2))   # True
    print("租约到期后能不能被接手（等 2.2 秒）...")
    time.sleep(2.2)
    print("  scavenge 抢 U:", a.take(U, 2))         # True —— 到期自动释放
    a.give_back(U)
    print("剩余租约:", a.held())
