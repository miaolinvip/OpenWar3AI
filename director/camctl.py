# -*- coding: utf-8 -*-
"""镜头控制：给导播 / 战情台网页用的 Python 版。

★ 09-23 P3：全部走运行时的 W3P 协议 —— 镜头走 cam_* 语义命令（快车道，一帧内回执），单位列表 / 单位位置读
  W3P 世界块，迷雾问 q_visible。这里不直接碰引擎（以前每次都要起一次命令行，一次往返约 17~50 毫秒；
  现在一次约一帧），镜头相关的细节都在运行时里。
  唯一还起命令行的是头顶血条（运行时动作 50，带单位/坐标参数）—— 那是运行时自己的动作号。

用的是 observer 车道（W3P 角色：只能查询、读写镜头和界面，下不了任何单位命令）。

──────────────────────────────────────────────────────────────────────────
★ 安全边界：这里**只**能动镜头，不能下任何单位命令

镜头是这条线唯一值得破的例外，因为 War3 是锁步模型，镜头**纯粹是本地表现**，不进指令流、不影响任何单位 ——
点错了最多是看错地方。现在这条边界由运行时的车道角色守着（observer 车道下单位命令 = forbidden），
不再只靠这里自觉。

网页传过来的是**动作名字**。下面 OPS 那张表是白名单，网页永远碰不到表外的能力。
"""
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.path.dirname(HERE), 'bin', 'war3_ui_command.exe')
_SDK = os.path.join(os.path.dirname(HERE), 'sdk', 'python')
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

import w3fast   # noqa: E402
import w3world  # noqa: E402

# Windows 上不加这个，每次调用都会闪一个黑框
_NOWIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# 字段号（JASS SetCameraField 的编号；W3P cam_field 命令的 code）
F_DISTANCE, F_FARZ, F_PITCH, F_FOV, F_ROLL, F_YAW, F_ZOFFSET = 0, 1, 2, 3, 4, 5, 6
_FIELD_NAME = dict(enumerate(w3fast.CAM_FIELDS))     # 0 distance 1 farz 2 aoa 3 fov 4 roll 5 rotation 6 zoffset

# 读镜头用的名字（导播 read_f(N_TARGET_X) 这类老调用照旧能用）
N_TARGET_X, N_TARGET_Y, N_TARGET_Z = 'target_x', 'target_y', 'target_z'
N_EYE_X, N_EYE_Y, N_EYE_Z = 'eye_x', 'eye_y', 'eye_z'

DEFAULTS = {'distance': 1650.0, 'pitch': 304.0, 'yaw': 90.0,
            'fov': 70.0, 'roll': 0.0, 'zOffset': 0.0, 'farZ': 5000.0}

DEG = 180.0 / 3.141592653589793


# ---------------------------------------------------------------------------
# 进程与车道
# ---------------------------------------------------------------------------

def pick_rows(tasklist_csv, inst=''):
    """tasklist CSV -> [(实例号串, pid 串)]，按实例号排序。inst 给了（'2'）就只留 War3-2.exe 那一个。"""
    rows = re.findall(r'"War3(?:-(\d+))?\.exe","(\d+)"', tasklist_csv, re.I)
    if inst.isdigit():
        rows = [r for r in rows if r[0] == str(int(inst))]
    rows.sort(key=lambda r: int(r[0] or 0))
    return rows


class _Ctl(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.pid = 0
        self.pid_at = 0.0
        self._warned_multi = 0   # 多开时"认错主人"的警告只打一次（见 find_pid）
        self._lane = None        # (pid, FastLane)
        self._world = None       # (pid, World)
        self._ui = 0             # 上一次见到的界面代数（换局 +1）
        self.rotate_mode = False
        self.last_error = None

    # War3 的进程号。缓存 5 秒 —— tasklist 一次要几十毫秒，而拖滑块的时候每秒会来十几次。
    #
    # ★ 多开：先认 WAR3_PID，再退回"挑第一个"（和 w3state._find_war3 同一条规矩）。
    # 09-16 打通多开之后，"正则取第一个 War3.exe"变成了一个**静默认错主人**的 bug：
    # 导播只对第一个实例生效，另外五个的镜头高度一直是游戏默认的 1650。
    # 这里不自动挑"某一个"实例：该管哪一局必须显式说（WAR3_PID），或者钉实例号（WAR3_INST）。
    def find_pid(self):
        now = time.time()
        if self.pid and now - self.pid_at < 5.0:
            return self.pid
        env_pid = os.environ.get('WAR3_PID', '').strip()
        if env_pid.isdigit() and int(env_pid) > 0:
            self.pid = int(env_pid)
            self.pid_at = now
            return self.pid
        # ★ 09-16 每个实例一个导播：跑批时每局结束 farm 会重起游戏、pid 每局都换，所以导播不能钉 WAR3_PID，
        #   要钉**实例号** WAR3_INST=N -> 每 5 秒按进程名 War3-<N>.exe 重新找。
        env_inst = os.environ.get('WAR3_INST', '').strip()
        try:
            out = subprocess.check_output(
                ['tasklist', '/FI', 'IMAGENAME eq War3*', '/FO', 'CSV', '/NH'],
                creationflags=_NOWIN, stderr=subprocess.DEVNULL).decode('mbcs', 'ignore')
            rows = pick_rows(out, env_inst)
            found = [r[1] for r in rows]
            self.pid = int(found[0]) if found else 0
            if len(found) > 1 and self.pid != self._warned_multi:
                self._warned_multi = self.pid
                print('[camctl] ⚠ 有 %d 个 War3 在跑而没设 WAR3_PID —— 取第一个 %d，'
                      '导播只会管这一个实例' % (len(found), self.pid), flush=True)
        except Exception:
            self.pid = 0
        self.pid_at = now
        return self.pid

    def lane(self):
        """这个游戏进程的 observer 车道（换进程就换一条）。拿不到返回 None。"""
        pid = self.find_pid()
        if not pid:
            self.last_error = 'War3 没在跑'
            return None
        if self._lane is None or self._lane[0] != pid:
            try:
                self._lane = (pid, w3fast.FastLane(pid, role=w3fast.ROLE_OBSERVER))
            except Exception as exc:                    # noqa: BLE001
                self.last_error = '快车道连不上：%s' % exc
                self._lane = None
                return None
        return self._lane[1]

    def world(self):
        pid = self.find_pid()
        if not pid:
            return None
        if self._world is None or self._world[0] != pid:
            try:
                self._world = (pid, w3world.World(pid))
            except Exception as exc:                    # noqa: BLE001
                self.last_error = '世界块连不上：%s' % exc
                self._world = None
                return None
        return self._world[1]

    def cmds(self, calls):
        """一批 W3P 命令 -> [Receipt]；拿不到车道返回 None。"""
        lane = self.lane()
        if lane is None:
            return None
        try:
            return lane.commands(calls)
        except Exception as exc:                        # noqa: BLE001
            self.last_error = '%s: %s' % (type(exc).__name__, exc)
            self._lane = None
            return None

    def cmd(self, op, **kw):
        """一条 W3P 命令，接下了返回回执，没接下 / 拿不到返回 None（原因记在 last_error）。"""
        rs = self.cmds([w3fast.command(op, **kw)])
        r = rs[0] if rs else None
        if r is None or not r.ok:
            self.last_error = 'W3P %s：%s' % (op, r.reason if r is not None else '超时/没车道')
            return None
        return r

    def cam_state(self):
        """W3P 镜头状态 dict（w3fast.parse_cam_state）；不在对局里返回 None。顺手记下界面代数（换局检测）。"""
        r = self.cmd('cam_state')
        s = w3fast.parse_cam_state(r.data) if r is not None else None
        self._ui = s['ui_epoch'] if s else 0
        return s

    def run(self, args, timeout=6):
        """起 war3_ui_command.exe 发一个**运行时动作**（只剩血条动作 50 在用）。"""
        pid = self.find_pid()
        if not pid:
            self.last_error = 'War3 没在跑'
            return None
        try:
            p = subprocess.run([EXE, '--pid', str(pid)] + args,
                               capture_output=True, timeout=timeout,
                               creationflags=_NOWIN)
            return p.stdout.decode('utf-8', 'ignore')
        except Exception as exc:                        # noqa: BLE001
            self.last_error = '%s: %s' % (type(exc).__name__, exc)
            return None

    def read_f(self, name, arg=''):
        """读一个镜头量：N_TARGET_X/Y/Z、N_EYE_X/Y/Z，或字段号（arg）。读不到返回 None。"""
        s = self.cam_state()
        if s is None:
            return None
        if name in (N_TARGET_X, N_TARGET_Y, N_TARGET_Z):
            return s['target'][(N_TARGET_X, N_TARGET_Y, N_TARGET_Z).index(name)]
        if name in (N_EYE_X, N_EYE_Y, N_EYE_Z):
            return s['eye'][(N_EYE_X, N_EYE_Y, N_EYE_Z).index(name)]
        try:
            return s['fields'][_FIELD_NAME[int(arg)]]
        except (KeyError, ValueError):
            return None

    # 老接口：cam() / gameui() 都只是"在不在对局里 + 这一局是谁"的令牌。
    # 换局界面代数就变（运行时的换局检测），导播拿它当对局令牌。
    def cam(self):
        s = self.cam_state()
        return ('ui%d' % s['ui_epoch']) if s else None

    def gameui(self):
        return self.cam()

    def ready(self):
        return self.cam() is not None


CTL = _Ctl()


# ---------------------------------------------------------------------------
# 时长：真实秒 -> 游戏秒
# ---------------------------------------------------------------------------
def _speed_pct():
    """倍速。实测"3 秒"的推镜在 300% 下只用 1.02 秒 —— 时长吃的是游戏时间。"""
    f = os.path.join(os.path.dirname(HERE), 'bin', 'speed.txt')
    inst_dir = os.environ.get('WAR3_INST_DIR', '').strip()   # 每实例导播读自己实例的倍速
    if inst_dir and os.path.exists(os.path.join(inst_dir, 'speed.txt')):
        f = os.path.join(inst_dir, 'speed.txt')
    try:
        v = int(open(f).read().strip())
        return v if 25 <= v <= 800 else 100
    except Exception:
        return 100


def gsec(seconds):
    return float(seconds) * (_speed_pct() / 100.0)


# ---------------------------------------------------------------------------
# 原语
# ---------------------------------------------------------------------------

def _unrotate():
    """转圈模式下位置每帧由"圆心+角度"重算，设位置会被**静默**顶掉（实测 orbit 之后瞬移完全无效，Reset 之后恢复）。
    所以位置类命令统一先过这道闸。只有 SetCameraRotateMode（这里的 orbit）会进转圈模式，所以标志位是准的。"""
    if not CTL.rotate_mode:
        return
    CTL.rotate_mode = False
    CTL.cmd('cam_reset', x=0.0)


# 远裁剪面跟着镜头距离走（09-18）。
# 直播中发现：切到全景后画面明显变暗、单位不显示 —— 单位和地形是 3D 几何，超过远裁剪面
# （FARZ，游戏默认 5000）就不画了。几何：需要的 farZ ≈ 距离 × sin(俯角) / sin(俯角 - 视野/2) ≈ 2.3 倍，取 3 倍。
# 放在 set_field 这一处：导播、手动滑块、预设机位都走这里（同一条规则写在多处，只改一处等于没改）。
FARZ_MIN = 5000.0
FARZ_PER_DIST = 3.0


def set_field(field, value, seconds=0.0):
    # 运行时按"传进来的角度是度"设（和 JASS 的 SetCameraField 一致）；时长是游戏秒
    calls = [w3fast.command('cam_field', code=int(field), x=float(value), y=float(gsec(seconds)))]
    if field == F_DISTANCE:
        # 同样的时长一起过渡：拉远时裁剪面跟着推开，推近时跟着收回，中途不会露黑
        farz = max(FARZ_MIN, float(value) * FARZ_PER_DIST)
        calls.append(w3fast.command('cam_field', code=F_FARZ, x=farz, y=float(gsec(seconds))))
    rs = CTL.cmds(calls)
    if not rs or not rs[0].ok:
        return False
    if field == F_DISTANCE:
        fog_for_distance(value, seconds)
        _auto_ui(value)
    return True


# ---------------------------------------------------------------------------
# 地形雾跟着镜头距离走（09-18）
#
# 直播中发现：全景后画面仍会变暗，像蒙了一层半透明的黑色。那层是**地形雾**：Echo Isles 的雾是
# 线性、黑色、离眼睛 3000 开始、5000 全黑。几何：俯角、视野不变时镜头距离放大 k 倍，画面上每个点离眼睛也放大 k 倍，
# 所以雾的起止也乘 k，**每个像素的雾就和 FOG_REF_DIST 时一模一样**。比它近的不动（保持游戏原样）。
# 运行时 cam_fog（x = k）：换局先恢复这张图的默认雾再读基准（别把上次推开的值当默认），起止乘 k。
# ---------------------------------------------------------------------------
FOG_REF_DIST = 2600.0
FOG = {'world': 0, 'base': None, 'k': 1.0, 'timer': None, 'on': True, 'error': ''}
_FOG_LOCK = threading.Lock()


def _fog_apply(k):
    r = CTL.cmd('cam_fog', x=float(k))
    FOG['error'] = '' if r is not None else (CTL.last_error or '推雾没接下')
    return r is not None


def fog_for_distance(distance, seconds=0.0):
    """按镜头距离把地形雾等比推开（见上面那段）。返回这次用的倍数，没动返回 None。

    拉远：立刻推开（中景那一两秒顶上少一点雾，看不出来）；
    推近：等过渡走完（seconds 墙钟秒）再收回 —— 否则镜头还在高处雾就回来了，会黑一下。
    """
    if not FOG['on'] or not distance or distance <= 0:
        return None
    k = max(1.0, float(distance) / FOG_REF_DIST)
    with _FOG_LOCK:
        ui = CTL._ui
        if ui != FOG['world']:
            FOG.update(world=ui, k=1.0)             # 换局：运行时那边也重置了（CamUi 换局清 g_fogK）
        # 每次调用换一代：等着收回的那个定时器如果已经醒了、正排队等锁，
        # 看到代数变了就作废 —— 不然"推近又马上拉远"时，它会在拉远之后把雾收回来
        FOG['gen'] = gen = FOG.get('gen', 0) + 1
        if FOG['timer']:
            FOG['timer'].cancel()
            FOG['timer'] = None
        if abs(k - FOG['k']) < 1e-3:
            return k
        if k > FOG['k'] or seconds <= 0:
            if _fog_apply(k):
                FOG['k'] = k
            return k

        def later():
            with _FOG_LOCK:
                if FOG.get('gen') == gen and FOG['world'] == ui and _fog_apply(k):
                    FOG['k'] = k
                    FOG['timer'] = None
        FOG['timer'] = threading.Timer(float(seconds), later)
        FOG['timer'].daemon = True
        FOG['timer'].start()
        return k


def get_field(field):
    """字段的当前值（引擎原样：角度是**弧度** —— 写进去是度、读出来是弧度，引擎自己的不对称）。"""
    return CTL.read_f(None, str(field))


# 「镜头被我们动过几次」。导播一拍里问镜头位置四五次 —— 缓存一拍读一次，而只要这个数变了就说明镜头被下过命令，
# 缓存作废（director._cam_at）。放在这里：以后新增的下令出口自动生效（不用每个出口各写一份，免得漏改）。
MOVES = {'n': 0}


def _moved():
    MOVES['n'] += 1


def look_at(x, y, seconds=0.0, zoffset=0.0):
    _moved()
    _unrotate()
    # 过渡 0 且不用高度偏移 = 瞬移（SetCameraPosition）；否则定时推镜（PanCameraToTimedWithZ）
    return CTL.cmd('cam_look', x=float(x), y=float(y),
                   fargs={4: float(gsec(seconds)), 5: float(zoffset), 6: 1 if zoffset else 0}) is not None


GLIDE_OVERLAP = 1.6


def glide(x, y, seconds=0.5, ease='inout', segments=5):
    _moved()
    return _glide(x, y, seconds, ease, segments)


def _glide(x, y, seconds=0.5, ease='inout', segments=5):
    """带缓动的快速推镜。

    引擎的定时推镜**是匀速的**——起步和收尾都是硬的，小范围里看就是画面被匀速拖走。
    做法是**把整段切成几段引擎推镜**：段内让引擎匀速插值，段与段之间按缓动曲线分配位移。
    ease='inout' 先慢后快再慢（最像人手甩镜头），'out' 起步猛收尾轻。
    """
    _unrotate()
    s = CTL.cam_state()
    if s is None:
        return False
    ui0 = s['ui_epoch']
    x0, y0 = s['target'][0], s['target'][1]

    def curve(t):
        if ease == 'linear':
            return t
        if ease == 'in':
            return t * t
        if ease == 'out':
            return 1 - (1 - t) * (1 - t)
        return t * t * (3 - 2 * t)           # smoothstep

    n = max(2, int(segments))
    slice_s = float(seconds) / n
    t0 = time.time()
    for i in range(1, n + 1):
        if i % 3 == 0:
            s = CTL.cam_state()
            if s is None or s['ui_epoch'] != ui0:
                return False                     # 对局换了，立刻停手
        k = curve(i / float(n))
        # 每段的时长给 GLIDE_OVERLAP 倍：下一段按时到就接着改道（前一段没走完），
        # 下一段晚到了镜头也还在走 —— 不会走完一段停在半路等下一条命令
        CTL.cmd('cam_look', x=x0 + (x - x0) * k, y=y0 + (y - y0) * k,
                fargs={4: float(gsec(slice_s * (GLIDE_OVERLAP if i < n else 1.0))), 5: 0.0, 6: 0})
        gap = (t0 + slice_s * i) - time.time()
        if gap > 0:
            time.sleep(gap)
    return True


# ---------------------------------------------------------------------------
# 锁定跟随 + 引擎平滑（09-18）
#
# 锁定跟随（SetCameraTargetController）：引擎每帧把镜头拉到"单位 + 偏移"，**逐帧在游戏里算** ——
# 单位走得多顺镜头就跟得多顺。平滑系数（CameraSetSmoothingFactor）只管它往目标收敛有多快：
#   实测挪 250 约 0.25 秒到位，但第一帧就走了 67%~73%；挪 700 以上一帧到位。
# ⚠ 所以**不能拿它来"缓过去"**—— 那是跳，不是缓。用法只有一种：镜头已经在目标上了，
#   按"镜头目标 - 单位当前位置"算偏移再锁（位移为 0），之后跟着单位走（导播的 _follow_anchor）。
# 解除锁定：跟随对象传 0，镜头原地不动、**高度不变**（不用 ResetToGameCamera：它会把高度打回 1650）。
# ---------------------------------------------------------------------------
SMOOTH_RADIUS = 1000.0
SMOOTH = {'factor': 1.0, 'cam': None, 'set': None}


def _unit_ref(unit_addr):
    try:
        a = int(unit_addr, 16) if isinstance(unit_addr, str) else int(unit_addr)
    except (TypeError, ValueError):
        return None
    w = CTL.world()
    s = w.read() if w is not None else None
    u = s.unit_by_addr(a) if s is not None else None
    return u if u is not None else a


def ensure_smoothing(factor=None):
    """把平滑系数设成 SMOOTH['factor']（每局设一次；换局界面代数变了就重设）。"""
    f = SMOOTH['factor'] if factor is None else float(factor)
    ui = CTL._ui or (CTL.cam_state() or {}).get('ui_epoch')
    if not ui:
        return False
    if SMOOTH['cam'] == ui and SMOOTH['set'] == f:
        return True
    if CTL.cmd('cam_smoothing', x=f) is None:
        return False
    SMOOTH['cam'], SMOOTH['set'] = ui, f
    return True


def follow(unit_addr, inherit=False, offx=0.0, offy=0.0):
    _moved()
    _unrotate()
    ref = _unit_ref(unit_addr)
    if not ref:
        return False
    # 运行时锁跟随时把平滑设成 1.0；导播可能要别的系数，紧跟着同一批再设一次
    f = SMOOTH['factor']
    rs = CTL.cmds([w3fast.command('cam_follow', target=ref, x=float(offx), y=float(offy), extra=1 if inherit else 0),
                   w3fast.command('cam_smoothing', x=float(f))])
    if not rs or not rs[0].ok:
        CTL.last_error = 'W3P cam_follow：%s' % (rs[0].reason if rs else '超时/没车道')
        return False
    SMOOTH['cam'], SMOOTH['set'] = CTL._ui, f
    return True


def target_now():
    """镜头**此刻**的注视点 (x, y)（推镜途中也准）。读不到返回 None。"""
    s = CTL.cam_state()
    if not s:
        return None
    x, y = s['live']
    return (x, y) if abs(x) < 1e5 and abs(y) < 1e5 and (x or y) else None


def unit_pos(addr):
    """单位**此刻**的坐标 (x, y)（W3P 世界块，50 毫秒一发布）。读不到返回 None。"""
    try:
        a = int(addr, 16) if isinstance(addr, str) else int(addr)
    except (TypeError, ValueError):
        return None
    w = CTL.world()
    s = w.read() if w is not None else None
    u = s.unit_by_addr(a) if s is not None else None
    if u is None or not u.alive:
        return None
    return (u.x, u.y) if abs(u.x) < 1e5 and abs(u.y) < 1e5 else None


def unfollow():
    """解除锁定跟随：镜头停在原地，高度角度都不动。"""
    _moved()
    return CTL.cmd('cam_follow') is not None


def reset(seconds=0.0):
    _moved()
    ok = CTL.cmd('cam_reset', x=float(gsec(seconds))) is not None
    if ok:
        CTL.rotate_mode = False
    return ok


def orbit_point(x, y, degrees=360.0, seconds=6.0):
    """引擎自己的转圈模式：绕一个**固定坐标**转（运行时 cam_rotate，转过的角度按弧度）。"""
    _moved()
    ok = CTL.cmd('cam_rotate', x=float(x), y=float(y),
                 fargs={4: float(degrees) / DEG, 5: float(gsec(seconds))}) is not None
    if ok:
        CTL.rotate_mode = True
    return ok


def set_bounds(minx, miny, maxx, maxy):
    return CTL.cmd('cam_bounds', x=float(minx), y=float(miny), fargs={4: float(maxx), 5: float(maxy)}) is not None


# ---------------------------------------------------------------------------
# 界面显隐：拉远之后血条失真的**唯一**现成解法
#
# 实测血条宽度 = UnitUI.slk 的 scale × 85.5 **屏幕像素**，距离从 900 拉到 6800 一个像素都不变 ——
# 没有任何镜头参数能改善血条和单位的比例。ShowInterface 关的是整个 HUD（并且自带电影黑边）。
UI_STATE = {'auto': False, 'threshold': 2600.0, 'hidden': False}

# 游戏自己设计的变焦范围：UI_MiscData.txt 的 [Camera] Distance=1650,1600,1500,1400,1275,1100（滚轮那六档）。
MELEE_MAX_DISTANCE = 1650.0
MELEE_ZOOM_STEPS = (1650, 1600, 1500, 1400, 1275, 1100)


def show_interface(show, seconds=0.0):
    ok = CTL.cmd('cam_show_ui', extra=1 if show else 0, x=float(gsec(seconds))) is not None
    if ok:
        UI_STATE['hidden'] = not show
    return ok


# ---------------------------------------------------------------------------
# 全屏取景：只留小地图，世界画面铺满整个窗口（运行时 cam_clean_view）
#
#   ShowInterface(false)   关掉 HUD，**并且**把世界收进电影黑边里 —— 看到的地图反而变少了。
#   clean_view(True)       把控制台那一帧藏掉（小地图不在那一帧里，所以留得住），世界矩形撑满 {0,0,0.6,0.8}。
# ★ 撑矩形是**改视口，不是拉伸**：同样的镜头投影到更大的视口上，比例尺不变、看到的地图更多。
# 原矩形由运行时存着（换局自动作废），还原用存下来的那份，不是"当前值加回 0.130"。
CLEAN = {'on': False, 'rect': None, 'ui': None}


def _clean_forget_if_stale(uiv):
    """对局换了就把记录清掉 —— 不清的话状态会**说谎**：新的一局界面是正常的，而 status 还报 clean=True。"""
    if CLEAN['ui'] is not None and CLEAN['ui'] != uiv:
        CLEAN['on'] = False
        CLEAN['rect'] = None
        CLEAN['ui'] = None


def clean_view(on, keep_minimap=True):
    """只留小地图的全屏取景（keep_minimap 保留签名：小地图本来就不在被藏的那一帧里）。"""
    s = CTL.cam_state()
    if s is None:
        return False
    uiv = s['ui_epoch']
    _clean_forget_if_stale(uiv)
    if on and CLEAN['on'] and CLEAN['ui'] == uiv:
        return True
    if CTL.cmd('cam_clean_view', extra=1 if on else 0) is None:
        return False
    CLEAN['on'] = bool(on)
    CLEAN['ui'] = uiv if on else None
    return True


def _auto_ui(distance):
    """距离变了之后按阈值自动收/放界面。只在**跨过阈值**的那一次下命令（拖滑块时每一格都发会抽搐）。"""
    # 血条系数：镜头一变就跟着变（它不做淡入淡出，只改一个 float）
    if BARS['width'] or BARS['colour']:
        new_scale = _bar_scale(distance) if BARS['width'] else 1.0
        if abs(new_scale - BARS['scale']) > 1e-4:
            bars_push(distance)
    if not UI_STATE['auto'] or distance is None:
        return
    want_hidden = distance > UI_STATE['threshold']
    if want_hidden != UI_STATE['hidden']:
        show_interface(not want_hidden, 0.35)


# ---------------------------------------------------------------------------
# 头顶血条（运行时动作 50）
#
# 血条画在运行时里，但**系数由这里算** —— 镜头距离本来就是这一层设的。DLL 只负责"把系数乘上去"。
# 位标志：bit0 = 宽度随镜头缩放，bit1 = 敌我配色（自己绿 / 别人红 / 中立灰）
BARS_ACTION = 50
BARS = {
    'width': False,
    'colour': False,
    'ref': MELEE_MAX_DISTANCE,   # 这个距离处宽度保持原样
    'lo': 0.30,                  # 镜头很远时最多缩到这么小
    'hi': 1.00,                  # 镜头很近时最多放到这么大
    'scale': 1.0,
}


def _bar_scale(distance=None):
    if distance is None:
        distance = get_field(F_DISTANCE)
    if not distance or distance <= 0 or not BARS['ref']:
        return 1.0
    return max(BARS['lo'], min(BARS['hi'], BARS['ref'] / float(distance)))


def bars_push(distance=None):
    """把当前开关和系数下发给运行时。镜头一动就该调它。"""
    flags = (1 if BARS['width'] else 0) | (2 if BARS['colour'] else 0)
    scale = _bar_scale(distance) if BARS['width'] else 1.0
    BARS['scale'] = scale
    out = CTL.run(['--action', str(BARS_ACTION), '--unit', str(flags),
                   '--at', '%g,0' % scale])
    return out is not None


BARS_FILE = os.path.join(HERE, 'bars-state.json')


def bars_save():
    try:
        import json
        with open(BARS_FILE, 'w', encoding='utf-8') as fh:
            json.dump({k: BARS[k] for k in ('width', 'colour', 'ref', 'lo', 'hi')}, fh)
    except Exception:                                   # noqa: BLE001
        pass


def bars_load():
    try:
        import json
        with open(BARS_FILE, encoding='utf-8') as fh:
            BARS.update({k: v for k, v in json.load(fh).items() if k in BARS})
        return True
    except Exception:                                   # noqa: BLE001
        return False


def _bars_keeper():
    """看住血条设置：换游戏进程或换对局之后重新下发一次（开关活在注入的 DLL 里，游戏一重启就归零）。"""
    seen_pid, seen_ui = 0, None
    while True:
        try:
            pid = CTL.find_pid()
            ui = CTL.gameui() if pid else None
            if pid and ui and (pid != seen_pid or ui != seen_ui):
                seen_pid, seen_ui = pid, ui
                if BARS['width'] or BARS['colour']:
                    time.sleep(1.0)      # 等对局界面稳一下再发
                    bars_push()
            elif not pid:
                seen_pid, seen_ui = 0, None
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(3.0)


def bars_autostart():
    """服务启动时调一次：读回上次的设置，并起一个看门线程。"""
    had = bars_load()
    t = threading.Thread(target=_bars_keeper, daemon=True)
    t.start()
    return had and (BARS['width'] or BARS['colour'])


def bars(width=None, colour=None, ref=None, lo=None, hi=None):
    """开关 / 调参。任何参数留空就保持原样。"""
    if width is not None:
        BARS['width'] = bool(width)
    if colour is not None:
        BARS['colour'] = bool(colour)
    if ref:
        BARS['ref'] = float(ref)
    if lo:
        BARS['lo'] = float(lo)
    if hi:
        BARS['hi'] = float(hi)
    bars_push()
    bars_save()
    return dict(BARS)


def locked():
    s = CTL.cam_state()
    return None if s is None else s['locked']


# ---------------------------------------------------------------------------
# 读状态
# ---------------------------------------------------------------------------

def state(full=True):
    """不在对局里返回 None（而不是一堆 0 —— "读到 0" 和 "没得读" 是两件事）。一次 cam_state 全拿到。"""
    with CTL.lock:
        s = CTL.cam_state()
        if not s:
            return None
        tx, ty, tz = s['target']
        ex, ey, ez = s['eye']
        f = s['fields']
        zoff = f['zoffset']
        out = {
            'inMatch': True,
            'x': tx, 'y': ty, 'z': tz,
            'eyeX': ex, 'eyeY': ey, 'eyeZ': ez,
            # ★ 角度类字段：写进去是度，读出来是弧度（引擎自己的不对称）
            'distance': f['distance'], 'zOffset': zoff, 'pitch': f['aoa'] * DEG, 'yaw': f['rotation'] * DEG,
            # 眼睛离地 = 眼睛比注视点高 + ZOFFSET（注视点本身已经被 ZOFFSET 抬起来了，所以要把它加回去）
            'groundHeight': ez - tz + zoff,
            'rotateMode': CTL.rotate_mode,
        }
        if full:
            fog = s['fog']
            out['fov'] = f['fov'] * DEG
            out['roll'] = f['roll'] * DEG
            out['farZ'] = f['farz']
            out['fogK'] = round(fog['k'], 3)
            out['fogStart'] = (fog['start'] / fog['k']) if fog['k'] else None      # 这张图默认从多远开始
            out['boundMinX'], out['boundMaxX'], out['boundMinY'], out['boundMaxY'] = s['bounds']
            out['locked'] = s['locked']
        return out


def units(local_only=False):
    """场上活着的单位（W3P 世界块），给网页当"主角选择"、给导播找热点用。

    镜头不分敌我：锁步模型下本地客户端持有所有玩家的完整状态，所以自己的单位被打光了也照样能挑一个敌方英雄来跟拍。
    """
    w = CTL.world()
    s = w.read() if w is not None else None
    if s is None:
        return []
    rows = []
    for u in s.units:
        if not u.alive or (local_only and u.owner != s.me):
            continue
        rows.append({
            'index': u.index, 'addr': '0x%08X' % u.addr,
            'type': u.type, 'owner': u.owner,
            'x': u.x, 'y': u.y,
            'hp': int(u.hp), 'hpMax': int(u.hp_max),
            'hero': u.is_hero,
        })
    return rows


# ---------------------------------------------------------------------------
# 战争迷雾：问引擎，不猜（运行时 q_visible：本机玩家视角的 看得见 / 灰雾 / 黑区）
#
# 问题：导播常把镜头切到战争迷雾上并停留很久。切换落点是不是迷雾，可以直接问引擎。
# 实测：我方大厅 visible=1，敌方大厅 masked=1，地图中心 masked=1。
# ---------------------------------------------------------------------------

def _fog_query(x, y):
    r = CTL.cmd('q_visible', x=float(x), y=float(y))
    return None if r is None else r.values[:3]


def is_visible(x, y):
    """这个点本地玩家现在看不看得见。问不到返回 None（别把 IPC 抖动当成迷雾）。"""
    v = _fog_query(x, y)
    return None if v is None else bool(v[0])


def visibility(x, y):
    """'visible' / 'fogged' / 'masked' / None。"""
    v = _fog_query(x, y)
    if v is None:
        return None
    if v[0]:
        return 'visible'
    return 'fogged' if v[1] else 'masked'


# ---------------------------------------------------------------------------
# 运镜
#
# 长镜头在后台线程里跑，HTTP 请求立刻返回。不这么做的话，
# 一段 25 秒的分镜会把请求挂 25 秒，页面全程转圈，而且中途没法叫停。
# ---------------------------------------------------------------------------

class _Runner(object):
    def __init__(self):
        self.thread = None
        self.cancel = threading.Event()
        self.what = None
        self.started = 0.0

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.cancel.set()

    def start(self, name, fn):
        if self.busy():
            self.cancel.set()
            self.thread.join(timeout=3.0)
        self.cancel = threading.Event()
        self.what = name
        self.started = time.time()
        ev = self.cancel

        def wrapper():
            try:
                fn(ev)
            except Exception as exc:                     # noqa: BLE001
                CTL.last_error = '%s: %s' % (type(exc).__name__, exc)
        self.thread = threading.Thread(target=wrapper, daemon=True)
        self.thread.start()
        return True


RUN = _Runner()


def _sleep(ev, seconds):
    """可打断的等待。返回 False 表示被叫停了。"""
    return not ev.wait(max(0.0, seconds))


def _preset_body(target, distance, pitch, fov, zoff, seconds, inherit=False, ev_wait=True):
    def body(ev):
        if isinstance(target, dict) and target.get('addr'):
            follow(target['addr'], inherit=inherit)
        elif target:
            look_at(target['x'], target['y'], seconds=min(1.5, seconds))
        if distance is not None:
            set_field(F_DISTANCE, distance, seconds)
        if pitch is not None:
            set_field(F_PITCH, pitch, seconds)
        if fov is not None:
            set_field(F_FOV, fov, seconds)
        if zoff is not None:
            set_field(F_ZOFFSET, zoff, seconds)
        if ev_wait:
            _sleep(ev, seconds)
    return body


# 常用机位。表里的数是实测调出来的，不是抄的。
PRESETS = {
    'melee':   dict(label='对战默认', distance=1650, pitch=304, fov=70, zoff=0),
    'ground':  dict(label='贴地环绕', distance=800,  pitch=338, fov=70, zoff=0),
    'hero':    dict(label='英雄特写', distance=500,  pitch=352, fov=60, zoff=100),
    'shoulder':dict(label='越肩跟拍', distance=600,  pitch=345, fov=70, zoff=120, inherit=True),
    'wide':    dict(label='定场全景', distance=5000, pitch=290, fov=90, zoff=0),
    'top':     dict(label='正俯视',   distance=3000, pitch=270, fov=70, zoff=0),
    'tele':    dict(label='长焦压缩', distance=3200, pitch=320, fov=32, zoff=0),
    'bird':    dict(label='高空巡航', distance=4200, pitch=300, fov=80, zoff=900),
}


def shot_preset(name, target=None, seconds=2.0):
    p = PRESETS.get(name)
    if not p:
        return False
    return RUN.start(p['label'], _preset_body(
        target, p['distance'], p['pitch'], p['fov'], p['zoff'], seconds,
        inherit=p.get('inherit', False)))


def shot_dolly(target, to, seconds=3.0):
    def body(ev):
        if isinstance(target, dict) and target.get('addr'):
            follow(target['addr'])
        set_field(F_DISTANCE, to, seconds)
        _sleep(ev, seconds)
    return RUN.start('推拉到 %d' % to, body)


def shot_crane(to, seconds=4.0):
    """升降臂：抬的是注视点，构图和角度都不变，整个镜头平移上去。"""
    def body(ev):
        set_field(F_ZOFFSET, to, seconds)
        _sleep(ev, seconds)
    return RUN.start('升降到 %d' % to, body)


def shot_whip(degrees=120.0, seconds=0.3):
    def body(ev):
        start = (get_field(F_YAW) or 0) * DEG
        set_field(F_YAW, start + degrees, seconds)
        _sleep(ev, seconds)
    return RUN.start('甩镜 %d°' % degrees, body)


# 分镜：一串镜头顺着跑。
SEQUENCES = {
    'cinematic': ('电影七镜头', [
        ('preset', dict(name='wide', seconds=3)),
        ('hold', dict(seconds=1)),
        ('preset', dict(name='hero', seconds=2)),
        ('orbit', dict(degrees=360, seconds=8, distance=800, pitch=338)),
        ('vertigo', dict(seconds=2.5, pull=2.5)),
        ('preset', dict(name='shoulder', seconds=3)),
        ('crane', dict(to=1200, seconds=3)),
    ]),
    'sweep': ('巡场', [
        ('preset', dict(name='bird', seconds=2)),
        ('sweep', dict(degrees=180, seconds=6)),
        ('sweep', dict(degrees=-180, seconds=6)),
    ]),
    'reveal': ('揭幕', [
        ('preset', dict(name='top', seconds=0.1)),
        ('hold', dict(seconds=0.6)),
        ('preset', dict(name='ground', seconds=4)),
        ('orbit', dict(degrees=180, seconds=6, distance=800, pitch=338)),
    ]),
    'combat': ('战斗跟拍', [
        ('preset', dict(name='ground', seconds=1.5)),
        ('orbit', dict(degrees=120, seconds=4, distance=900, pitch=336)),
        ('whip', dict(degrees=140, seconds=0.35)),
        ('preset', dict(name='hero', seconds=1.5)),
        ('vertigo', dict(seconds=2, pull=2.0)),
    ]),
}


def play_sequence(key, target=None, restore=True):
    item = SEQUENCES.get(key)
    if not item:
        return False
    label, steps = item

    def body(ev):
        saved = state(full=True) if restore else None
        for kind, kw in steps:
            if ev.is_set():
                break
            if kind == 'hold':
                if not _sleep(ev, kw['seconds']):
                    break
                continue
            if kind == 'preset':
                p = PRESETS[kw['name']]
                _preset_body(target, p['distance'], p['pitch'], p['fov'], p['zoff'],
                             kw.get('seconds', 2), inherit=p.get('inherit', False))(ev)
            elif kind == 'orbit':
                _orbit_body(target, **kw)(ev)
            elif kind == 'vertigo':
                _vertigo_body(target, **kw)(ev)
            elif kind == 'crane':
                set_field(F_ZOFFSET, kw['to'], kw['seconds'])
                if not _sleep(ev, kw['seconds']):
                    break
            elif kind == 'whip':
                start = (get_field(F_YAW) or 0) * DEG
                set_field(F_YAW, start + kw['degrees'], kw['seconds'])
                if not _sleep(ev, kw['seconds']):
                    break
            elif kind == 'sweep':
                _sweep_body(kw['degrees'], kw['seconds'])(ev)
        if saved and not ev.is_set():
            restore_state(saved, seconds=1.0)
            _sleep(ev, 1.2)
    return RUN.start(label, body)


def _orbit_body(target, degrees=360.0, seconds=8.0, distance=0, pitch=0, step=90.0):
    """绕着一个单位（或坐标）转，转的过程中一直看着它。

    ★ 用的是"跟随 + 分段转朝向"，不是引擎的 rotate mode —— 后者绕的是固定坐标，单位一走圈心就留在原地。
    ★ 必须分段：实测一次给 yaw +360 是**空操作**（角度被归一化），而 yaw 90 -> 350 走的是短路（经过 0）。
      切成 <= 90° 的小段之后，方向由步长正负唯一确定，一圈只要 4 次调用。
    """
    def body(ev):
        if isinstance(target, dict) and target.get('addr'):
            follow(target['addr'])
        elif target:
            look_at(target['x'], target['y'])
        if distance:
            set_field(F_DISTANCE, distance, min(1.0, seconds / 4))
        if pitch:
            set_field(F_PITCH, pitch, min(1.0, seconds / 4))
        start = (get_field(F_YAW) or 0) * DEG
        steps = max(1, int(abs(degrees) / max(1.0, step) + 0.999))
        per, per_sec = degrees / steps, seconds / steps
        t0 = time.time()
        for i in range(1, steps + 1):
            if ev.is_set():
                return
            set_field(F_YAW, start + per * i, per_sec)
            if not _sleep(ev, per_sec * i - (time.time() - t0)):
                return
    return body


def _vertigo_body(target, seconds=3.0, pull=2.5):
    """希区柯克变焦：往后拉的同时收窄视角，主体大小不变、背景压扁涌上来。
    主体在画面里的大小 ∝ 1 / (d · tan(fov/2))，所以 d 变成 k 倍时 tan(f1/2) = tan(f0/2) / k。"""
    import math

    def body(ev):
        if isinstance(target, dict) and target.get('addr'):
            follow(target['addr'])
        d0 = get_field(F_DISTANCE) or 1650.0
        f0 = (get_field(F_FOV) or 0) * DEG or 70.0
        half0 = math.tan(f0 / 2 / DEG)
        f1 = max(10.0, min(120.0, 2 * math.atan(half0 / pull) * DEG))
        set_field(F_DISTANCE, d0 * pull, seconds)
        set_field(F_FOV, f1, seconds)
        _sleep(ev, seconds)
    return body


def _sweep_body(degrees, seconds):
    """慢摇：只转朝向，不绕圈（不跟随时就是原地摇镜）。"""
    def body(ev):
        start = (get_field(F_YAW) or 0) * DEG
        steps = max(1, int(abs(degrees) / 90.0 + 0.999))
        per, per_sec = degrees / steps, seconds / steps
        t0 = time.time()
        for i in range(1, steps + 1):
            if ev.is_set():
                return
            set_field(F_YAW, start + per * i, per_sec)
            if not _sleep(ev, per_sec * i - (time.time() - t0)):
                return
    return body


# 对外的三个镜头都只是"起一个后台线程跑那段 body"，body 本身给分镜复用 —— 一份实现两个入口。
def shot_orbit(target, degrees=360.0, seconds=8.0, distance=0, pitch=0, step=90.0):
    return RUN.start('环绕 %d°' % degrees,
                     _orbit_body(target, degrees, seconds, distance, pitch, step))


def shot_vertigo(target, seconds=3.0, pull=2.5):
    return RUN.start('变焦推拉 ×%.1f' % pull, _vertigo_body(target, seconds, pull))


def shot_sweep(target, degrees=60.0, seconds=4.0):
    return RUN.start('摇镜 %d°' % degrees, _sweep_body(degrees, seconds))


def restore_state(s, seconds=0.0):
    """还原到 state() 拿到的那一份。
    ★ 先解除跟随再摆位置：跟随是挂在相机上的控制器，引擎**每帧**用它覆盖注视点，不摘掉就会被顶回去。"""
    reset(0)
    for f, k in ((F_DISTANCE, 'distance'), (F_FARZ, 'farZ'), (F_PITCH, 'pitch'),
                 (F_FOV, 'fov'), (F_ROLL, 'roll'), (F_YAW, 'yaw'), (F_ZOFFSET, 'zOffset')):
        if s.get(k) is not None:
            set_field(f, s[k], seconds)
    if s.get('x') is not None:
        look_at(s['x'], s['y'], seconds=seconds)
    return True


# ---------------------------------------------------------------------------
# 白名单：网页只能点这些名字，永远碰不到表外的能力
# ---------------------------------------------------------------------------

def _num(d, k, default=0.0):
    try:
        return float(d.get(k, default))
    except (TypeError, ValueError):
        return default


def _target(d):
    """网页传的主角：{addr, x, y} 或 {x, y}，都不传就是 None。"""
    t = d.get('target')
    if not isinstance(t, dict):
        return None
    if t.get('addr'):
        return {'addr': t['addr'], 'x': _num(t, 'x'), 'y': _num(t, 'y')}
    if 'x' in t and 'y' in t:
        return {'addr': None, 'x': _num(t, 'x'), 'y': _num(t, 'y')}
    return None


def _op_look(d):    return look_at(_num(d, 'x'), _num(d, 'y'), _num(d, 'seconds'))
def _op_field(d):
    names = {'distance': F_DISTANCE, 'pitch': F_PITCH, 'yaw': F_YAW,
             'fov': F_FOV, 'roll': F_ROLL, 'zoffset': F_ZOFFSET, 'farz': F_FARZ}
    f = names.get(str(d.get('field', '')).lower())
    if f is None:
        return False
    return set_field(f, _num(d, 'value'), _num(d, 'seconds'))


def _op_height(d):
    """按"眼睛离地多高"给，距离由当前俯角反算：离地 = ZOFFSET + 距离 × sin(|俯角|)。俯角很平时这个反算会炸，所以夹住。"""
    import math
    s = state(full=False)
    if not s:
        return False
    sp = abs(math.sin((s['pitch'] or 304) / DEG)) or 0.05
    sp = max(0.05, sp)
    dist = max(50.0, (_num(d, 'value') - (s['zOffset'] or 0)) / sp)
    return set_field(F_DISTANCE, dist, _num(d, 'seconds'))


def _op_nudge(d):
    """按**屏幕方向**推一段。朝向不是 90° 的时候"屏幕上的右边"和世界的 +X 不是一回事。"""
    import math
    s = state(full=False)
    if not s:
        return False
    yaw = (s['yaw'] or 90) / DEG
    fx, fy = math.cos(yaw), math.sin(yaw)
    rx, ry = fy, -fx
    right, up = _num(d, 'right'), _num(d, 'up')
    return look_at(s['x'] + fx * up + rx * right,
                   s['y'] + fy * up + ry * right, _num(d, 'seconds'))


def _op_follow(d):
    t = _target(d)
    if not t or not t.get('addr'):
        return False
    return follow(t['addr'], inherit=bool(d.get('inherit')))


def _op_ui(d):
    return show_interface(bool(d.get('show', True)), _num(d, 'seconds'))


def _op_autohide(d):
    """拉远到阈值以外自动收起界面（阈值默认 2600：单位还认得出、但血条已经明显比单位长的那条线）。"""
    UI_STATE['auto'] = bool(d.get('enabled'))
    if 'threshold' in d:
        UI_STATE['threshold'] = max(1650.0, _num(d, 'threshold', 2600))
    s = state(full=False)
    if s:
        if UI_STATE['auto']:
            _auto_ui(s['distance'])
        elif UI_STATE['hidden']:
            show_interface(True, 0.3)      # 关掉自动就把界面还回来，别留一个"没有 HUD"的坑
    return True


def _op_bounds(d):
    s = state(full=True)
    if not s:
        return False
    m = _num(d, 'margin', 3000)
    return set_bounds(s['boundMinX'] - m, s['boundMinY'] - m,
                      s['boundMaxX'] + m, s['boundMaxY'] + m)


def _op_director(d):
    """自动导播（director.py）。这里**延迟导入** —— director 依赖 camctl，在模块顶部 import 会成环。"""
    from director import DIRECTOR
    if 'enabled' in d:
        if d.get('enabled'):
            DIRECTOR.configure(**d)
            return DIRECTOR.start()
        DIRECTOR.stop()
        return True
    DIRECTOR.configure(**d)
    DIRECTOR.save()          # 参数也要存盘，下次随游戏起来时才是同一套
    return True


def director_status():
    try:
        from director import DIRECTOR
        return DIRECTOR.status()
    except Exception:                                   # noqa: BLE001
        return None


def _op_bars(d):
    """头顶血条：宽度随镜头缩放 + 敌我配色。"""
    return bars(width=d.get('width'), colour=d.get('colour'),
                ref=d.get('ref'), lo=d.get('lo'), hi=d.get('hi')) is not None


OPS = {
    'director': _op_director,
    'bars':     _op_bars,
    'look':     _op_look,
    'nudge':    _op_nudge,
    'field':    _op_field,
    'height':   _op_height,
    'follow':   _op_follow,
    'release':  lambda d: reset(0),
    'reset':    lambda d: reset(_num(d, 'seconds')),
    'bounds':   _op_bounds,
    'ui':       _op_ui,
    'clean':    lambda d: clean_view(bool(d.get('on', True))),
    'autohide': _op_autohide,
    'orbitPoint': lambda d: orbit_point(_num(d, 'x'), _num(d, 'y'),
                                        _num(d, 'degrees', 360), _num(d, 'seconds', 6)),
    'preset':   lambda d: shot_preset(str(d.get('name', 'melee')), _target(d),
                                      _num(d, 'seconds', 2)),
    'orbit':    lambda d: shot_orbit(_target(d), _num(d, 'degrees', 360),
                                     _num(d, 'seconds', 8), _num(d, 'distance'),
                                     _num(d, 'pitch')),
    'vertigo':  lambda d: shot_vertigo(_target(d), _num(d, 'seconds', 3),
                                       _num(d, 'pull', 2.5)),
    'dolly':    lambda d: shot_dolly(_target(d), _num(d, 'to', 1650), _num(d, 'seconds', 3)),
    'crane':    lambda d: shot_crane(_num(d, 'to', 1200), _num(d, 'seconds', 4)),
    'whip':     lambda d: shot_whip(_num(d, 'degrees', 120), _num(d, 'seconds', 0.3)),
    'sweep':    lambda d: shot_sweep(_target(d), _num(d, 'degrees', 60), _num(d, 'seconds', 4)),
    'sequence': lambda d: play_sequence(str(d.get('name', 'cinematic')), _target(d)),
    'stop':     lambda d: (RUN.stop(), True)[1],
}


def do(op, payload):
    """网页唯一的入口。名字不在白名单里就直接拒。"""
    fn = OPS.get(op)
    if not fn:
        return {'ok': False, 'error': '不认识的动作: %s' % op}
    with CTL.lock:
        try:
            ok = bool(fn(payload or {}))
        except Exception as exc:                        # noqa: BLE001
            return {'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
    return {'ok': ok, 'busy': RUN.busy(), 'shot': RUN.what if RUN.busy() else None}


def status():
    s = state(full=True)
    if CTL._ui:
        _clean_forget_if_stale(CTL._ui)
    # 超出对战最远档多少倍 —— 血条相对单位被放大的正是这个倍数
    over = (s['distance'] / MELEE_MAX_DISTANCE) if (s and s.get('distance')) else None
    return {
        'inMatch': s is not None,
        'cam': s,
        'ui': {'hidden': UI_STATE['hidden'], 'auto': UI_STATE['auto'],
               'threshold': UI_STATE['threshold'], 'clean': CLEAN['on']},
        'zoomOver': over,
        'meleeMax': MELEE_MAX_DISTANCE,
        'bars': dict(BARS),
        'director': director_status(),
        'busy': RUN.busy(),
        'shot': RUN.what if RUN.busy() else None,
        'pid': CTL.pid,
        'error': CTL.last_error,
        'presets': [{'key': k, 'label': v['label']} for k, v in PRESETS.items()],
        'sequences': [{'key': k, 'label': v[0]} for k, v in SEQUENCES.items()],
    }
