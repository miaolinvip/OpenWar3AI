# -*- coding: utf-8 -*-
"""自动导播：AI 自对战时替观众选镜头。

要解决的问题是"没人看"——两个程序对打，镜头停在哪儿全靠人手动跟。
这一层每秒看一遍场上情况，挑出最值得看的地方，把镜头带过去。

────────────────────────────────────────────────────────────────────────
设计上的四条硬规则（都是为了"看着不累"，不是为了炫技）

0. **交火抢占一切。** 打起来了就立刻切过去，不等最短停留也不看倍数门槛 ——
   错过开战那一下，比多切一次镜头亏得多。唯一不抢的情况是"现在看的就是
   同一场仗"。下面第 1 条的最短停留只管非交火的切换。

1. **一个镜头至少停 3 秒。** 事件评分每秒都在抖，谁高就切谁的话，
   画面会一秒一跳，比不切还难受。所以先满足最短停留，再谈换不换。
   而且换的门槛是"新的比当前的高一大截"（默认 1.4 倍），不是"高一点"。

2. **近的快速滑过去，远的硬切；滑动必须带缓动。** 分界线 3500：
   比它近用 0.5 秒的缓动滑镜（先慢后快再慢），比它远直接切。
   **不用引擎自带的定时推镜** —— 它是匀速的，小范围里看就是画面被匀速
   拖走，人眼对这个最敏感。滑动之外镜头一律不动：原来每拍都跟一下战线，
   结果是镜头一直在缓慢漂移，比不跟还难受。

3. **镜头高度和俯角一律不动。** 用启动那一刻的值当基准。
   导播只决定"看哪儿"，不决定"怎么看"——那是你在镜头台上调好的。
   唯一的例外是环绕时的偏航，环绕结束会精确转回去。

3.5 **英雄带队打野时锁定跟随。** 打野是一段有头有尾的过程，按事件一个个切
   会把它切碎。识别到"英雄离家够远 + 身边有野怪"就交给引擎的单位跟随，
   打完（野怪没了 7 秒 / 英雄回家 / 英雄阵亡）再放开，回到按事件切。
   我方基地重心是从"自家哪些单位一拍都不动"推出来的 —— 扫描不区分建筑，
   但建筑不会动，观察比维护一张 rawcode 表可靠。

4. **只切自己人参与的事件。** 野怪互殴、对手在地图另一头拆中立建筑，
   观众并不关心，切过去只是打断正在看的东西。判据是"事件重心附近有我方
   单位"，不是"掉血的是我方单位"—— 我们打赢的那一边，掉血的全在对面。

5. **环绕要克制。** 只在"打起来了并且持续了几秒"时才转，转完有 40 秒冷却。
   每次交火都转一圈的话，观众会晕。

────────────────────────────────────────────────────────────────────────
事件是怎么认出来的

只用一个数据源：`camctl.units()`（动作 20 的全场扫描，带 owner/x/y/hp）。
逐拍对比就能得到最有信息量的两类事件——

  掉血：这一拍的 hp 比上一拍低  -> 有人在打架，权重按伤害量
  消失：上一拍有、这一拍没有    -> 有人死了，权重固定且更高

**为什么不用引擎事件（动作 43）**：那条路要先认出事件键，而"谁在挨打"
这件事从血量差里直接就能读出来，不需要再解一层协议。先用便宜的那个。

把事件按 1400 单位的格子聚合，取加权重心当落点——这样镜头对准的是
"这场仗的中间"，而不是某一个单位，队形一动镜头也不会跟着乱晃。
"""
import json
import math
import os
import threading
import time

import camctl
import camframe
from dirintent import INTENTS, ONSET_FORGET_S, beat_key
from dirledger import LEDGER

# 开关和参数持久化。存盘是为了让"随游戏启动"成立 —— 服务重启、游戏重开
# 之后，导播该不该开这件事不能只活在内存里。
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'director-state.json')

# 网页上调的参数走这个文件（和 STATE_FILE 同目录）。导播每几秒看一眼修改时间，
# 变了就热加载 —— 这样改参数不用停导播、不用打断对局。
#
# 为什么不复用 STATE_FILE：那份是导播**自己**写的（存盘 + 自动调参的结果），
# 人和机器写同一个文件，谁也说不清某个值是谁定的。分开之后规矩很简单：
# **人调过的项，自动调参不许再动**（见 _tune）。
CONFIG_NAME = 'director-config.json'

# 网页键 -> 属性名。configure() 和配置文件共用这一张表（同一份映射别写两处，只改一处等于没改）。
CONFIG_KEYS = (
    ('minShot', 'min_shot'), ('glideMax', 'glide_max'),
    ('orbitSeconds', 'orbit_seconds'), ('orbitCooldown', 'orbit_cooldown'),
    ('switchRatio', 'switch_ratio'), ('distance', 'distance'),
    ('ownRadius', 'own_radius'), ('driftTol', 'drift_tol'),
    ('zoomTol', 'zoom_tol'), ('staleAfter', 'stale_after'),
    ('tick', 'tick'), ('idleSeconds', 'idle_seconds'),
    ('idleRotateAfter', 'idle_rotate_after'), ('decay', 'decay'),
    ('combatScore', 'combat_score'), ('cell', 'cell'),
    ('fogGrace', 'fog_grace'), ('emptyRadius', 'empty_radius'),
    ('awayFromBase', 'away_from_base'), ('creepRadius', 'creep_radius'),
    ('heroMinHold', 'hero_min_hold'), ('heroMaxHold', 'hero_max_hold'),
    ('heroCooldown', 'hero_cooldown'), ('reaimDist', 'reaim_dist'),
    ('glideSeconds', 'glide_seconds'),
    ('reaimMinS', 'reaim_min_s'), ('reaimSmall', 'reaim_small'), ('reaimSettleS', 'reaim_settle_s'),
    ('manyOutFrac', 'many_out_frac'), ('reaimSeconds', 'reaim_seconds'),
    ('shrinkAfterS', 'shrink_after_s'), ('roomyFrac', 'roomy_frac'),
    ('fightRadius', 'fight_radius'), ('engageRadius', 'engage_radius'),
    ('hurtMemoryS', 'hurt_memory_s'),
    ('smoothFactor', 'smooth_factor'), ('anchorRadius', 'anchor_radius'),
    ('fightTau', 'fight_tau'), ('fightEndS', 'fight_end_s'), ('fightSwitch', 'fight_switch'),
    ('fightMinHold', 'fight_min_hold'), ('pingpongS', 'pingpong_s'), ('creepFactor', 'creep_factor'),
    ('meetMinFoes', 'meet_min_foes'), ('liveMin', 'live_min'),
    ('moveMin', 'move_min'), ('cutStay', 'cut_stay'), ('leadFactor', 'lead_factor'),
    ('resyncErr', 'resync_err'), ('resyncMinS', 'resync_min_s'),
    ('fightDead', 'fight_dead'), ('fightFollowS', 'fight_follow_s'),
    ('brainMinN', 'brain_min_n'),
    ('brainWeight', 'brain_weight'), ('brainFreshS', 'brain_fresh_s'),
    ('brainFadeS', 'brain_fade_s'),
    ('distWide', 'dist_wide'),
    ('wideCrowd', 'wide_crowd'), ('sizeSeconds', 'size_seconds'),
    ('openingWideS', 'opening_wide_s'), ('heroBreakW', 'hero_break_w'),
    ('subjectGraceS', 'subject_grace'), ('emptyGraceS', 'empty_grace'), ('screenHitS', 'screen_hit_s'),
)
CONFIG_BOOLS = (('ownOnly', 'own_only'), ('correctDrift', 'correct_drift'),
                ('heroFollow', 'hero_follow'), ('fogCheck', 'fog_check'),
                ('orbitEnabled', 'orbit_enabled'), ('brainSelect', 'brain_select'),
                ('sizesEnabled', 'sizes_enabled'), ('heroIntent', 'hero_intent'),
                ('subjectCheck', 'subject_check'), ('heroCreepLock', 'hero_creep_lock'),
                ('smoothMove', 'smooth_move'), ('anticipateQuiet', 'anticipate_quiet'),
                ('followFix', 'follow_fix'), ('fightFollow', 'fight_follow'))

# 大脑意图里参与选镜的几类（Step 3）。
# ⚠ creep（打野目标）不在里面：打野有自己的英雄跟随逻辑，而且 09-18 那局它报警 2 次、
#   10 秒内应验 0 次 —— 大脑定下营地之后要走很远才开打，这时候切过去是对着一群睡着的野怪。
BRAIN_KINDS = ('threat', 'intent', 'meet', 'gather', 'retreat')
# 大脑意图里没信息量的几种（出现在它的人话里）：切过去是看一队人走路
LOW_VALUE_INTENTS = ('回家', '打完了', '目标没了')
# 这几种意图给的是"要去哪"（目的地）：落点要换成正往那走的人（_who_is_going）
MOVE_KINDS = ('intent', 'gather', 'retreat')
# 这几种预判关系到"马上要和敌人打"，可以和真交火一起比分；别的（出击/集结/撤退/打野）有真交火就不参与
URGENT_BRAIN_KINDS = ('threat', 'meet')
# 农民的兵种代码。闲时巡视不看它们（大脑没发布部队分团时的退路才用得上）
WORKER_TYPES = frozenset(('hpea', 'opeo', 'uaco', 'ewsp'))


def _load_static_types():
    """单位数据表（build_intel4/units.json，837 种）里移动速度为 0 的：建筑、塔。读不到返回空集。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'game', 'units.json')
    try:
        with open(p, encoding='utf-8') as fh:
            return frozenset(k for k, v in json.load(fh).items() if not (v or {}).get('spd'))
    except (OSError, ValueError, AttributeError):
        return frozenset()


# 建筑/塔的代码（"画面里还有没有人"不算它们）。读不到数据表时退回"开局以来挪过"判人
STATIC_TYPES = _load_static_types()
# 一个意图消失超过这么久再出现，就算"新冒出来的"（墙钟秒）—— 定义在 dirintent，复盘共用
BRAIN_FORGET_S = ONSET_FORGET_S


class Director(object):
    def __init__(self):
        self.thread = None
        self.stop_flag = threading.Event()
        self.lock = threading.RLock()

        # ---- 可调参数 ----
        # ★ 0.6 -> 0.3（09-18 实机）：每拍实际间隔 = 这里的等待 + 这一拍干活（中位 0.3 秒），
        #   原来中位 0.9 秒墙钟 = 3 倍速下 2.7 游戏秒。一场仗在两拍之间打起来，平均要晚半拍
        #   才发现、再滑 0.5 秒才到 —— 观众看到的就是已经开打了、画面还没到。
        self.tick = 0.3
        self.tick_base = 0.3          # 自动调参能把节拍拉长到 0.6，但不那么卡了要收回这个数
        self._last_tick_at = 0.0      # 上一拍是什么时候（算"两拍之间隔多久"）
        self._cam_now = None          # 这一拍读到的镜头实际位置（一拍只读一次，见 _cam_at）
        self._cam_gen = -1            # 读它的时候 camctl.MOVES 是多少（镜头被下过令就作废）
        self.min_shot = 3.0        # 一个镜头至少停这么久
        self.switch_ratio = 1.4    # 新事件要比当前高这么多倍才换
        # ★ 当前镜头的分数每拍衰减。不衰减的话，一次死人（26 分）之后
        #   这个镜头就再也切不走了 —— 实测在一片打完的空地上钉了 64 秒。
        #   仗还在打的话分数每拍会被重新算高，所以只有"这儿没事了"才会掉下去。
        #   0.92^16 ≈ 0.26，也就是十秒左右降到四分之一。
        self.decay = 0.92
        # 比这近就滑过去，比这远直接切。3500 -> 2000（09-18 晚）：两三千的距离 0.5 秒滑过去是"甩"，
        # 观众只看到一片糊；导播的习惯是目标在画外（中景东西约 ±1300）就直接切
        self.glide_max = 2000.0
        self.glide_seconds = 0.5   # 滑就要滑得快;慢推镜看着就是画面被拖走
        # ★ 镜头落定之后就**别再动**。原来是每拍看战线挪了多少、超过 450
        #   就重新对焦一次,结果是小范围里镜头一直在缓慢漂移 ——
        #   这是最难受的一种画面。现在只有打斗真的快出画了才重新对一次,
        #   而且是快速滑过去,不是漂过去。
        # ★ 1600 -> 600（09-18：交战内容要完整落在画面中央）。
        #   实测镜头在仗上的时候，中心离交火重心中位 600、25% 超过 900 —— 仗挪出 1600 以内
        #   镜头都不动，就是这么来的。原来的顾虑是"镜头一直在缓慢漂移"，所以换成：
        #   偏了 600 就快速滑过去对准，但两次对焦至少隔 reaim_min_s 秒 —— 不漂，也不跑偏。
        self.reaim_dist = 600.0
        # ★ 09-18 下午实机账本（带归因）：大仗里 30 秒挪了 12 次、每次 600~1400 —— 镜头一直在动。
        #   最短间隔 1.5 -> 2 秒、等待 3 -> 4 秒、"很多人出画"25% -> 1/3（many_out_frac）；
        #   就地升级只在"类型变了"（巡视/事件 -> 交火）时走紧急通道（见 _upgrade_in_place）。
        self.reaim_min_s = 2.0
        self.reaim_small = 350.0      # 挪不到这么多不重新对（防两段式抖动，见 _track）
        self.reaim_settle_s = 4.0     # 刚对过焦这么久内，除非很多人出画，不补第二刀
        self.many_out_frac = 0.34     # 这么多参战的人出画了才算"很多人出画"（可以不等 settle）
        # 全景推回中景：仗在中景里只占可见区 roomy_frac 以内、持续 shrink_after_s 秒（迟滞，免得来回推拉）
        self.roomy_frac = 0.75
        self.shrink_after_s = 5.0
        self._roomy_since = 0.0
        self.last_reaim = 0.0
        self.reaim_seconds = 0.6      # 重新对准滑得柔一点（0.45 在 600~1400 的挪动上像甩）
        # 一场仗里有谁（_fight_box）：这团交火 + 附近交火团 fight_radius 内、
        # 最近 hurt_memory_s 秒挨过打的单位，再加上它们 engage_radius 内的人（打人的那一边）。
        # 镜头目标按投影算（camframe）：这些人全落在可见区（扣掉顶栏和底部操作栏）正中；
        # 中景装不下就给全景。
        self.fight_radius = 1300.0
        self.engage_radius = 700.0
        self.hurt_memory_s = 4.0
        self._hurt = {}             # addr -> 最近一次掉血的时刻
        self.fight_ids = set()      # 镜头正看着的这场仗里挨过打的人（跟人，不守地）
        self.frame_at = None        # 镜头现在的目标 (x, y)：按构图算出来的；锁着单位时每拍跟着它挪
        # ---- 运镜（09-18：近距离滑镜不够顺，收尾处有一下小卡；单位移动时应直接锁定镜头跟随；
        #      切过去后很短间隔内又补挪一小段，像抖动）：
        #   * 滑镜段与段重叠（camctl.glide），某段晚到镜头也不停在半路 —— 原来的"小卡"；
        #   * 跟着一队人走的镜头（部队/英雄/预判），滑到位之后**下一拍零位移上锁**，
        #     之后单位走到哪、镜头由引擎逐帧跟到哪（camctl"锁定跟随"那一段）；
        #   * 打仗不锁，由构图框住；防两段式抖动见 _track 的 reaim_small / reaim_settle_s。
        #   ⚠ 试过、弃用：拿引擎平滑来"缓过去"—— 实测挪 500 第一帧就走 86%、挪 700 一帧到位，
        #     不是缓动是跳；滑镜最后一段交给它落地也跳过（577 -> 1320 一帧）。
        self.smooth_move = True       # 开关：跟着一队人走的镜头要不要锁定跟随
        self.smooth_factor = 1.0      # 锁定跟随时引擎的平滑系数（0 < 值 < 10），抹掉单位走位的小抖
        self.anchor_radius = 1200.0   # 锚点单位离场面中心不超过这么远
        self.anchor = None            # 当前锁着的单位：addr、偏移、锁上那一刻它和场面中心的位置
        self.lock_want = None         # 这个镜头想跟的那个单位：它走起来才锁（_follow_anchor）
        self.lead_factor = 1.3        # 在走的队伍：滑镜提前量 = 速度 × 滑镜时长 × 这个（进程调用会拖长滑镜）
        self.move_min = 120.0         # 镜头要挪的不到这么多就不挪（一顿比偏一点难受）
        self.cut_stay = 500.0         # 切镜的新落点离现在的画面不到这么多：镜头不动，只换"在看哪儿"
        self.lock_move = 150.0        # 一拍挪这么多算"在走"（3 倍速下走路一拍约 250~500，原地挪步 < 100）
        self.still_move = 40.0        # 一拍挪不到这么多算"停着"
        self.unlock_still_s = 1.5     # 停着这么久就解锁
        # ---- 跟随跑偏了就追上（09-18 深夜：跟随要么跟紧，要么提前预判一个落点固定下来）----
        # 5-030 3:09 对局画面：跟着大法师去打野，英雄和交火贴在画面最上沿，镜头中间是空草地。
        # 零位移上锁只保证"锁的那一刻不跳"，锁的那一刻偏多少就一路偏多少 —— 英雄在走（3 倍速一秒近千），
        # 滑镜还没到位就锁上了。现在锁着的时候每拍核对被跟的人在画面哪儿（_keep_framed）：
        # 偏离想要的位置 resync_err 以上、或者出了舒服区，就滑到"滑完时他会在的地方"再零位移锁上。
        self.follow_fix = True        # 开关
        self.resync_err = 400.0       # 被跟的人离想要的位置偏这么多（世界坐标）就追
        self.resync_min_s = 1.5       # 两次追之间最少隔这么久（追完先看一两拍）
        self.resync_seconds = 0.45    # 追的那一下滑多久
        self.comfy = (0.12, 0.88, 0.20, 0.62)   # 英雄在屏幕上的舒服区（左 右 上 下，可见区 0.10~0.70 往里收）
        self._resync_at = 0.0
        self.glide_wall = 0.0         # 滑镜实际花的墙钟秒数（指数平均；进程调用会把 0.45 秒拖长）
        # ---- 跟仗（09-18 深夜：一旦开打，镜头中心就对准交战中心）见 _follow_fight ----
        self.fight_follow = True      # 开关：打仗时每拍往打架中心推（关掉 = 回到"挪 600 以上、隔 4 秒才对一次"）
        self.fight_dead = 250.0       # 离打架中心不到这么多不动
        self.fight_follow_s = 0.8     # 每一推的时长（墙钟秒）；下一拍接着改道
        self.pan_until = 0.0          # 推镜还在走（纠偏别插手）
        self._hot = None              # 装不下的大仗里"打得最凶的那一块"（_current_fight；切镜清掉）
        self.rows_now = []
        self.prev_t = 0.0             # self.prev 是哪一刻的
        self.prev_old, self.prev_old_t = {}, 0.0   # 再上一拍（_move 里估速度用）
        # ---- 专业导播的切换规矩（09-18 晚：一有战斗就要立刻切过去；原来的切换缺少专业导播的章法，
        #      画面时常让人摸不着头脑）----
        # 账本（213508 那局 7.7 分钟 53 刀）：两场仗同时打，「交火抢占」不受最短停留限制，谁这一拍掉血多
        # 就切谁 —— 368~378 秒 1.6 / 7.3 / 1.2 秒来回跳了 4 次、每次八九千；切去看「回家路上」
        # 「出击前集结」，1.7 秒后又被交火抢回去；预判切到迷雾里，几秒后强制切走。
        # 现在按"一场仗"来看（_update_fights / _fight_policy）：
        #   * 镜头上没有正在打的仗：任何一场开打，**立刻**切过去；
        #   * 正在看一场仗：别处要热到它的 fight_switch 倍、而且这边已经看了 fight_min_hold 秒才换；
        #     刚离开的仗 pingpong_s 秒内不切回去；这边打完（fight_end_s 没动静）才去下一场；
        #   * 全场有仗在打就不做预判切镜；没信息量的预判（回家路上、打完了、目标没了、两三个敌人）不要。
        self.fight_tau = 3.0          # 一场仗的"热度"= 最近约这么多秒的伤害/阵亡权重（指数衰减累加）
        self.fight_end_s = 3.0        # 这么久没新动静算打完了
        self.fight_join = 2100.0      # 新的交火点离一场仗这么近算同一场
        self.fight_switch = 2.0       # 正在看一场仗时，别处要热到它的这么多倍才换过去
        self.fight_min_hold = 4.0     # 正在看的仗至少看这么久才允许被别处顶掉
        self.pingpong_s = 8.0         # 刚离开的仗这么久内不切回去（除非正在看的已经打完）
        self.creep_factor = 0.5       # 打野的热度打折（观众更想看和玩家打）
        self.live_min = 3.0           # 热度过这个就算"正在打"（有仗不预判、空档里保留为候选）；打野也算
        # 闲时/预判镜头被一场"正在打的小仗"抢走的门槛（09-20）：光看"正在打"（热度 3）会被一拍的小碰撞
        # 拽走、去了又回来；要有点规模才值得切。
        self.idle_break_heat = 6.0
        self.idle_break_n = 3         # 这场仗最多同时有几个人（双方合计）
        self.anticipate_quiet = True  # 全场有仗在打时不做预判切镜
        self.meet_min_foes = 4        # 预判"敌方正朝我方过来"至少这么多敌人才值得切过去
        self.fights = {}              # id -> 一场仗（_update_fights）
        self._fid_seq = 0
        self._fights_t = 0.0
        self.left_fights = {}         # fid -> 从这场仗切走的时刻
        self.cell = 1400.0             # 事件聚合的格子边长
        self.combat_score = 18.0       # 到这个分数就算"打起来了"
        self.combat_hold = 4.0         # 打满这么久才考虑环绕
        # ★ 09-15 直播中发现镜头旋转让人头晕 —— 环绕交战点默认关掉。
        self.orbit_enabled = False
        self.orbit_seconds = 11.0
        self.orbit_cooldown = 40.0
        self.idle_seconds = 8.0        # 这么久没事件就去看自家主力
        # ★ "这个镜头已经没戏了"。原来镜头只在**被别人分数超过**时才走，
        #   原地打完了它也不主动离开 —— 现场表现就是对着一片迷雾待很久。
        #   现在只要当前落点附近这么久没有事件，就判定过期，
        #   立刻切到当下最好的目标，不再看倍数门槛。
        self.stale_after = 6.0
        # 闲时画面在同一处待这么久就换一团部队看。轮换只在没有事件时发生。
        self.idle_rotate_after = 15.0
        # 启动时把镜头设成这个高度。用的是调试时那一档 —— 对战默认最远 1650,
        # 2600 大约是"单位还认得出、场面看得全"的位置。设成 0 就沿用当前高度。
        self.distance = 2600.0
        # ★ 只切自己人参与的事件。野怪互殴、对手在别处拆中立建筑，
        #   观众并不关心，切过去只会打断正在看的东西。
        #   判据是"事件重心附近有我方单位",这样"我们在挨打"和"我们在打人"
        #   两种情况都算 —— 后者的掉血其实发生在对方单位身上。
        self.own_only = True
        self.own_radius = 1800.0
        self.own_owner = 0

        # ---- 英雄打野跟随 ----
        # 打野是一段有头有尾的事件,逐个事件切反而把它切碎了。识别出来就
        # 交给引擎的单位跟随(SetTargetController),镜头稳稳跟着英雄走,
        # 打完再放开、回到按事件切。
        self.hero_follow = True
        # ★ "身边打起来了才锁英雄"（打野跟随）默认关掉（09-18：交战时不应一直锁定英雄视角）。
        #   那条路锁英雄的前提就是英雄在打仗 —— 正好是要避免的。打野现在按"一场仗"框住
        #   （英雄 + 小兵 + 野怪的外框）；跟英雄只留给"大脑说要去打野/出击"的赶路那一段。
        self.hero_creep_lock = False
        self.away_from_base = 2600.0   # 离家这么远才算"出去了"
        self.creep_radius = 1300.0     # 身边这个范围里有野怪就算在打野
        self.creep_end_grace = 7.0     # 野怪没了再等这么久才收工
        self.home_radius = 1800.0      # 回到家这个范围就算回来了
        # ★ "回家"的判据必须明显小于"出门"，否则一跟上就立刻判定回家、
        #   放开、又满足出门条件……实测这样会 3 秒内来回抖 11 次。
        #   所以下面用 _hysteresis() 强制留出死区，而不是指望调参的人记得。
        self.hero_min_hold = 4.0       # 跟上之后至少跟这么久（英雄阵亡除外）
        # ★ 硬上限。判据再准也会有没想到的情况，而一个能把镜头锁死的功能
        #   必须有一条兜底的出路 —— 实测缺了它锁过 144 秒。
        self.hero_max_hold = 45.0
        # 收工之后这个英雄多久之内不许再锁。没有它就会"跳出去又跳回来"。
        self.hero_cooldown = 30.0

        # ---- 镜头纠偏 ----
        # 人手滚轮/拖屏/点小地图之后镜头就不在导播以为的地方了。每拍对一次，
        # 偏了就拉回来。
        # ★ 比的是"上一次落定后**实际**的位置"，不是"我们下的那条指令" ——
        #   引擎会按地图边界夹取，指令给 9048 实际停在 7424 是常事；
        #   拿指令当基准的话会永远对不上，变成每拍都在纠正。
        self.correct_drift = True
        self.drift_tol = 300.0         # 注视点偏这么多就拉回来
        self._drift_n = 0              # 连着几拍偏了（两拍才拉，一拍的读数可能在推镜半路上）
        self.zoom_tol = 60.0           # 高度偏这么多就改回去
        self.zoom_every = 4            # 高度每这么多拍查一次（省一次往返）

        # ---- 运行时状态 ----
        self.prev = {}             # addr -> 上一拍的单位
        self.shot = None           # {'x','y','score','kind','since'}
        self.shot_at = 0.0
        self.combat_since = 0.0
        self.last_orbit = 0.0
        self.orbit_until = 0.0
        self.base = None           # 启动时的镜头基准（高度/俯角/偏航）
        self.last_event_at = 0.0
        self.in_match = False
        self.ui_token = None       # 这一局的身份牌：进程号 + 游戏界面对象（每局新建，见 _step）
        self.mode = 'auto'          # 'auto' 按事件切 / 'hero' 跟着英雄打野
        self.aim = None             # 上一次落定后实际的注视点
        self.shot_last_event = 0.0  # 当前落点附近最后一次有事件的时刻
        self.empty_radius = 1400.0  # 落点这个范围内没有我方单位就算"空镜头"
        # ★ 迷雾：问引擎，不猜（camctl.is_visible，见那段注释）。
        #   "附近有我方单位"只是近似 —— 单位视野 800~1400，而 own_radius 是 1800，
        #   落点在我方单位 1500 外的黑雾里也会被当成"看得见"；我方单位死光之后
        #   对面还在挨野怪打，事件不断，镜头就钉在一片迷雾上（实测 maxSameSpotS 65~85 秒）。
        #   现在：候选落点先问引擎看不看得见，看不见的不切；当前镜头落进迷雾超过
        #   fog_grace 秒就切走。这些都计进报告（fogPct / maxFogStreakS / fogCuts）。
        self.fog_check = True
        self.fog_grace = 1.2        # 镜头在迷雾里最多待这么久（秒）
        self.fog_candidates = 3     # 每拍最多问引擎几个候选点（一次约 17 毫秒）
        self.fog_since = 0.0
        self.shot_vis = True        # 这一拍当前落点看不看得见（给统计用）
        self.tick_n = 0
        self.hero_addr = None
        self.hero_seen = 0.0        # 最后一次在英雄附近打起来的时刻
        self.hero_block = {}        # 英雄地址 -> 这个时刻之前不许再锁
        self.home = None            # 我方基地重心（由不动的自家单位推出来）
        self._still = {}            # addr -> (x, y, 连续没动的拍数)
        self._first_pos = {}        # addr -> 第一次见到时的位置
        self._moved = set()         # 开局以来挪过的自家单位（建筑永远不在里面）
        self.stats = {'shots': 0, 'orbits': 0, 'cuts': 0, 'glides': 0, 'creeps': 0,
                      'fogCuts': 0, 'fogSkips': 0}
        # ★ 每一条判断都先记数，再决定要不要打印。
        #   计数器无条件累加，0 就是这条路一次都没走过 —— 而"打印出来的那些"
        #   永远是被条件筛剩下的，estimate 不了分布。
        self.counts = {}
        self.pinned = set()         # 人在网页上调过的属性名，自动调参不许动（见 _tune）
        self.config_mtime = 0.0
        self.last_clusters = []     # 这一拍**全部**事件团（不过滤），给账本用
        self.brain_beats = []       # 这一拍大脑给的候选点（原样，全部记进账本）
        # ---- 大脑意图参与选镜（Step 3）----
        # 只在意图**刚冒出来**的那几秒有分量：brain_fresh_s 内满分，之后线性淡到
        # brain_fade_s 归零。09-18 实测"护送开矿"在同一个点挂了 200 多游戏秒，
        # 一直给分的话镜头会被钉在一个没事发生的地方 —— 正是这轮要治的病。
        self.brain_select = True
        self.brain_weight = 1.0
        self.brain_fresh_s = 3.0
        self.brain_fade_s = 10.0
        # 意图里的人数少于这个就不拿来预判。09-18 那局 16 次预判切镜只应验 3 次，
        # 没应验的多是「英雄跟在主力后面（1 人）」这种 —— 一两个人走动不是一场戏
        self.brain_min_n = 3
        self._brain_seen = {}       # beat_key -> {'first', 'last', 'n'}
        self.brain_cands = []       # 这一拍还在新鲜期里的意图候选
        # ---- 景别（Step 4）----
        # 09-18 设计要求：该给全景时给全景，团战给中景，景别不能频繁乱切。
        # 原来第 3 条硬规则是"镜头高度一律不动"，全程 2600 —— 景别在架构上就不存在。
        # 现在两档：中景 self.distance（网页上那个"导播默认高度"）/ 全景 dist_wide。
        # ⚠ 原来还有第三档"特写"（英雄的小规模交手 / 残血撤退，用来近似"英雄技能给特写"），
        #   09-18 判定不需要，整块去掉了（连同特写时长、每分钟次数的限制）。
        # 规矩（环绕已经证实看得人头晕，换档同样要克制）：
        #   * 只在**切镜那一刻**换档，镜头落定之后高度不动；
        #   * 纠偏的高度基准是"当前这一档"，不再是固定的 2600 —— 否则刚拉远就被拉回去。
        self.sizes_enabled = True
        # 原来 4200。09-18 实机看全景镜头拉得太远 —— 降到 3400（中景 2600 和原全景之间）。
        # 同一次还查出画面变暗是远裁剪面 5000 不够，那个在 camctl.set_field 里修了。
        self.dist_wide = 3400.0
        self.wide_crowd = 16.0      # 一场仗双方加起来这么多人，中景框不下，给全景
        self.size_seconds = 0.8     # 换档的推拉时长（墙钟秒，camctl 按倍速折成游戏秒）
        self.opening_wide_s = 8.0   # 开局这么久里的闲时镜头给全景（定场）
        self.size = 'mid'
        self.cur_distance = self.distance
        self.size_since = 0.0
        self.match_at = 0.0
        self.size_why = ''
        # ---- 英雄意图跟随 ----
        self.hero_intent = True       # 大脑说要带英雄去打野/出击/撤退，就提前跟上
        # 跟随期间出现这么高分的我方交火（**任何地方，包括英雄自己身边**）就放开，改成框住整场仗。
        # 原来是 36 分、而且只看"别处" —— 09-18 三局，交战期间 11% 的拍数镜头锁在英雄身上，
        # 一场 5 打 6 的仗 90% 的时间在锁英雄。现在和"打起来了"的门槛（combat_score 18）一致。
        self.hero_break_w = 18.0
        self.hero_intent_done = {}    # 已经为之跟过的打算 -> 时刻（同一个打算只跟一次）
        self.follow_src = ''          # 'event' 打起来了才跟 / 'intent' 按大脑意图提前跟
        self.follow_key = None
        # ---- 主体保证 ----
        self.subject_check = True
        # 画面里没有活单位多久就切走（墙钟秒）。2.5 -> 1.5（09-20）：节拍 1.5 秒那阵子，这条宽限
        # 实际要等 2 拍 + 宽限 = 4~6 秒（实测最长 5.8 秒的空镜就是这么来的）。节拍修回 0.3 之后，
        # 1.5 秒 = 5 拍，够确认"人是真的没了"，又不会让观众盯着空草地。
        self.subject_grace = 1.5
        # ★ 画面"没人了"（09-18 晚：画面里已经没人，镜头还常在空地上停两三秒甚至更久）：
        #   可见区里（camframe 屏幕模型）没有我方会动的单位、而且 screen_hit_s 秒内画面里没人掉血 ——
        #   empty_grace 秒后切走。实测原来要 2.5~3.8 秒：上面那条"半径 1400 内一个活单位都没有"
        #   一座建筑、一个雾里的敌人都算"有人"，只能等迷雾（1.2 秒宽限）或过期（4~6 秒）。
        self.empty_grace = 0.5
        self.screen_hit_s = 1.0
        self._screen_hit_t = 0.0
        self._empty_since = 0.0
        self.subj_since = 0.0
        self.shot_subj = 0
        self.shot_why = ''          # 当前镜头的人话原因
        self.shot_near = 0          # 落点附近我方活单位数（_sample 每拍算）
        # ---- 每局统计 ----
        # 镜头在迷雾里待很久这类问题没法凭感觉调，得先能量出来。
        # 判据用"落点附近有没有**我方**单位"：我方单位一定是可见的，
        # 附近一个都没有，画面上多半就是黑的或者空地。
        self.samples = None
        self.report = None
        self.reports = []
        self.last_tick_s = 0.0
        self.idle_pick = 0          # 闲时轮到看第几团
        self.note = ''

    def _bump(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    # -- 生命周期 ----------------------------------------------------------
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        """待命。**不要求现在就在对局里** —— 循环自己会等。

        原来这里"不在对局里就返回失败"，那样"随游戏启动"根本不成立：
        服务先起、游戏后进对局，开机那一下必然失败。现在只负责把线程拉起来，
        进没进对局交给循环里那段跨对局自愈去认。
        """
        if self.running():
            return True
        self.load_config(force=True)   # 人调过的参数优先于存盘（存盘里混着自动调参的结果）
        self.prev = {}
        self.shot = None
        self.shot_at = 0.0
        self.in_match = False          # 让循环把"进对局"当成一次新对局来落基准
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.note = '待命中'
        self.save()
        return True

    def stop(self):
        self.stop_flag.set()
        t = self.thread
        if t:
            t.join(timeout=3.0)
        # 环绕模式和单位跟随都必须解掉，否则镜头会一直自己转/自己跟。
        # 但 ResetToGameCamera 会顺带把高度角度恢复成游戏默认（1650），
        # 所以停完要把我们的高度写回去 —— 否则"关掉导播"会莫名其妙地
        # 把镜头拉近，看着像是别的地方出了问题。
        LEDGER.close_match()        # 句柄别留着：farm 随时会把这个进程拉起来换一份
        camctl.reset(0.6)
        self.mode = 'auto'
        self.hero_addr = None
        if self.distance and self.distance > 0:
            time.sleep(0.7)
            camctl.set_field(camctl.F_DISTANCE, self.distance, 0.4)
        self.size, self.cur_distance = 'mid', self.distance
        self.note = '已停止'
        self.save()
        return True

    def status(self):
        with self.lock:
            return {
                'running': self.running(),
                'shot': dict(self.shot) if self.shot else None,
                'heldFor': round(time.time() - self.shot_at, 1) if self.shot_at else 0,
                'orbiting': time.time() < self.orbit_until,
                'mode': self.mode,
                'home': list(self.home) if self.home else None,
                'base': dict(self.base) if self.base else None,
                'stats': dict(self.stats),
                'report': self.report,
                'live': ({'emptyPct': round(100.0 * self.samples['empty'] /
                                            max(1, self.samples['ticks']), 1),
                          'maxEmptyStreakS': round(self.samples['maxStreak'], 1),
                          'maxSameSpotS': round(self.samples.get('maxSameSpot', 0.0), 1),
                          'ticks': self.samples['ticks'],
                          'maxTickS': round(self.samples.get('maxTick', 0.0), 2),
                          'slowTicks': self.samples.get('slowTicks', 0),
                          'frozenS': round(self.samples.get('slowSum', 0.0), 1)}
                         if self.samples else None),
                'lastTickS': round(self.last_tick_s, 2),
                'note': self.note,
                'why': self.shot_why,
                'size': self.size, 'sizeWhy': self.size_why,
                'curDistance': self.cur_distance,
                'counts': dict(self.counts),
                'ledger': {'stamp': LEDGER.stamp, 'shots': LEDGER.shots_n,
                           'ticks': LEDGER.ticks_n, 'error': LEDGER.error},
                # 全部参数都发出去 —— 网页上能看见的才调得动，
                # 而只发 8 个的话，另外二十来个就只能改代码
                'config': dict([(k, getattr(self, a)) for k, a in CONFIG_KEYS]
                               + [(k, getattr(self, a)) for k, a in CONFIG_BOOLS]),
                # 哪些是人调过的（自动调参不会动它们）
                'pinned': sorted(k for k, a in CONFIG_KEYS + CONFIG_BOOLS
                                 if a in self.pinned),
            }

    def configure(self, pin=True, **kw):
        """改参数。`pin=True`（默认）表示这是**人**调的 —— 自动调参从此不许动它。"""
        for key, attr in CONFIG_KEYS:
            if kw.get(key) is not None:
                setattr(self, attr, float(kw[key]))
                if pin:
                    self.pinned.add(attr)
        for key, attr in CONFIG_BOOLS:
            if kw.get(key) is not None:
                setattr(self, attr, bool(kw[key]))
                if pin:
                    self.pinned.add(attr)
        # 改了某一档的高度、而镜头正好在这一档 —— 目标跟着变，下一次纠偏检查会把镜头推过去
        # （不在这里直接下命令：configure 可能在别的线程里被调，镜头只归导播循环动）
        self.cur_distance = self.dist_wide if self.size == 'wide' else self.distance
        if not self.sizes_enabled and self.size != 'mid':
            self.size, self.cur_distance = 'mid', self.distance
        return self.status()

    def config_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(STATE_FILE)), CONFIG_NAME)

    def load_config(self, force=False):
        """网页上调的参数：文件变了就热加载。返回这次改了哪些（没变返回 {}）。

        导播在独立进程里跑（director_inst.py），而网页在另一个进程里 ——
        原来控制台要改参数只能"接管"，也就是 **terminate 掉正在跑的导播**，
        对局中途镜头会停十几秒。改成过一个文件，谁都不用死。
        """
        path = self.config_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return {}
        if not force and mtime <= self.config_mtime:
            return {}
        self.config_mtime = mtime
        try:
            with open(path, encoding='utf-8-sig') as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return {}
        if not isinstance(d, dict):
            return {}
        before = {attr: getattr(self, attr, None) for _k, attr in CONFIG_KEYS + CONFIG_BOOLS}
        self.configure(pin=True, **d)
        changed = {attr: (before[attr], getattr(self, attr, None))
                   for _k, attr in CONFIG_KEYS + CONFIG_BOOLS
                   if before.get(attr) != getattr(self, attr, None)}
        if changed:
            self.note = '参数更新：' + '、'.join(
                '%s %s→%s' % (a, v[0], v[1]) for a, v in list(changed.items())[:4])
        return changed

    def _tune(self, attr, value):
        """自动调参改一项。人在网页上调过的项一律跳过。

        ⚠ 没有这道闸的时候：director-state.json 里 minShot 是 6.0、staleAfter 3.0，
          而代码默认是 3.0 / 6.0 —— 全是自动调参改完存盘的。人在网页上调完，
          下一局结束就可能被改回去，而且**不留痕**，于是"我明明调过"和
          "它自己变了"永远对不上账。
        """
        if attr in self.pinned:
            self._bump('自动调参·跳过（人调过的）·' + attr)
            return False
        setattr(self, attr, value)
        return True

    # -- 主循环 ------------------------------------------------------------
    def _loop(self):
        n = 0
        while not self.stop_flag.wait(self.tick):
            t0 = time.time()
            err = ''
            n += 1
            if n % 5 == 0:
                # 网页上改的参数：看一眼文件修改时间就够（几十微秒），别等到局间
                try:
                    if self.load_config():
                        self.save()
                except Exception as exc:               # noqa: BLE001
                    self._bump('读配置出错·' + type(exc).__name__)
            try:
                self._step()
            except Exception as exc:                       # noqa: BLE001
                err = '%s: %s' % (type(exc).__name__, exc)
                self.note = err
                self._bump('这一拍出错·' + type(exc).__name__)
            took = time.time() - t0
            self._note_tick(took)
            try:
                self._trace_tick(t0, took, err)
            except Exception as exc:                       # noqa: BLE001
                # 写账本绝不能把导播搞挂 —— 记一笔就继续
                self._bump('账本写不进去·' + type(exc).__name__)

    def _trace_tick(self, t0, took, err=''):
        """每拍一行账本。**不管切没切镜都写。**

        只在切镜时记录的话，看不见"该切而没切"的那些拍 —— 而那才是要改的东西。
        （经验：条件性日志只能确认发生过，不能拿来估分布。）
        """
        if not self.in_match or not LEDGER.stamp:
            return
        row = {'t': round(t0, 2), 'gt': LEDGER.game_time(), 'm': self.mode,
               'vis': 1 if self.shot_vis else 0, 'near': self.shot_near,
               'took': round(took, 3), 'pid': camctl.CTL.pid, 'sz': self.size,
               'subj': self.shot_subj}
        if self.shot:
            row['s'] = [round(self.shot['x']), round(self.shot['y']),
                        self.shot['kind'], round(self.shot['score'], 1)]
        # ⚠ 跟随模式下 shot 的 x/y 是**锁定那一刻**的坐标，之后不再更新 ——
        #   镜头其实一直跟着英雄在走。拿 shot 去判断"镜头在哪"会越差越远
        #   （英雄跑了 3000 就差 3000）。所以这里额外记英雄的当前位置，
        #   复盘一律用 hx/hy。（决策里那个过期坐标是另一件事，Step 3 再动它，
        #   现在动就不叫"只加记录"了。）
        if self.mode == 'hero' and self.hero_addr:
            h = self.prev.get(self.hero_addr)
            if h:
                row['hx'], row['hy'] = round(h['x']), round(h['y'])
                row['hhp'] = h['hp']
        if err:
            row['err'] = err
        # 事件团按分数从高到低，最多 8 个 —— 一拍同时有 8 团以上的情况没出现过，
        # 真出现了也不是"看哪儿"的问题了。
        ev = sorted(self.last_clusters, key=lambda c: c['w'], reverse=True)[:8]
        if ev:
            row['ev'] = [{'x': round(c['x']), 'y': round(c['y']), 'w': round(c['w'], 1),
                          'mine': c['mineN'], 'foe': c['foeN'], 'creep': c['creepN'],
                          'dmgMine': c['dmgMine'], 'dmgFoe': c['dmgFoe'],
                          'deadMine': c['deadMine'], 'deadFoe': c['deadFoe'],
                          'hero': 1 if c['hero'] else 0,
                          'skip': c.get('skip', ''), 'pick': 1 if c.get('picked') else 0}
                         for c in ev]
        if self.brain_beats:
            row['bb'] = [{'k': b['kind'], 'x': round(b['x']), 'y': round(b['y']),
                          'w': round(b['w'], 1), 'why': b['why']}
                         for b in self.brain_beats[:6]]
        LEDGER.tick(row)

    def _note_tick(self, took):
        """记一拍花了多久。卡住的那一拍就是画面冻住的那一段。"""
        self.last_tick_s = took
        sp = self.samples
        if sp is None:
            return
        sp['maxTick'] = max(sp.get('maxTick', 0.0), took)
        sp['tickSum'] = sp.get('tickSum', 0.0) + took
        # 两拍之间实际隔了多久（处理 + 等待）—— **观众感受到的反应速度是这个**，不是 took
        now = time.time()
        if self._last_tick_at:
            sp['gapSum'] = sp.get('gapSum', 0.0) + (now - self._last_tick_at)
        self._last_tick_at = now
        if took > 2.0:
            sp['slowTicks'] = sp.get('slowTicks', 0) + 1
            sp['slowSum'] = sp.get('slowSum', 0.0) + took

    def _step(self):
        now = time.time()
        self._cam_now = None            # 新的一拍：镜头位置重新读一次（引擎自己也会挪它）

        # ★ 跨对局自愈。无人值守要连着跑好几个小时,中间会换好多局;
        #   一换局游戏界面对象就是新的、镜头回到默认高度,而这个循环必须自己
        #   认出这件事并重新落基准 —— 否则新的一局里高度是游戏默认的 1650,
        #   而我们以为还是 2600。
        ui = camctl.CTL.gameui()
        if not ui:
            if self.in_match:
                self._finish_report(now)     # 一局打完，出报告并按报告调参
            self.in_match = False
            self.ui_token = None
            self.note = '等对局'
            return
        # ★ 换局判据不能只有"某一拍恰好看到游戏界面对象为空"。
        #   一拍要 0.3~5 秒（实测有一拍 5.28 秒），而两局之间那段空档很短 ——
        #   09-18 实测：一份账本里游戏时钟从 1751.8 直接跳到 7.5，导播全程以为
        #   还是同一局。后果不只是账本串味：self.prev 还留着上一局的单位表，
        #   新局第一拍会拿它做差分，凭空造出一堆"有人死了"。
        #   游戏界面对象每局都是新建的（camctl 换局重取镜头也靠它），进程号也每局换，
        #   所以拿这两个当身份牌 —— **状态的身份，不靠采样到某个瞬间**。
        token = '%s/%s' % (camctl.CTL.pid, ui)
        jumped = False
        if self.in_match and token != self.ui_token:
            self._finish_report(now)      # 上一局收尾（它会清空计数器）
            self.in_match = False
            jumped = True                 # ⚠ 计数要记在**新局**那一侧：_finish_report
                                          #   和下面的新局分支都会 counts = {}，
                                          #   记在它们前面等于没记（测试就是这么抓到的）
        if not getattr(self, 'in_match', False):
            self.in_match = True
            self.ui_token = token
            self.prev = {}
            self.shot = None
            self.shot_at = 0.0
            self.orbit_until = 0.0
            self.shot_last_event = 0.0
            self.mode = 'auto'
            self.hero_addr = None
            self.hero_block = {}
            self.idle_pick = 0
            self.aim = None
            self.home = None
            self._still = {}
            self._first_pos, self._moved = {}, set()
            self._hurt, self.frame_at, self.fight_ids = {}, None, set()
            self._hot = None
            self.fights, self.left_fights, self._fights_t = {}, {}, 0.0
            self.anchor = self.lock_want = None
            self._brain_seen = {}        # 上一局的意图不算"已经见过"
            self.hero_intent_done = {}
            self.size, self.cur_distance, self.size_since = 'mid', self.distance, now
            self.match_at = now
            if self.distance and self.distance > 0:
                camctl.set_field(camctl.F_DISTANCE, self.distance, 1.0)
            st = camctl.state(full=False)
            if st:
                self.base = {'distance': st['distance'], 'pitch': st['pitch'],
                             'yaw': st['yaw']}
            mine = camctl.units(local_only=True) or []
            if mine:
                self.own_owner = mine[0]['owner']
            self.counts = {}
            if jumped:
                self._bump('换局·没看到空档，靠界面对象/进程号变化认出来')
            LEDGER.bind(STATE_FILE)      # 账本跟着 STATE_FILE 走，自动分实例
            INTENTS.bind(STATE_FILE)
            LEDGER.open_match()
            LEDGER.shot({'t': now, 'gt': LEDGER.game_time(), 'ev': 'match_start',
                         'owner': self.own_owner, 'distance': self.distance})
            self.note = '新对局，已落基准'
            return

        # 环绕期间不改落点 —— 中途插一条 look_at 会把转圈打断成一次抽搐。
        # 但**要看**：仗要是刚好在开始转的时候打完，原样转满 11 秒就是对着
        # 空地干转，这正是"镜头在迷雾里待很久"最常见的一种。
        if now < self.orbit_until:
            rows = camctl.units()
            if rows:
                self._sample(rows, now, 'orbit')
                if not self._own_near(rows, self.shot, self.empty_radius) or \
                        camctl.is_visible(self.shot['x'], self.shot['y']) is False:
                    self.note = '场面空了/落进迷雾，提前收掉环绕'
                    self._end_orbit()
            return
        if self.orbit_until and now >= self.orbit_until:
            self._end_orbit()

        rows = camctl.units()
        if not rows:
            return
        # 锁着单位的话镜头已经跟着它走了 —— 先把"镜头在看哪儿"挪到位，下面的迷雾/主体/构图判断才对得上
        self.rows_now = rows
        self._follow_anchor({u['addr']: u for u in rows})
        # 先问引擎"当前落点看不看得见"，统计和下面的迷雾切走都用它
        self.shot_vis = self._shot_visible()
        self._sample(rows, now, self.mode)
        self.shot_subj = self._subjects(rows)
        cur = {u['addr']: u for u in rows}
        events = self._events(cur)
        self._update_hurt(events, now)
        if events and self.frame_at and camframe.visible(
                [(e['x'], e['y']) for e in events], self.frame_at[0], self.frame_at[1],
                self.cur_distance or self.distance):
            self._screen_hit_t = now
        self._update_home(cur)
        self.prev_old, self.prev_old_t = self.prev, self.prev_t
        self.prev, self.prev_t = cur, now
        # ★ 聚团放在**所有**分支之前算。放在 _pick 里的话，英雄跟随那一拍会直接
        #   return，账本上这段时间就是一片空白 —— 而"跟着英雄的时候别处在打什么"
        #   恰恰是复盘最要看的。算一次几乎不花钱（事件几十个），漏一段就补不回来。
        self.last_clusters = self._cluster(events, rows) if events else []
        self._update_fights(now, rows)
        if self.mode == 'auto' and self.anchor:
            self._keep_framed(now)                  # 锁着跟一队人：锚点跑出舒服区就追上
        # ★ 大脑的意图（Step 2）：**这一步只记账，不参与选镜。**
        #   先把"如果用了它们会怎样"落到账本里，用 director_review 离线对比 ——
        #   直接改选镜规则的话，好了坏了都只能靠肉眼，而这正是这轮改造要摆脱的。
        try:
            self.brain_beats = INTENTS.beats(self.own_owner)
            if self.brain_beats:
                # 拍数和点数分开记 —— 一个计数器同时承载两种口径，
                # 读的时候永远要先猜它在数什么（今天已经在这上面栽过一次）
                self._bump('大脑候选点·有产出的拍')
                self._bump('大脑候选点·累计点数', len(self.brain_beats))
            elif INTENTS.read() is None:
                self._bump('大脑候选点·读不到发布（大脑没跑 or 太旧）')
            else:
                self._bump('大脑候选点·这一刻没有动静')
        except Exception as exc:                          # noqa: BLE001
            self.brain_beats = []
            self._bump('大脑候选点·出错·' + type(exc).__name__)
        # ★ "意图什么时候冒出来的"要**每一拍**都记，不能只在 _pick 里记：
        #   英雄跟随那段 _pick 不跑，放开跟随后，一直挂着的老意图会被当成新冒出来的，
        #   满分把镜头拽过去 —— 淡出机制在最需要它的时候失效。
        self.brain_cands = self._brain_candidates(now)

        # ★ 英雄打野优先于一切事件切换。
        #   打野是一段**有头有尾**的过程，按事件一个个切会把它切碎；
        #   交给引擎的单位跟随，镜头稳稳跟着英雄走，打完再放开。
        if self.hero_follow and self._hero_step(rows, events, now):
            return

        # 纠偏放在事件逻辑之前：镜头都不在该在的地方，谈切换没意义。
        # 环绕和英雄跟随期间不纠 —— 那两种情况镜头本来就归别人控制。
        if self._fix_drift(now):
            return

        # ★ 镜头落在迷雾里超过 fog_grace 秒就切走 —— 不管那边还有没有"事件"。
        #   我方单位死光就没有视野了，这时应该切走。
        #   自己人死光 = 引擎立刻判成看不见，这一条就把它盖住了；
        #   不用再猜"附近有没有我方单位"。
        forced = False
        if self.shot and self.fog_check and self.mode == 'auto':
            # ★ 中心点在雾里、可我方的兵就在画面里 —— 不算落进迷雾（自己的兵永远画得出来）。
            #   09-18 晚账本：26 次「镜头落进迷雾，强制切走」里 20 次切之前镜头附近还有我方单位
            #   （夜里视野变小、仗打进黑暗里、兵站在视野边上），这些切镜观众看来莫名其妙。
            #   只剩敌人（雾里的敌方单位不画）或空地才切。
            if self.shot_vis is False and self._own_on_screen(rows):
                self._bump('迷雾·中心在雾里但我方的兵在画面里，不切')
                self.fog_since = 0.0
            elif self.shot_vis is False:
                if not self.fog_since:
                    self.fog_since = now
                elif now - self.fog_since >= self.fog_grace:
                    self.shot['score'] = 0.0
                    self.shot_last_event = 0.0      # 过期，允许立刻切
                    self.stats['fogCuts'] += 1
                    self.note = '镜头在迷雾里待了 %.1f 秒，切走' % (now - self.fog_since)
                    forced = True
            else:
                self.fog_since = 0.0

        # ★ 主体保证：画面里一个活单位都没有，也要切走（不光是迷雾）。
        #   09-18 设计要求：镜头不能长时间停在无视野或没有主体的画面上。
        #   原来"空镜"只进统计（emptyPct），不驱动任何动作 —— 仗打完、人跑散之后，
        #   只能等"过期"（3~6 秒没事件）才走；而迷雾判据抓不到它，因为那里往往还看得见。
        #   判据用"画面半径内任何一方的活单位（含野怪、建筑，不含金矿商店这类场景物件）"，
        #   不是只数我方 —— 看得见的敌方基地、正在被拆的塔，都不叫没主体。
        subj_forced = False
        if self.shot and self.subject_check and self.mode == 'auto' and not forced:
            # 只管仗打完、人没了的交火/事件镜头：预判（家被威胁 = 拍自家的房子）和闲时看基地，
            # 画面里本来就只有建筑 —— 按这条判空会被强制切走、再被预判切回来（对照世界来回跳 1 -> 15）
            screen_empty = (self.shot['kind'] in ('combat', 'event')
                            and now - self._screen_hit_t > self.screen_hit_s
                            and not self._people_on_screen(rows))
            if screen_empty:
                if not self._empty_since:
                    self._empty_since = now
                elif now - self._empty_since >= self.empty_grace:
                    self.shot['score'] = 0.0
                    self.shot_last_event = 0.0
                    self._bump('空镜·画面里没有我方的人、也没人在打，切走')
                    self.note = '画面里 %.1f 秒没有我方的人，切走' % (now - self._empty_since)
                    forced = subj_forced = True
                    self._empty_since = 0.0
            else:
                self._empty_since = 0.0
            if forced:
                pass
            elif self.shot_subj == 0:
                if not self.subj_since:
                    self.subj_since = now
                elif now - self.subj_since >= self.subject_grace:
                    self.shot['score'] = 0.0
                    self.shot_last_event = 0.0
                    self._bump('空镜·画面里一个活单位都没有，切走')
                    self.note = '画面里 %.1f 秒没有任何活单位，切走' % (now - self.subj_since)
                    forced = subj_forced = True
            else:
                self.subj_since = 0.0

        if self.shot:
            self.shot['score'] *= self.decay

        best = self._pick(events, rows, now)
        if subj_forced:
            # 切去的地方自己也得有主体。最好的候选还是这片空地（多半是刚死完人留下的
            # "消失"事件）、或者也是空的，就回自家部队；连自家部队都没有 —— 不切，
            # 等下一个宽限期再看。否则会从一片空地切到另一片空地，每几秒来一次
            # （对照工具里我方全灭之后就是这样：镜头每 3 秒换一块空地）。
            best = self._subject_target(best, rows)
            if not best:
                self._bump('空镜·哪儿都没有活单位，先不切')
                self.subj_since = now
                return
        elif not best and forced and self.home:
            # 哪儿都看不见就回家 —— 家里一定看得见
            best = {'x': self.home[0], 'y': self.home[1], 'score': 1.0, 'kind': 'idle'}
        if not best:
            # 这一拍哪儿都没新事件（0.3 秒一拍，大多数拍是这样）—— 眼前这场仗照样要跟
            cur = self._current_fight(rows)
            if cur:
                self._track(cur, now)
            return
        if forced:
            self.fog_since = 0.0
            self.subj_since = 0.0
            self._cut_to(best, now, '画面里没有主体，强制切走' if subj_forced
                         else '镜头落进迷雾，强制切走')
            return

        # ★ 交火抢占：打起来了就**立刻**切过去，不等最短停留、不看倍数门槛。
        #   最短停留是为了"看着不累"，但错过开战的那一下比多切一次更亏 ——
        #   观众要的就是那一刻。不抢的情况：现在看的就是这一处。
        # ★ 09-18 起"这一处"不再要求原来也是交火镜头：刚为一个事件/预判/部队切过来，同一处紧接着
        #   打起来，原来会再"交火抢占"切一刀 —— 两刀只差一两百，画面上就是先挪一下、很短间隔内
        #   又挪一小段，像抖动（对照世界里剩下的补刀 13/40 是这一种）。现在就地升级。
        if self._fight_policy(best, rows, now):
            return
        if best['kind'] == 'combat':
            if not (self.shot and self._same_place(best, self.shot)):
                self._cut_to(best, now, '交火抢占')
                return
            if self.shot['kind'] != 'combat':
                self._upgrade_in_place(best, rows, now, '打起来了，就地换成交火镜头')
                return

        # 当前落点已经没事发生了 -> 不看倍数门槛，直接换。
        # （过期判据是 6 秒，本来就比最短停留的 3 秒长，不会打架。）
        if self.shot and now - self.shot_last_event > self.stale_after:
            if (self.shot['kind'] == 'idle' and best['kind'] == 'idle'
                    and self._same_place(best, self.shot)):
                self._bump('闲时·还是这一团，不重切')
                self.shot_last_event = now
                return
            self._cut_to(best, now, '当前落点 %.0f 秒没动静' % (now - self.shot_last_event))
            return

        held = now - self.shot_at if self.shot_at else 1e9
        # 不切的时候跟的是**眼前这场仗**（_current_fight），不是全场最好的候选 —— 那可能在别处
        if self.shot and held < self.min_shot:
            self._bump('不切·没到最短停留')
            self._track(self._current_fight(rows) or best, now)   # 同一场仗挪了就跟，但不换镜头
            return
        # 闲时镜头（看部队/基地）不靠分数抢有内容的镜头（预判、小交火）：原来预判的分一路衰减到 1.6，
        # 「看基地」2.6 就以"分数够了"把它切走，3 秒后那里来了个小交火又切回去（对照世界 brain 31/32）。
        # 闲时只在当前镜头过期（上面那条）的时候接手。
        if self.shot and best['kind'] == 'idle' and self.shot['kind'] != 'idle':
            self._bump('不切·闲时镜头不抢有内容的镜头（等它过期）')
            cur = self._current_fight(rows)
            if cur:
                self._track(cur, now)
            return
        if self.shot and best['score'] < self.shot['score'] * self.switch_ratio:
            # 当前镜头还够好：只跟随，不切换。这是"稳"的主要来源。
            if self._same_place(best, self.shot):
                self._bump('不切·同一处，只跟随')
                self._track(self._current_fight(rows) or best, now)
                self.shot['score'] = best['score']
                self._maybe_orbit(best, now)
                return
            # 换了地方但没好到该切的程度 —— 不切，眼前这场照样跟
            self._bump('不切·新的没高出 %.1f 倍' % self.switch_ratio)
            cur = self._current_fight(rows)
            if cur:
                self._track(cur, now)
            return

        # 同一处分数涨了：不切（切过去也就挪一两百 —— 对照世界里剩下的补刀 16/40 是这一种），就地升级
        if self.shot and best['kind'] in ('combat', 'event') and self._same_place(best, self.shot):
            self._upgrade_in_place(best, rows, now, '同一处分数涨了，只跟随')
            return
        self._cut_to(best, now, '分数够了（%.1f > 当前 %.1f × %.1f）' % (
            best['score'], self.shot['score'] if self.shot else 0.0, self.switch_ratio))

    def _who_is_going(self, x, y):
        """大脑说要去 (x, y)：镜头该拍哪儿。返回 (x, y, 'there'/'going') 或 None。

        * 那里已经有 2 个以上自家的兵（非农民） -> 拍那里；
        * 否则找**正在往那走**的自家兵：这一拍在走（_velocity，一拍挪 lock_move 以上）、方向对着目的地
          （夹角余弦 >= 0.7）、离目的地还有 300 以上、6000 以内 -> 拍他们的重心（之后锁定跟着走）；
        * 都没有 -> None（不预判：人还没动，拍的只会是空地）。
        """
        mine = [u for u in self.rows_now if u['owner'] == self.own_owner and u['hp'] > 0
                and u['type'] not in WORKER_TYPES and (u['hero'] or u['addr'] in self._moved)]
        there = [u for u in mine if math.hypot(u['x'] - x, u['y'] - y) < 900]
        if len(there) >= 2:
            return (sum(u['x'] for u in there) / len(there), sum(u['y'] for u in there) / len(there), 'there')
        going = []
        for u in mine:
            dx, dy = x - u['x'], y - u['y']
            d = math.hypot(dx, dy)
            if d < 300 or d > 6000:
                continue
            v = self._velocity(u)
            if not v:
                continue
            sp = math.hypot(v[0], v[1])
            if sp > 0 and (v[0] * dx + v[1] * dy) / (sp * d) >= 0.7:
                going.append(u)
        if not going:
            return None
        return (sum(u['x'] for u in going) / len(going), sum(u['y'] for u in going) / len(going), 'going')

    def _update_fights(self, now, rows=None):
        """把这一拍的交火团并进"一场仗"：热度按时间指数衰减再加上这一拍的分，打完的过一阵删掉。

        一场仗 = 开打时刻、最后一次有动静的时刻、热度（≈ 最近 fight_tau 秒的伤害/阵亡权重之和）、
        是不是和敌方玩家打（pvp，否则是打野）、有没有英雄、最多几个人。每个交火团标上 fid。
        """
        dt = now - self._fights_t if self._fights_t else 0.0
        self._fights_t = now
        if dt > 0:
            k = math.exp(-dt / self.fight_tau)
            for f in self.fights.values():
                f['heat'] *= k
        for c in self.last_clusters:
            if self.own_only and not c.get('mineN'):
                continue
            best, bd = None, self.fight_join
            for f in self.fights.values():
                if now - f['last'] > self.fight_end_s:
                    continue                       # 打完的不再往里并：再打起来算新的一场
                d = math.hypot(c['x'] - f['x'], c['y'] - f['y'])
                if d < bd:
                    best, bd = f, d
            if best is None:
                self._fid_seq += 1
                best = self.fights[self._fid_seq] = {
                    'id': self._fid_seq, 'x': c['x'], 'y': c['y'], 't0': now, 'last': now,
                    'heat': 0.0, 'peak': 0.0, 'pvp': False, 'hero': False, 'deaths': 0, 'n': 0}
                self._bump('仗·新开打')
            best['x'] += (c['x'] - best['x']) * 0.3        # 仗会挪：中心慢慢跟着新交火走
            best['y'] += (c['y'] - best['y']) * 0.3
            best['last'] = now
            best['heat'] += c['w']
            best['pvp'] = best['pvp'] or c.get('foeN', 0) > 0
            best['hero'] = best['hero'] or bool(c.get('hero'))
            best['deaths'] += c.get('deadMine', 0) + c.get('deadFoe', 0)
            best['n'] = max(best['n'], c.get('mineN', 0) + c.get('foeN', 0))
            best['mineN'], best['foeN'], best['creepN'] = c.get('mineN', 0), c.get('foeN', 0), c.get('creepN', 0)
            best['peak'] = max(best['peak'], best['heat'])
            c['fid'] = best['id']
        # ★ 我方的人在那儿死光了 / 都走了：这场仗对我们来说打完了，不等 fight_end_s。
        #   09-18 晚：一场仗的人全死了，画面空了切走，下一拍它还"正在打"（最后一次动静 = 死人，才 2.4 秒），
        #   又被「开打了，马上切过去」切回那片空地。
        if rows is not None:
            r2 = self.fight_radius ** 2
            mine = [(u['x'], u['y']) for u in rows if u['owner'] == self.own_owner and u['hp'] > 0
                    and not self._is_prop(u) and self._is_person(u)]
            for f in self.fights.values():
                if now - f['last'] > self.fight_end_s:
                    continue
                if not any((x - f['x']) ** 2 + (y - f['y']) ** 2 < r2 for x, y in mine):
                    f['last'] = now - self.fight_end_s - 0.01
                    self._bump('仗·我方的人都不在了，算打完')
        for fid in [k for k, f in self.fights.items() if now - f['last'] > self.fight_end_s + 30.0]:
            del self.fights[fid]
        for fid in [k for k, t in self.left_fights.items() if now - t > self.pingpong_s + 30.0]:
            del self.left_fights[fid]

    def _fight_value(self, f):
        """这场仗现在值多少：热度，打野打折。"""
        if not f:
            return 0.0
        return f['heat'] * (1.0 if f['pvp'] else self.creep_factor)

    def _live_fights(self, now):
        """正在打的仗：fight_end_s 内有动静、热度过 live_min。

        ⚠ 门槛不能用 combat_score（18）：打野热度打五折，一场英雄带兵打野常年在 10~30 之间 ——
          09-18 晚新版第一分钟就被「预判·打野·去集合点」以"分数够了"从一场「我方 9 vs 野怪 1」上切走，
          正是对局画面上那一种（这场仗没算"正在打"，有仗不预判那条没拦住）。
          「开打了立刻切过去」只对交火级别（combat）生效，那条另外判。
        """
        return [f for f in self.fights.values()
                if now - f['last'] <= self.fight_end_s and f['heat'] >= self.live_min]

    def _fight_policy(self, best, rows, now):
        """专业导播的规矩（见 __init__ 那一段）。处理了返回 True（切了 / 决定不切并跟着眼前这场），
        不归它管的返回 False，交给后面原来的规则。"""
        cur = self.fights.get((self.shot or {}).get('fid'))
        cur_live = cur is not None and now - cur['last'] <= self.fight_end_s
        kind = best['kind']
        if kind == 'anticipate':
            if self.anticipate_quiet and self._live_fights(now):
                self._bump('不切·有仗在打，不做预判')
                c = self._current_fight(rows)
                if c:
                    self._track(c, now)
                return True
            return False
        fid = best.get('fid')
        if kind not in ('combat', 'event') or not fid:
            return False
        new = self.fights.get(fid)
        if cur is not None and fid == cur['id']:
            if self.shot and self._same_place(best, self.shot):
                return False                        # 就是眼前这场：交给原来的跟随 / 就地升级
            # 同一场仗，只是铺得开（新交火点离镜头中心 2100 以上）—— 不是"别处"，不切，交给构图跟
            self._bump('不切·同一场仗铺得开，交给构图跟')
            self._track(self._current_fight(rows) or best, now)
            return True
        if not cur_live:
            # ★ 09-20：原来只有"交火级"（单拍 18 分以上，kind == combat）才立刻切。可打野/小规模交火
            #   每拍只有 3~8 分、kind 是 event —— 镜头停在闲时看部队/预判上，一边真在打仗。
            #   实测有仗在打的拍里 10% 镜头不在任何一场仗上（最长连着 2.3 秒）。
            #   现在：**镜头上没有仗、而这场仗正在打（热度过 live_min）**，且当前是闲时/预判 —— 也立刻切。
            live = (new is not None and now - new['last'] <= self.fight_end_s
                    and new['heat'] >= self.idle_break_heat and new.get('mineN')
                    and new.get('n', 0) >= self.idle_break_n
                    # 刚从这场切走就别又切回去（和"别处的仗更大"那条同一道护栏）
                    and now - self.left_fights.get(fid, -1e9) >= self.pingpong_s)
            idle_now = (self.shot or {}).get('kind') in ('idle', 'anticipate')
            if kind == 'combat' or (live and idle_now):
                # 镜头上没有正在打的仗 —— 开打就切，不等最短停留（观众要的就是开打那一下）
                if self.shot and self._same_place(best, self.shot):
                    return False                    # 就在画面里：交给就地升级
                self._cut_to(best, now, '开打了，马上切过去')
                return True
            return False
        # 正在看一场还在打的仗，别处又打起来了
        held = now - self.shot_at if self.shot_at else 1e9
        nv, cv = self._fight_value(new), self._fight_value(cur)
        if now - self.left_fights.get(fid, -1e9) < self.pingpong_s:
            self._bump('不切·刚从那场切走，不来回跳')
        elif held < self.fight_min_hold:
            self._bump('不切·这场仗还没看够')
        elif nv < self.fight_switch * cv:
            self._bump('不切·别处的仗没有大到值得换')
        else:
            self._cut_to(best, now, '别处的仗更大（热度 %.0f，这边 %.0f）' % (nv, cv))
            return True
        c = self._current_fight(rows)
        if c:
            self._track(c, now)
        return True

    def _upgrade_in_place(self, best, rows, now, why):
        """要切去的地方就在现在画面里这一处：不再切一刀，把当前镜头就地换成新的类型/分数/原因，
        交给构图跟（_track，urgent：不等"刚对过焦"的 3 秒，但照样不做 350 以内的小挪动）。
        锁着单位的话先解锁 —— 打仗不跟某一个人（见 _anchor_for）。"""
        kind_changed = best['kind'] != self.shot['kind']
        if best['kind'] == 'combat' and self.shot['kind'] != 'combat':
            self.combat_since = now
        self.shot['kind'] = best['kind']
        self.shot['score'] = best['score']
        if best.get('fid'):
            self.shot['fid'] = best['fid']
        self.shot_last_event = now
        self.shot_why = best.get('why', '') or self.shot_why
        if self.anchor:
            self._unlock_here('打起来了，框整场仗，不跟某一个人')
        self.anchor = self.lock_want = None
        self._bump('不切·就在画面里，' + why)
        self._log_move('upgrade', best['kind'], why, score=round(best['score'], 1))
        # 紧急通道（不等"刚对过焦"）只给类型变了的：打起来了要马上框住；同一处分数涨了按正常节奏
        self._track(self._current_fight(rows) or best, now, urgent=kind_changed)

    # -- 事件 --------------------------------------------------------------
    @staticmethod
    def _is_prop(u):
        """场景物件：金矿、商店、喷泉、地精实验室这类中立被动建筑。**不是战斗单位。**

        为什么要单独认它（09-18，账本上线第一局就抓到）：
        中立金矿 ngol 的"血量"其实是**储量** —— 实测 150000/150000，喷泉 nfoh、
        商店 ngme、实验室 ngad 也都是 150000。金矿被采集、被缠绕替换、采空消失，
        在血量差分里就是一次巨大的"掉血"；权重被 min(drop,400) 封顶成 33.3 分，
        而"打起来了"的门槛是 18 —— 于是导播**交火抢占**，把镜头切到一个没人打架
        的地方。实测一局两次。

        判据两条，任一成立（都实测过）：
          * hpMax >= 20000：中立被动建筑一律 150000，而真单位最大的是城镇大厅 1500、
            野怪 950 —— 13 倍余量，不会误伤；
          * owner >= 13：12 号是中立敌对（真野怪，要看），13~15 是中立被动那几个槽。
        ⚠ 亡灵闹鬼金矿 ugol 是 950/950 的**真建筑**（owner 是玩家），不在此列 ——
          分矿被拆当然要看。它和中立 ngol 在同一坐标各有一个单位（早期实测）。
        """
        return u['hpMax'] >= 20000 or u['owner'] >= 13

    def _events(self, cur):
        """和上一拍比，得到事件列表。

        ★ 09-18 从 4 元组改成 dict：权重的算法一个字都没动，多带的是
          **这件事发生在谁身上**（mine / hero / 掉了多少血 / 什么单位）。
          没有这几个字段，账本里就只剩一个分数，"为什么切这里"依然答不了。
        """
        out = []
        if not self.prev:
            return out
        for addr, u in cur.items():
            old = self.prev.get(addr)
            if not old:
                continue
            drop = old['hp'] - u['hp']
            if drop > 0:
                # ⚠ 这个计数一定要放在 drop > 0 **之后**：放前面的话，它数的是
                #   "每一拍场上有多少中立建筑"（19 个 × 每拍一次 = 一局几千），
                #   而不是"本来会变成事件、被挡下来几次"。第一版就是这么写的，
                #   113 拍数出 2443，差点据此得出"影响比想象的大十倍"的结论
                #   —— 经验：量之前先问这个数量的是不是自己以为的那个东西。
                if self._is_prop(u):
                    self._bump('事件·挡下场景物件的假掉血（金矿/商店/喷泉）')
                    continue
                w = min(drop, 400) / 12.0 * (3.0 if u['hero'] else 1.0)
                out.append({'addr': addr, 'x': u['x'], 'y': u['y'], 'w': w, 'kind': 'damage',
                            'mine': u['owner'] == self.own_owner, 'hero': u['hero'],
                            'drop': drop, 'type': u['type'], 'owner': u['owner']})
        for addr, old in self.prev.items():
            if addr in cur:
                continue
            if self._is_prop(old):
                # 金矿被占用时会被换成另一个单位（ngol -> egol/ugol），在这里看是"消失"，
                # 权重 26+18=44 —— 比多数真交火还高。它不是战斗，不该抢镜头。
                self._bump('事件·跳过场景物件消失（多半是矿被占了）')
                continue
            # 消失 = 死了（或者出了视野，但自对战里我们两边都看得见）
            w = 26.0 * (4.0 if old['hero'] else 1.0)
            if old['hpMax'] >= 600:            # 血厚的多半是建筑，塌一个更值得看
                w += 18.0
            out.append({'addr': addr, 'x': old['x'], 'y': old['y'], 'w': w, 'kind': 'death',
                        'mine': old['owner'] == self.own_owner, 'hero': old['hero'],
                        'drop': old['hp'], 'type': old['type'], 'owner': old['owner'],
                        'big': old['hpMax'] >= 600})
        return out

    def _cluster(self, events, rows):
        """把事件按格子聚成团，每团带上"这是谁在打谁"。

        ★ 返回的是**全部**团，不做 own_only 过滤 —— 过滤是下一步（_pick）的事。
          账本要记全部，否则离线复盘看不见"被 own_only 挡掉的那些"，
          也就分不清"没有候选"和"有候选但被规则挡了"。
        """
        cells = {}
        for e in events:
            key = (int(e['x'] // self.cell), int(e['y'] // self.cell))
            c = cells.setdefault(key, {'wx': 0.0, 'wy': 0.0, 'w': 0.0, 'kinds': set(),
                                       'dmgMine': 0, 'dmgFoe': 0,
                                       'deadMine': 0, 'deadFoe': 0, 'hero': False})
            c['wx'] += e['x'] * e['w']
            c['wy'] += e['y'] * e['w']
            c['w'] += e['w']
            c['kinds'].add(e['kind'])
            if e['kind'] == 'death':
                c['deadMine' if e['mine'] else 'deadFoe'] += 1
            else:
                c['dmgMine' if e['mine'] else 'dmgFoe'] += int(e['drop'])
            if e['hero']:
                c['hero'] = True
        mine = [u for u in rows if u['owner'] == self.own_owner and u['hp'] > 0]
        foe = [u for u in rows
               if u['owner'] != self.own_owner and u['owner'] < 12 and u['hp'] > 0]
        creeps = [u for u in rows if u['owner'] >= 12 and u['hp'] > 0 and not self._is_prop(u)]
        r2 = self.own_radius ** 2
        out = []
        for c in cells.values():
            cx, cy = c['wx'] / c['w'], c['wy'] / c['w']
            near = lambda us: sum(1 for u in us                     # noqa: E731
                                  if (u['x'] - cx) ** 2 + (u['y'] - cy) ** 2 < r2)
            out.append({'x': cx, 'y': cy, 'w': c['w'], 'kinds': sorted(c['kinds']),
                        'dmgMine': c['dmgMine'], 'dmgFoe': c['dmgFoe'],
                        'deadMine': c['deadMine'], 'deadFoe': c['deadFoe'],
                        'hero': c['hero'], 'mineN': near(mine), 'foeN': near(foe),
                        'creepN': near(creeps)})
        return out

    def _update_hurt(self, events, now):
        """记下最近挨过打的单位（构图用）：一拍（0.3 秒）里掉血的只是零星几个，
        一场仗里的人要攒几秒才看得全。死了的去掉，太久没挨打的过期。"""
        for e in events:
            if e['kind'] == 'damage':
                self._hurt[e['addr']] = now
            else:
                self._hurt.pop(e['addr'], None)
        if self._hurt:
            old = [a for a, t in self._hurt.items() if now - t > self.hurt_memory_s]
            for a in old:
                del self._hurt[a]

    def _fight_box(self, c, rows):
        """一场仗里的人。返回 (中心 x, 中心 y, 宽, 高, 人数, 点列表)。

        09-18 设计要求：交战内容要完整落在画面中央。原来交战时画面偏上，看不全战场，
        要往下挪，让所有参战单位都在画面里。
        原来落点是**一个 1400 格子里掉血单位的重心**：一场仗跨两个格子就只框到一半；
        而且掉血的是挨打的那一边，打人的那一边可能整个在画面外。
        现在"参战的人" =
          * 这团交火 + cell×1.5 内其它交火团 fight_radius 以内、最近 hurt_memory_s 秒挨过打的；
          * 再加上它们 engage_radius 以内的活单位（打人的那一边、围着的那一圈）——
            不含场景物件，也不含没挨打的农民（在旁边采矿的不算参战）。
        点列表交给 camframe 求镜头目标（全员落在可见区正中）；中心和宽高只进账本、给网页看。
        """
        cs = [g for g in self.last_clusters
              if math.hypot(g['x'] - c['x'], g['y'] - c['y']) < self.cell * 1.5] or [c]
        pts, ids = self._fight_people(cs, rows)
        if not pts:
            self._bump('构图·没认出参战的人，用交火重心')
            return c['x'], c['y'], 0.0, 0.0, 0, [(c['x'], c['y'])], set()
        self._bump('构图·按参战的人')
        return self._box(pts) + (len(pts), pts, ids)

    @staticmethod
    def _box(pts):
        """点列表的外框：(中心 x, 中心 y, 宽, 高)。"""
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0

    def _fight_people(self, centers, rows, keep=()):
        """这几个交火点附近参战的人（定义见 _fight_box）。返回 ([(x, y)], 挨过打的那些人的 addr)。

        keep：上一次认出来的这场仗里挨过打的人 —— 他们跑远了（追杀、撤退）也还算这场仗，
        镜头跟着人走，而不是守着原来那片地。
        """
        r2 = self.fight_radius ** 2
        live = [u for u in rows if u['hp'] > 0 and not self._is_prop(u)]
        hurt = [u for u in live if u['addr'] in self._hurt
                and (u['addr'] in keep
                     or any((u['x'] - g['x']) ** 2 + (u['y'] - g['y']) ** 2 < r2 for g in centers))]
        if not hurt:
            return [], set()
        e2 = self.engage_radius ** 2
        hurt_set = set(u['addr'] for u in hurt)
        pts = [(u['x'], u['y']) for u in hurt]
        for u in live:
            if u['addr'] in hurt_set or u['type'] in WORKER_TYPES:
                continue
            if any((u['x'] - h['x']) ** 2 + (u['y'] - h['y']) ** 2 < e2 for h in hurt):
                pts.append((u['x'], u['y']))
        return pts, hurt_set

    def _current_fight(self, rows):
        """镜头**正看着的这场仗**现在的样子（给 _track 用）。不是仗、或者仗已经散了，返回 None。

        为什么不直接跟"全场最好的候选"（09-18 对照工具查出来的）：
        镜头停在一场仗上、而别处有一场分数更高但还没到切镜门槛（最短停留 / 倍数）时，
        最好的候选是**别处那场** —— _track 判"不是同一场仗"就不动，于是眼前这场挪走了、
        人跑出画了，镜头也一直不跟：对照世界里打仗时"装得下却没对上"的拍一半是这么来的。
        这一拍这里没人掉血也照样认得出（参战的人按最近几秒挨过打的算，见 _update_hurt）。
        """
        if not self.shot or self.shot.get('kind') not in ('combat', 'event'):
            return None
        # 同一场仗（_update_fights 给的编号）的交火团也算 —— 仗一边打一边挪，新的交火点可能已经离
        # 上次对准的地方 2100 以外了（5-033 29:00：追着打出去 4500，这里只认"离 shot 近的"，一个都认不到）
        fid = self.shot.get('fid')
        near = [g for g in self.last_clusters
                if self._same_place(g, self.shot) or (fid and g.get('fid') == fid)]
        pts, ids = self._fight_people(near + [self.shot], rows, keep=self.fight_ids)
        if not pts:
            return None
        # ★ 全景都装不下：对准打得最凶的那一块，不对外框中心（09-18 深夜实机 23:53，同一场仗南北两块
        #   相距 2000，南边 1 打 18、北边 6 打 17 伤害最重；外框中心落在两块中间、几乎不动 —— 北边那块一直贴着
        #   画面上沿 7 秒）。"最凶" = 这一拍这场仗各交火团按伤害权重的重心，和上一拍的平均（不在两块间来回跳）；
        #   取它 fight_radius 以内的人构图。
        if not camframe.frame(pts, self.dist_wide)[2]:
            hot = [g for g in near if g.get('w', 0) > 0]
            if hot:
                wsum = sum(g['w'] for g in hot)
                hx = sum(g['x'] * g['w'] for g in hot) / wsum
                hy = sum(g['y'] * g['w'] for g in hot) / wsum
                if self._hot:
                    hx, hy = (hx + self._hot[0]) / 2.0, (hy + self._hot[1]) / 2.0
                self._hot = (hx, hy)
            if self._hot:
                r2 = self.fight_radius ** 2
                sub = [p for p in pts if (p[0] - self._hot[0]) ** 2 + (p[1] - self._hot[1]) ** 2 < r2]
                if sub:
                    pts = sub
                    self._bump('构图·仗太大装不下，对准打得最凶的一块')
        else:
            self._hot = None
        fx, fy, bw, bh = self._box(pts)
        # current：这就是镜头正看着的这场仗 —— _track 不再拿"离上次对准的地方多远"去怀疑它
        return {'x': fx, 'y': fy, 'score': self.shot['score'], 'kind': self.shot['kind'],
                'fid': fid, 'current': True,
                'cl': {'crowd': max([g['mineN'] + g['foeN'] for g in near] or [0]),
                       'hero': False, 'dmg': 0, 'dead': 0,
                       'bw': bw, 'bh': bh, 'n': len(pts), 'pts': pts, 'ids': ids}}

    def _frame(self, best, dist=None):
        """这个镜头的目标该放哪：让参战的人（没有就是落点本身）落在可见区正中。

        返回 (x, y, 中景装不装得下)。投影和可见区的标定见 camframe。
        """
        d = dist if dist else (self.cur_distance or self.distance)
        pts = (best.get('cl') or {}).get('pts') or [(best['x'], best['y'])]
        tx, ty, fits, _, _ = camframe.frame(pts, d)
        return tx, ty, fits

    def _why(self, c):
        """一团事件的人话。写进账本和网页，观众能看懂、复盘也能看懂。"""
        who = []
        if c['mineN']:
            who.append('我方 %d' % c['mineN'])
        if c['foeN']:
            who.append('敌方 %d' % c['foeN'])
        if c['creepN'] and not c['foeN']:
            who.append('野怪 %d' % c['creepN'])
        head = ' vs '.join(who) if len(who) > 1 else (who[0] + ' 人' if who else '没人在旁边')
        bits = []
        if c['dmgMine'] or c['dmgFoe']:
            bits.append('掉血 我%d/敌%d' % (c['dmgMine'], c['dmgFoe']))
        if c['deadMine'] or c['deadFoe']:
            bits.append('倒下 我%d/敌%d' % (c['deadMine'], c['deadFoe']))
        if c['hero']:
            bits.append('英雄在场')
        return '%s（%s）' % (head, '，'.join(bits) if bits else '有动静')

    def _brain_candidates(self, now):
        """大脑的意图 -> 候选点。**只在它刚冒出来的那几秒有分量。**

        为什么不是"它在就给分"（09-18 那局 169 拍的账）：
          * 「护送开矿」在同一个点挂了 228 游戏秒、「家被威胁」挂了 115 秒 ——
            一直给分，镜头就被钉在一个大半时间没事发生的地方；
          * 按"第一次出现后 10 秒内那里真出了我方参与的事"算应验率：
            threat 2/2、meet 1/3、intent 2/12、creep 0/2。
            意图**冒出来的那一下**才是信号，它的持续只是大脑在坚持同一个打算。
        所以：刚出现 brain_fresh_s 秒内满分，之后线性淡到 brain_fade_s 归零；
        预判相遇（meet）要连续见到两拍才算（单拍的预测抖得厉害）。
        这些候选的 kind 是 'anticipate'，**不会触发交火抢占** —— 真打起来的那一下
        依然比"大脑觉得要打了"优先。
        """
        seen = self._brain_seen
        out = []
        if self.brain_select:
            for b in self.brain_beats:
                if b['kind'] not in BRAIN_KINDS:
                    continue
                if b['kind'] == 'intent' and (b.get('n') or 99) < self.brain_min_n:
                    self._bump('大脑意图·人太少不预判')
                    continue
                # 没信息量的：回家路上、打完了回家、目标没了 —— 213508 那局这几种预判切过去 12 秒内
                # 一次都没应验，观众看到的是一队人在走路，然后 1~2 秒后被别处的交火拽走
                if b['kind'] == 'intent' and any(k in (b.get('why') or '') for k in LOW_VALUE_INTENTS):
                    self._bump('大脑意图·没信息量（回家/打完了/目标没了）不预判')
                    continue
                if b['kind'] == 'meet' and (b.get('en') or 99) < self.meet_min_foes:
                    self._bump('大脑意图·过来的敌人太少不预判')
                    continue
                key = beat_key(b['kind'], b['why'], b['x'], b['y'])
                s = seen.get(key)
                if s is None or now - s['last'] > BRAIN_FORGET_S:
                    s = seen[key] = {'first': now, 'last': now, 'n': 0}
                    self._bump('大脑意图·新冒出来·' + b['kind'])
                s['last'] = now
                s['n'] += 1
                if b['kind'] == 'meet' and s['n'] < 2:
                    self._bump('大脑意图·相遇预判只见过一拍，先不信')
                    continue
                age = now - s['first']
                span = max(0.1, self.brain_fade_s - self.brain_fresh_s)
                fresh = 1.0 if age <= self.brain_fresh_s else \
                    max(0.0, 1.0 - (age - self.brain_fresh_s) / span)
                w = b['w'] * self.brain_weight * fresh
                if w < 1.0:
                    self._bump('大脑意图·过了新鲜期')
                    continue
                c = {'x': b['x'], 'y': b['y'], 'w': w, 'brain': True, 'bk': b['kind'],
                     'why': b['why'], 'fresh': round(fresh, 2)}
                if b['kind'] in MOVE_KINDS:
                    # 大脑给的是**目的地**。人还没到的时候那是一块空地 —— 09-18 晚对局画面：
                    # 「预判·打野·去集合点（6 人）」切过去，画面是空草地加两座农场，
                    # 而落选的是一场真在打的「我方 7 vs 野怪 3」。拍正在往那走的人，到了再拍那里。
                    at = self._who_is_going(b['x'], b['y'])
                    if not at:
                        self._bump('大脑意图·还没人往那走，不预判（不拍空地）')
                        continue
                    c['x'], c['y'], c['dest'] = at[0], at[1], (b['x'], b['y'])
                    self._bump('大脑意图·' + ('人已经在那' if at[2] == 'there' else '拍正在往那走的人'))
                if 'ax' in b:
                    c['ax'], c['ay'] = b['ax'], b['ay']
                out.append(c)
        for key in [k for k, s in seen.items() if now - s['last'] > BRAIN_FORGET_S]:
            del seen[key]
        return out

    def _pick(self, events, rows, now):
        """把事件聚成团，挑最高分的那团。没有事件就退回"自家主力"。"""
        # 尸体（hp<=0）还会在扫描里留一会儿，它不提供视野，也不是"自家主力"
        mine = [u for u in rows if u['owner'] == self.own_owner and u['hp'] > 0]
        if events and not self.last_clusters:       # _step 已经算过；单独调用时兜底
            self.last_clusters = self._cluster(events, rows)
        bc = self.brain_cands
        live = self._live_fights(now) if self.fights else []
        if events or bc or live:
            groups = []
            near_shot = False
            for c in self.last_clusters:
                cx, cy = c['x'], c['y']
                # ★ 只要自己人参与的。判据是"重心附近有我方单位"而不是
                #   "掉血的是我方单位" —— 我们打赢的那一边，掉血的全是对面。
                if self.own_only:
                    if not c['mineN']:
                        c['skip'] = 'ownOnly'
                        self._bump('候选·没有我方单位在场')
                        continue
                groups.append(c)
                if self.shot and math.hypot(cx - self.shot['x'],
                                            cy - self.shot['y']) < self.cell * 1.5:
                    near_shot = True
            # ★ 正在打的仗，这一拍（0.3 秒）恰好没人掉血也照样是候选 —— 专业导播心里记着"哪几场在打"。
            #   原来只认"这一拍有人掉血的格子"：仗打到一半的空档里它就从候选里消失，
            #   迷雾强制切走 / 当前落点过期时，镜头被切去远处的预判，1 秒后又被这场仗拽回来
            #   （215027 局 328~329 秒：切去「预判·英雄没回血，回家」7729，1.0 秒后交火抢占切回 6719）。
            seen = set(c.get('fid') for c in groups if c.get('fid'))
            for f in live:
                if f['id'] in seen:
                    continue
                groups.append({'x': f['x'], 'y': f['y'], 'w': self._fight_value(f), 'fid': f['id'],
                               'mem': True, 'kinds': ['damage'], 'hero': f['hero'],
                               'mineN': f.get('mineN', 0), 'foeN': f.get('foeN', 0),
                               'creepN': f.get('creepN', 0), 'dmgMine': 0, 'dmgFoe': 0,
                               'deadMine': 0, 'deadFoe': 0})
                self._bump('候选·正在打的仗（这一拍没人掉血）')
                if self.shot and math.hypot(f['x'] - self.shot['x'],
                                            f['y'] - self.shot['y']) < self.cell * 1.5:
                    near_shot = True
            self._bump('候选·总数', len(groups))
            n_ev = len(groups)
            for c in bc:
                # 眼前有我方参与的真交火：「打算去哪」不和它抢 —— 观众要看的是正在打的
                if n_ev and c.get('bk') not in URGENT_BRAIN_KINDS:
                    self._bump('候选·有真交火，出击/集结/撤退这类预判不参与')
                    continue
                groups.append(c)
                # 意图还新鲜、又正好指着当前镜头 = 这里"快要有事了"，别急着判过期走掉
                if self.shot and math.hypot(c['x'] - self.shot['x'],
                                            c['y'] - self.shot['y']) < self.cell * 1.5:
                    near_shot = True
            if bc:
                self._bump('候选·大脑意图', len(bc))
            if near_shot:
                self.shot_last_event = now
            if groups:
                if n_ev:
                    self.last_event_at = now      # "最近有事件"只认真事件，意图不算
                # ★ 候选先问引擎看不看得见。按分数从高到低最多问 fog_candidates 个，
                #   看得见的第一个就是它；全看不见就当没有事件（落到下面的自家镜头）。
                #   问不到（None）当看得见 —— IPC 抖动不该让导演瘫痪。
                # 事件团按它所在的那场仗的热度排（不是这一拍的分）：一场打了十几秒的大仗，
                # 不会因为这一拍别处有人掉了一口血就排到后面去
                for c in groups:
                    f = None if c.get('brain') else self.fights.get(c.get('fid'))
                    c['rank'] = max(c['w'], self._fight_value(f)) if f else c['w']
                groups.sort(key=lambda c: (c['rank'], c['x'], c['y']), reverse=True)
                chosen = None
                for k, c in enumerate(groups):
                    if not self.fog_check:
                        chosen = c
                        break
                    if k >= self.fog_candidates:
                        self._bump('候选·问够了还全在雾里')
                        break                       # 问够了还全在雾里，就当这拍没事件
                    v = camctl.is_visible(c['x'], c['y'])
                    if v is None or v:
                        chosen = c
                        break
                    if c.get('ax') is not None:
                        # 预判相遇的中点还在雾里 —— 退到我方那一团（自己人在的地方一定看得见），
                        # 画面上是"我们的人，敌人正从画外过来"，这本身就是个好镜头
                        c['x'], c['y'] = c['ax'], c['ay']
                        self._bump('候选·相遇点在雾里，退到我方那一团')
                        chosen = c
                        break
                    c['skip'] = 'fog'
                    self.stats['fogSkips'] += 1
                    self._bump('候选·在迷雾里跳过')
                if chosen:
                    chosen['picked'] = True
                    rivals = [{'kind': g['bk'] if g.get('brain') else 'event',
                               'score': round(g['w'], 1), 'x': round(g['x']), 'y': round(g['y']),
                               'why': g['why'] if g.get('brain') else self._why(g)}
                              for g in groups if g is not chosen][:3]
                    if chosen.get('brain'):
                        return {'x': chosen['x'], 'y': chosen['y'], 'score': chosen['w'],
                                'kind': 'anticipate', 'bk': chosen['bk'],
                                'why': '预判·' + chosen['why'], 'rivals': rivals}
                    fx, fy, bw, bh, fn, pts, ids = self._fight_box(chosen, rows)
                    return {'x': fx, 'y': fy, 'score': chosen['w'], 'fid': chosen.get('fid'),
                            'kind': 'combat' if chosen['w'] >= self.combat_score else 'event',
                            'why': self._why(chosen), 'rivals': rivals,
                            # 景别和构图要看场面有多大、人在哪（_size_for / _frame）
                            'cl': {'crowd': chosen['mineN'] + chosen['foeN'],
                                   'hero': chosen['hero'],
                                   'dmg': chosen['dmgMine'] + chosen['dmgFoe'],
                                   'dead': chosen['deadMine'] + chosen['deadFoe'],
                                   'bw': bw, 'bh': bh, 'n': fn, 'pts': pts, 'ids': ids}}

        # 闲着的时候：看自家单位最密的一团。分数给得很低，
        # 任何真事件都能顶掉它。
        # 只有"当前这个镜头还有事在发生"时才压住 idle。
        # 原来的条件是"最近全场有过事件"，那会在别处打架、这边空着的时候
        # 把镜头钉在空地上 —— 正是"对着迷雾待很久"的来源。
        if (self.shot and now - self.shot_last_event < self.stale_after
                and now - self.last_event_at < self.idle_seconds):
            self._bump('闲时·当前镜头还有事，不巡视')
            return None
        if not mine:
            self._bump('闲时·我方一个活单位都没有')
            return None
        groups = self._idle_groups(mine)
        if not groups:
            self._bump('闲时·我方单位太散，凑不出一团')
            return None
        # 画面在同一处待够久就换下一团。**只在闲时轮换** —— 有事件的时候
        # 永远优先事件，轮换不该把镜头从一场仗上拽走。
        if self._same_spot_for() > self.idle_rotate_after and len(groups) > 1:
            self.idle_pick = (self.idle_pick + 1) % len(groups)
            self._reset_same_spot()
        elif self.idle_pick >= len(groups):
            self.idle_pick = 0
        cx, cy, n, what = groups[self.idle_pick]
        self._bump('闲时·看' + what.split('（')[0])
        return {'x': cx, 'y': cy, 'score': 1.0 + n * 0.2, 'kind': 'idle',
                'why': '没事发生，看%s' % what,
                'rivals': [{'kind': 'idle', 'score': round(1.0 + m * 0.2, 1),
                            'x': round(gx), 'y': round(gy), 'why': w}
                           for k, (gx, gy, m, w) in enumerate(groups) if k != self.idle_pick][:3]}

    def _subjects(self, rows, x=None, y=None):
        """某个落点（默认当前镜头）画面半径内，任何一方的活单位有几个。场景物件不算。"""
        if x is None:
            if not self.shot:
                return 0
            x, y = self.shot['x'], self.shot['y']
        r2 = self.empty_radius ** 2
        sx, sy = x, y
        return sum(1 for u in rows
                   if u['hp'] > 0 and not self._is_prop(u)
                   and (u['x'] - sx) ** 2 + (u['y'] - sy) ** 2 < r2)

    def _subject_target(self, best, rows):
        """画面空了要切走：去一个**有主体**的地方。"""
        if best and self._subjects(rows, best['x'], best['y']) > 0 and not (
                self.shot and math.hypot(best['x'] - self.shot['x'],
                                         best['y'] - self.shot['y']) < self.empty_radius):
            return best
        return self._fallback_own(rows)

    def _fallback_own(self, rows):
        """画面空了又没有更好的去处：正在打的仗 > 部队 > 英雄 > 基地。

        原来是"自家单位最多的一团"—— 农民和房子也算，最多的那团常常是主基地：
        仗打完人走了，镜头从空地切回老家，观众看到的是一堆农民在采矿。
        """
        now = time.time()
        live = sorted(self._live_fights(now), key=self._fight_value, reverse=True) if self.fights else []
        for f in live:
            if not self.shot or math.hypot(f['x'] - self.shot['x'], f['y'] - self.shot['y']) >= self.empty_radius:
                return {'x': f['x'], 'y': f['y'], 'score': self._fight_value(f), 'fid': f['id'],
                        'kind': 'combat' if self._fight_value(f) >= self.combat_score else 'event',
                        'why': '画面空了，去正在打的那场（%s）' % ('和敌方' if f['pvp'] else '打野')}
        mine = [u for u in rows if u['owner'] == self.own_owner and u['hp'] > 0]
        groups = self._idle_groups(mine) if mine else []
        for gx, gy, n, what in groups:
            if not self.shot or math.hypot(gx - self.shot['x'], gy - self.shot['y']) >= self.empty_radius:
                return {'x': gx, 'y': gy, 'score': 1.0 + n * 0.2, 'kind': 'idle',
                        'why': '画面空了，去看%s' % what}
        groups = self._own_groups(mine)
        if groups:
            cx, cy, n = groups[0]
            return {'x': cx, 'y': cy, 'score': 1.0 + n * 0.2, 'kind': 'idle',
                    'why': '画面空了，回到自家部队（%d 人）' % n}
        if mine:                       # 凑不成一团（全散开了）：随便一个自己人也比空地强
            u = max(mine, key=lambda v: v['hpMax'])
            return {'x': u['x'], 'y': u['y'], 'score': 1.0, 'kind': 'idle',
                    'why': '画面空了，回到自家单位'}
        return None

    def _idle_groups(self, mine):
        """没仗打的时候看什么：**部队 > 英雄 > 基地**。返回 [(x, y, 人数, 人话), ...]。

        09-18 实战发现：镜头多次长时间停在二矿，画面上没什么可看的。
        原来闲时看"自家单位最多的一团"，而那数的是**所有**自家单位 —— 农民和房子也算，
        于是人最多的几团永远是主基地和采矿的分矿：09-18 那两局 41%~57% 的时间镜头停在
        自家基地附近，主基地单次最长连续 62~67 秒，二矿那边每次都是「看自家第 2/3 团（6~8 人）」
        —— 就是分矿的大厅加农民。
        部队按大脑发布的 myArmies（大脑自己按"能动、能打、不是农民不是建筑"聚的团，
        和它下命令用的是同一个口径）；大脑没发布时退回自己认：农民按兵种代码，
        建筑按"开局以来一次都没挪过"。一个兵都还没出来，才看英雄，再没有才看基地。
        """
        armies = [a for a in INTENTS.armies() if a['n'] >= 2]
        if armies:
            armies.sort(key=lambda a: -a['strength'])
            return [(a['x'], a['y'], a['n'], '部队（%d 人，强度 %s）' % (a['n'], a['strength']))
                    for a in armies[:3]]
        movers = [u for u in mine if u['addr'] in self._moved and u['type'] not in WORKER_TYPES]
        groups = self._own_groups(movers) if movers else []
        if groups:
            return [(x, y, n, '部队（%d 人）' % n) for x, y, n in groups]
        heroes = [u for u in mine if u['hero']]
        if heroes:
            h = heroes[0]
            return [(h['x'], h['y'], 1, '英雄（还没有成团的部队）')]
        return [(x, y, n, '基地（还没有部队，%d 个单位）' % n) for x, y, n in self._own_groups(mine)]

    def _own_groups(self, mine, radius=1500.0, top=3):
        """把我方单位分成几团，按人数从多到少返回 [(x, y, 人数), ...]。

        贪心：每次取还没被认领的单位里邻居最多的那个当团心，
        把它半径内的都认领掉，再找下一团。够用，也不需要真的做聚类。
        """
        r2 = radius ** 2
        left = list(mine)
        out = []
        while left and len(out) < top:
            best, members = None, []
            for u in left:
                near = [v for v in left
                        if (v['x'] - u['x']) ** 2 + (v['y'] - u['y']) ** 2 < r2]
                if len(near) > len(members):
                    best, members = u, near
            if not best or len(members) < 2:
                break
            cx = sum(v['x'] for v in members) / len(members)
            cy = sum(v['y'] for v in members) / len(members)
            out.append((cx, cy, len(members)))
            ids = {id(v) for v in members}
            left = [v for v in left if id(v) not in ids]
        return out

    def _same_spot_for(self):
        sp = self.samples
        return sp.get('sameSpot', 0.0) if sp else 0.0

    def _reset_same_spot(self):
        if self.samples:
            self.samples['sameSpot'] = 0.0
            self.samples['lastSpot'] = None

    # -- 统计 --------------------------------------------------------------
    def _own_near(self, rows, spot, radius):
        if not spot:
            return 0
        r2 = radius ** 2
        return sum(1 for u in rows
                   if u["owner"] == self.own_owner and u["hp"] > 0
                   and (u["x"] - spot["x"]) ** 2 + (u["y"] - spot["y"]) ** 2 < r2)

    def _cam_at(self):
        """镜头此刻在看哪儿。凡是问"画面里有什么"的都从这里取。

        读引擎里的**实际**位置（camctl.target_now，读一次）；锁着单位时用 frame_at（引擎在拉，
        它每拍跟着单位挪）；读不到用 frame_at，再不行用 shot。
        ★ 09-18 晚：记的（frame_at）和实际差了 1000~3700（解锁后被纠偏拉回，见 _unlock_here），
          而构图（_track）、"画面里有没有人"、"离得近就不动镜头"（_move）全按记的算 ——
          以为仗就在画面里，镜头其实停在空草地上，谁也不去纠正它。
        """
        if not self.anchor:
            # ★ 一拍只读一次（09-20）：每次读都是一趟进程调用，而一拍里问"镜头在哪"的地方有四五处 ——
            #   实测每拍 0.45 秒里大半是这种往返。镜头一动就作废（_cam_moved）。
            gen = camctl.MOVES['n']
            if self._cam_now is None or self._cam_gen != gen:
                cam = camctl.target_now()
                self._cam_now, self._cam_gen = ((cam[0], cam[1]) if cam else False), gen
            if self._cam_now:
                return self._cam_now
        return self.frame_at or ((self.shot['x'], self.shot['y']) if self.shot else None)

    def _people_on_screen(self, rows):
        """画面可见区里有几个我方的"人"（_is_person：不是建筑/塔）。"""
        at = self._cam_at()
        if not at:
            return 1                       # 不知道镜头在哪：别乱判空
        near = [u for u in rows if u['hp'] > 0 and not self._is_prop(u)
                and abs(u['x'] - at[0]) < 3000 and abs(u['y'] - at[1]) < 3000]
        mine = [u for u in near if u['owner'] == self.own_owner]
        # 我方的人；加上我方单位/建筑 900 以内的敌方和野怪的人 —— 在我方视野里、画得出来
        # （只数我方的话，敌人在拆我方房子的画面会被判成"没人"，掉血一停就切走、再掉血又切回来）
        pts = [(u['x'], u['y']) for u in mine if self._is_person(u)]
        pts += [(u['x'], u['y']) for u in near if u['owner'] != self.own_owner and self._is_person(u)
                and any(abs(u['x'] - m['x']) < 900 and abs(u['y'] - m['y']) < 900 for m in mine)]
        return camframe.visible(pts, at[0], at[1], self.cur_distance or self.distance) if pts else 0

    def _is_person(self, u):
        """是"人"（会动的单位）不是建筑：按单位数据表的移动速度；表读不到就看开局以来挪没挪过。"""
        if u['hero']:
            return True
        if STATIC_TYPES:
            return u['type'] not in STATIC_TYPES
        return u['addr'] in self._moved

    def _own_on_screen(self, rows):
        """画面可见区里（顶栏和操作栏之间，按 camframe 的屏幕模型）有几个我方的活单位。"""
        at = self._cam_at()
        if not at:
            return 0
        pts = [(u['x'], u['y']) for u in rows
               if u['owner'] == self.own_owner and u['hp'] > 0 and not self._is_prop(u)
               and abs(u['x'] - at[0]) < 3000 and abs(u['y'] - at[1]) < 3000]
        return camframe.visible(pts, at[0], at[1], self.cur_distance or self.distance) if pts else 0

    def _shot_visible(self):
        """当前落点本地玩家看不看得见（问引擎）。没落点/关了检查/问不到都算看得见。"""
        if not self.shot or not self.fog_check or self.mode != 'auto':
            return True
        v = camctl.is_visible(self.shot['x'], self.shot['y'])
        return True if v is None else bool(v)

    def _sample(self, rows, now, mode):
        """每拍记一笔。空镜头连续多久是这里唯一真正关心的数字。"""
        sp = self.samples
        if sp is None:
            sp = self.samples = {"t0": now, "ticks": 0, "empty": 0,
                                 "streak": 0.0, "maxStreak": 0.0, "byMode": {}}
        sp["ticks"] += 1
        sp["byMode"][mode] = sp["byMode"].get(mode, 0) + 1
        # ★ 与模式无关：注视点在同一小片区域待了多久。
        #   emptyPct 抓不到"跟着英雄但英雄不动"这种 —— 附近有我方单位，
        #   不算空镜头，可画面几分钟没变。度量要直接对着观众看到的现象。
        spot = self.shot
        if spot:
            last = sp.get("lastSpot")
            if last and math.hypot(spot["x"] - last[0], spot["y"] - last[1]) < 600.0:
                sp["sameSpot"] = sp.get("sameSpot", 0.0) + self.tick
            else:
                sp["sameSpot"] = 0.0
                sp["lastSpot"] = (spot["x"], spot["y"])
            sp["maxSameSpot"] = max(sp.get("maxSameSpot", 0.0), sp.get("sameSpot", 0.0))
        self.shot_near = self._own_near(rows, self.shot, self.empty_radius)
        if self.shot_near == 0:
            sp["empty"] += 1
            sp["streak"] += self.tick
            sp["maxStreak"] = max(sp["maxStreak"], sp["streak"])
        else:
            sp["streak"] = 0.0
        # 迷雾统计用引擎的答案（shot_vis 在主循环开头问过）——
        # "镜头在迷雾里待很久"从此是一个能量出来的数，不靠感觉
        if self.shot and self.shot_vis is False:
            sp["fog"] = sp.get("fog", 0) + 1
            sp["fogStreak"] = sp.get("fogStreak", 0.0) + self.tick
            sp["maxFogStreak"] = max(sp.get("maxFogStreak", 0.0), sp["fogStreak"])
        else:
            sp["fogStreak"] = 0.0

    def _finish_report(self, now):
        """一局结束：出报告、存盘、按报告调参。"""
        sp = self.samples
        self.samples = None
        if not sp or sp["ticks"] < 20:          # 太短的局没有统计意义
            LEDGER.close_match()
            self.counts = {}
            return
        dur = max(1.0, now - sp["t0"])
        rep = {
            "durationS": round(dur, 1),
            "ticks": sp["ticks"],
            "emptyPct": round(100.0 * sp["empty"] / sp["ticks"], 1),
            "maxEmptyStreakS": round(sp["maxStreak"], 1),
            "maxSameSpotS": round(sp.get("maxSameSpot", 0.0), 1),
            "fogPct": round(100.0 * sp.get("fog", 0) / sp["ticks"], 1),
            "maxFogStreakS": round(sp.get("maxFogStreak", 0.0), 1),
            "fogCuts": self.stats.get("fogCuts", 0),
            "fogSkips": self.stats.get("fogSkips", 0),
            "shots": self.stats["shots"],
            "switchesPerMin": round(60.0 * self.stats["shots"] / dur, 1),
            "orbits": self.stats["orbits"],
            "creeps": self.stats["creeps"],
            "heroPct": round(100.0 * sp["byMode"].get("hero", 0) / sp["ticks"], 1),
            "avgHoldS": round(dur / max(1, self.stats["shots"]), 1),
            # 卡顿：一拍本该 0.6 秒，超过 2 秒就是卡住了。
            # frozenS 是这一局里画面被冻住的总时长 —— 它和 emptyPct 是
            # 两种完全不同的"镜头不动"，必须分开看。
            "maxTickS": round(sp.get("maxTick", 0.0), 2),
            "avgTickS": round(sp.get("tickSum", 0.0) / max(1, sp["ticks"]), 2),
            "slowTicks": sp.get("slowTicks", 0),
            "frozenS": round(sp.get("slowSum", 0.0), 1),
            "byMode": sp["byMode"],
            # ⚠ tick（节拍）必须在这里：自动调参会改它，而它决定**反应有多快**。
            #   09-20 之前没打印，于是它被悄悄顶到 1.5 秒、30 小时没人看见。
            "params": {"tick": self.tick, "staleAfter": self.stale_after,
                       "idleSeconds": self.idle_seconds,
                       "minShot": self.min_shot, "switchRatio": self.switch_ratio,
                       "orbitSeconds": self.orbit_seconds},
            "gapS": round(sp.get("gapSum", 0.0) / max(1, sp["ticks"]), 2),
        }
        rep["stamp"] = LEDGER.stamp
        rep["counts"] = dict(self.counts)       # 0 就是那条路一次都没走过
        rep["tuned"] = self._autotune(rep)
        self.report = rep
        self.reports = (self.reports + [rep])[-30:]
        try:
            path = os.path.join(os.path.dirname(STATE_FILE), "director-stats.jsonl")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rep, ensure_ascii=False) + "\n")
        except Exception:                                   # noqa: BLE001
            pass
        LEDGER.close_match(rep)
        self.counts = {}
        for k in self.stats:
            self.stats[k] = 0

    def _autotune(self, rep):
        """按这一局的报告往好的方向挪一步。

        **一次只挪一小步，而且有上下界。** 自动调参最容易出的问题是来回震荡：
        这一局嫌空镜头多就把阈值调死，下一局嫌切得太碎又调回去。
        所以每项每局最多动一格；并且"空镜头"优先于"切太碎"——
        对着黑屏比多切一次镜头难受得多。
        """
        # 卡顿优先于选点：画面被冻住时，怎么选点都没意义。
        # 能做的是**少调用** —— 关掉纠偏（每拍省 2 次往返）、把节拍拉长。
        # ★ 09-20 重做（实战中视角切换总是滞后两三秒）：原来是**棘轮**——
        #   一局里只要有几拍超过 2 秒（换局、加载、游戏自己卡一下都会）就 +0.2，上限 1.5；
        #   要降回去得整整一局一拍都不卡（frozenS < 2），而且下限还是 0.6。
        #   实测 1 号实例的导播连着跑了 30 小时 200 多局，节拍被顶到 1.5 再没下来：
        #   每拍处理只花 0.45 秒，两拍之间却隔 1.9 秒 —— 反应慢的三分之二是它自己加的。
        #   而且报表里**根本没打印 tick**，账本上看不出来（现在打了）。
        #   现在按"卡的拍占多大比例"判，只有连着卡才拉长（上限 0.6），一局不那么卡就往回收（下限 = tick_base）。
        ticks = max(1, rep.get("ticks", 0))
        slow_share = rep.get("slowTicks", 0) / float(ticks)
        if slow_share > 0.08 or rep.get("frozenS", 0) > 30.0:
            self._tune('tick', min(0.6, round(self.tick + 0.1, 2)))
            if rep.get("frozenS", 0) > 60.0 and self.correct_drift:
                self._tune('correct_drift', False)
        elif self.tick > self.tick_base:
            self._tune('tick', max(self.tick_base, round(self.tick - 0.1, 2)))

        # 画面长时间没变 —— 不管是哪种模式造成的，都要收紧。
        if rep.get("maxSameSpotS", 0) > 40.0:
            self._tune('hero_max_hold', max(20.0, self.hero_max_hold - 5.0))
            self._tune('hero_cooldown', min(90.0, self.hero_cooldown + 10.0))
            self._tune('stale_after', max(3.0, self.stale_after - 0.5))

        # 跟随占了太多镜头时间也是一种"待很久"，收紧硬上限。
        if rep.get("heroPct", 0) > 45.0:
            self._tune('hero_max_hold', max(20.0, self.hero_max_hold - 5.0))
        elif rep.get("heroPct", 0) < 10.0 and self.hero_max_hold < 45.0:
            self._tune('hero_max_hold', min(45.0, self.hero_max_hold + 5.0))

        before = (self.stale_after, self.idle_seconds, self.min_shot, self.orbit_seconds)
        if rep["maxEmptyStreakS"] > 5.0 or rep["emptyPct"] > 25.0:
            self._tune('stale_after', max(3.0, self.stale_after - 1.0))
            self._tune('idle_seconds', max(4.0, self.idle_seconds - 1.0))
            if rep["maxEmptyStreakS"] > 9.0:
                self._tune('orbit_seconds', max(6.0, self.orbit_seconds - 2.0))
        elif rep["maxEmptyStreakS"] < 2.5 and rep["switchesPerMin"] > 14.0:
            # 已经不空了，但切得太碎 —— 往回收一点
            self._tune('min_shot', min(6.0, self.min_shot + 0.5))
            self._tune('stale_after', min(8.0, self.stale_after + 0.5))
        after = (self.stale_after, self.idle_seconds, self.min_shot, self.orbit_seconds)
        if before == after:
            return None
        self.save()
        return {"staleAfter": self.stale_after, "idleSeconds": self.idle_seconds,
                "minShot": self.min_shot, "orbitSeconds": self.orbit_seconds}

    # -- 镜头纠偏 ----------------------------------------------------------
    def _remember_aim(self):
        """记下镜头**实际**停在哪。读一次比猜一次值。

        锁着单位时不读：引擎还在缓过去，读到的是半路上的位置；锚点一丢，纠偏会把镜头
        "拉回"这个半路位置。这时记镜头目标（frame_at，每拍跟着锚点更新）。"""
        if self.anchor and self.frame_at:
            self.aim = self.frame_at
            return
        # 09-20：原来两次 native 调用（两趟进程往返）读 x 和 y —— 换成读一次（_cam_at）。
        # 顺带它们不再在推镜途中分两次读到不一致的坐标。
        cam = self._cam_at()
        if cam:
            self.aim = (cam[0], cam[1])

    def _fix_drift(self, now):
        """人手动过镜头就拉回来。返回 True 表示这一拍已经用掉了。"""
        if not self.correct_drift:
            return False
        # 锁着单位的时候镜头本来就跟着它走（引擎在拉），位置不用纠 —— 纠了反而和引擎抢；
        # 跟仗的推镜还在走（_follow_fight）的时候也一样：镜头本来就在半路上
        if self.aim and not self.anchor and now >= self.pan_until:
            cam = self._cam_at()            # 一拍只读一次（原来这里两次 native 往返）
            if not cam:
                return False
            x, y = cam
            moved = math.hypot(x - self.aim[0], y - self.aim[1])
            fa = self.frame_at
            if moved > self.drift_tol and fa and math.hypot(x - fa[0], y - fa[1]) <= self.drift_tol:
                # ★ 镜头就在导播要它在的地方（frame_at），是基准记旧了，不是被人挪了 —— 更新基准，不拉。
                #   09-18 晚：解锁那一刻基准没跟上，这里把镜头从营地拉回了 3700 外的集合点（见 _unlock_here）。
                #   只信 frame_at 也不行：引擎会按地图边界夹取（上面"镜头纠偏"那段），所以两个都不像才算被挪了。
                self.aim = (x, y)
                self._drift_n = 0
                self._bump('纠偏·镜头就在该在的地方，是基准记旧了，不拉')
            elif moved > self.drift_tol:
                # 连着两拍都偏才拉：一拍的读数可能落在推镜半路上
                self._drift_n += 1
                if self._drift_n >= 2:
                    self._drift_n = 0
                    to = self.aim
                    camctl.glide(to[0], to[1], seconds=0.4, ease='inout')
                    self._remember_aim()
                    self.frame_at = self.aim
                    self.note = '镜头被移动了 %d，已拉回' % int(moved)
                    self._bump('纠偏·镜头被挪走了，拉回')
                    # 这一下原来不记账 —— 09-18 晚它把镜头拉回空地，账本里一个字都没有，只有探针看得见
                    self._log_move('drift', round(moved), '镜头被挪走了，拉回', frm=[round(x), round(y)],
                                   to=[round(to[0]), round(to[1])])
                    return True
            else:
                self._drift_n = 0
        # 高度不用每拍都查：一次往返 ~50 毫秒，而人不会每半秒滚一次滚轮。
        self.tick_n += 1
        # ★ 基准是"当前这一档"（Step 4 之前是固定的 self.distance —— 那样刚拉成全景，
        #   下一次检查就被拉回 2600）。换档的推拉还在进行时读到的是中间值，也别去纠正它。
        settling = now - self.size_since < self.size_seconds + 0.6
        if self.cur_distance > 0 and not settling and self.tick_n % self.zoom_every == 0:
            d = camctl.get_field(camctl.F_DISTANCE)
            if d and abs(d - self.cur_distance) > self.zoom_tol:
                camctl.set_field(camctl.F_DISTANCE, self.cur_distance, 0.4)
                self.note = '高度被改成 %d，已改回 %d' % (int(d), int(self.cur_distance))
                return True
        return False

    # -- 英雄打野 ----------------------------------------------------------
    def _update_home(self, cur):
        """我方基地重心 = 自家**不动**的单位的重心。

        扫描不区分建筑和单位，但建筑的坐标一拍都不会变 —— 与其去猜哪个
        rawcode 是建筑（还要维护一张表），不如直接观察"谁没动过"。
        """
        still = []
        nxt = {}
        for addr, u in cur.items():
            if u['owner'] != self.own_owner:
                continue
            # 开局以来挪过 60 以上的就是"会动的"（建筑一辈子不挪）—— 闲时认部队的退路用它
            p0 = self._first_pos.setdefault(addr, (u['x'], u['y']))
            if addr not in self._moved and abs(p0[0] - u['x']) + abs(p0[1] - u['y']) > 60.0:
                self._moved.add(addr)
            old = self._still.get(addr)
            n = 0
            if old and abs(old[0] - u['x']) < 1.0 and abs(old[1] - u['y']) < 1.0:
                n = old[2] + 1
            nxt[addr] = (u['x'], u['y'], n)
            if n >= 4:
                still.append(u)
        self._still = nxt
        if still:
            self.home = (sum(u['x'] for u in still) / len(still),
                         sum(u['y'] for u in still) / len(still))

    def _creeps_near(self, rows, x, y, radius):
        """身边的野怪。

        ★ 原来只排掉了金矿 ngol，于是商店 ngme、喷泉 nfoh、地精实验室 ngad
          （都是中立被动、都是 150000 血）统统算成"野怪" —— 英雄在商店旁边挨一下打
          就会被判成"在打野"并锁定跟随。改用同一个 _is_prop（同一条规则
          别写两份，只改一处等于没改）。
        """
        r2 = radius ** 2
        return [u for u in rows
                if u['owner'] >= 12 and not self._is_prop(u)
                and (u['x'] - x) ** 2 + (u['y'] - y) ** 2 < r2]

    def _hysteresis(self):
        """回家半径不能大过出门距离的四分之三 —— 两个阈值必须留出死区。"""
        return min(self.home_radius, self.away_from_base * 0.75)

    def _hero_step(self, rows, events, now):
        """返回 True 表示"这一拍由英雄跟随接管，别再做事件切换"。"""
        heroes = [u for u in rows if u['owner'] == self.own_owner and u['hero'] and u['hp'] > 0]
        by_addr = {u['addr']: u for u in rows}

        if self.mode == 'hero':
            hero = by_addr.get(self.hero_addr)
            if not hero:
                return self._hero_end('英雄没了', cooldown=False)
            # ★ 英雄阵亡：尸体还在扫描里（hp=0），原来会盯着尸体跟满 45 秒 ——
            #   自己人死了就没视野了，这时就该切走。
            if hero['hp'] <= 0:
                return self._hero_end('英雄阵亡，没视野了', cooldown=False)
            # ★ 跟随时镜头在英雄身上，shot 的坐标要跟着走。原来它停在锁定那一刻，
            #   英雄跑多远就差多远，于是：
            #   * 放开跟随后算"离下一个镜头多远"用的是旧坐标 —— 该滑的硬切了、该切的滑了；
            #   * "同一处待了多久"（maxSameSpotS）把跟着英雄跑了半张地图也算成"没动"，
            #     而自动调参正是按这个数收紧跟随上限的。
            if self.shot:
                self.shot['x'], self.shot['y'] = hero['x'], hero['y']
            # 刚跟上就判收工是没意义的：镜头才刚过去，观众什么都没看到。
            # ★ 维持条件是"英雄附近**真的在打**"，不是"附近有野怪"。
            #   后者的问题：英雄站在营地旁边不动，野怪一直在，于是永远收不了工。
            r2 = self.creep_radius ** 2
            if any((e['x'] - hero['x']) ** 2 + (e['y'] - hero['y']) ** 2 < r2
                   for e in events):
                self.hero_seen = now
            # 按大脑意图跟上的：大脑还在坚持同一个打算，就算"还在进行"（赶路途中没有掉血）
            intent_on = self.follow_src == 'intent' and self._hero_intent_alive(heroes, now)
            if intent_on:
                self.hero_seen = now
            # ★ 跟随期间别处打起了大仗 —— 放开。原来跟随会无视别处最多 45 秒，
            #   意图跟随让锁得更早更多，这个口子必须一起补上。
            fight = self._fight_now(hero if self.follow_src == 'event' else None)
            if not fight and self.follow_src == 'intent':
                # 打野那种仗单拍分数低（3~8 分），按"正在打"算；**任何地方**都算，不只英雄身边
                fight = self._live_fight_any(now)
            if fight:
                # 放开之后这一拍接着走事件切换（_step 里 _hero_step 返回 False），
                # 同一拍就切到整场仗的外框上，不用等下一拍
                return self._hero_end('打起来了，改成框住整场仗：' + fight)
            self._keep_framed(now)
            if now - self.shot_at < self.hero_min_hold:
                return True
            if now - self.shot_at > self.hero_max_hold:
                return self._hero_end('跟够久了，换个镜头')
            # 出发去打野/出击时英雄就在家门口 —— 赶路途中别按"回家了"放开
            heading_out = intent_on and (self.follow_key or ('',))[0] in ('creep', 'attack', 'gather')
            if self.home and not heading_out and math.hypot(
                    hero['x'] - self.home[0], hero['y'] - self.home[1]) < self._hysteresis():
                return self._hero_end('英雄回家了')
            if now - self.hero_seen > self.creep_end_grace:
                return self._hero_end('野打完了' if self.follow_src == 'event'
                                      else '大脑的打算变了')
            return True

        if self._hero_intent_step(heroes, now):
            return True
        if not self.hero_creep_lock:
            return False
        if not self.home or not heroes:
            return False
        # 挑"离家最远、身边有野怪"的那个英雄。带着小兵的优先 ——
        # 那种才是成规模的打野，也最好看。
        best, best_key = None, None
        r2 = self.creep_radius ** 2
        for h in heroes:
            if now < self.hero_block.get(h['addr'], 0.0):
                continue                     # 刚收工，冷却中
            away = math.hypot(h['x'] - self.home[0], h['y'] - self.home[1])
            if away < self.away_from_base:
                continue
            if not self._creeps_near(rows, h['x'], h['y'], self.creep_radius):
                continue
            # ★ 还必须**真的在打**。只看"身边有野怪"的话，英雄站在营地旁边
            #   不动也算在打野 —— 那正是收工后立刻又锁回去的原因。
            if not any((e['x'] - h['x']) ** 2 + (e['y'] - h['y']) ** 2 < r2
                       for e in events):
                self._bump('打野跟随·英雄身边没在打')
                continue
            escort = sum(1 for u in rows
                         if u['owner'] == self.own_owner and not u['hero']
                         and (u['x'] - h['x']) ** 2 + (u['y'] - h['y']) ** 2 < 900.0 ** 2)
            key = (escort, away)
            if best_key is None or key > best_key:
                best, best_key = h, key
        if not best:
            return False
        return self._hero_lock(best, now, '跟拍英雄打野（带 %d 个小兵）' % best_key[0],
                               'event', '英雄打野', escort=best_key[0], away=best_key[1])

    def _hero_lock(self, h, now, note, src, reason, size='mid', escort=0, away=0.0):
        """锁定跟随一个英雄。两条路共用：打起来了才锁（event）/ 大脑说要去了就锁（intent）。"""
        # 跟随也给底部操作栏让位：让英雄出现在可见区正中（camframe），偏移按跟上之后那一档的距离算
        dist = self.dist_wide if size == 'wide' else self.distance
        off = camframe.aim_offset(dist)
        # ★ 先滑过去再锁（09-18 实机账本：直接锁，镜头离"英雄 + 偏移"几百就是一帧跳过去 ——
        #   引擎的锁定平滑挪 700 一帧到位）。英雄在走就带提前量；滑完按实际位置零位移上锁。
        ok = False
        cur = camctl.target_now() if self.smooth_move else None
        if cur:
            tx, ty = h['x'] + off[0], h['y'] + off[1]
            d = math.hypot(tx - cur[0], ty - cur[1])
            hp0, t0 = camctl.unit_pos(h['addr']), time.time()
            if d > self.move_min:
                v = self._velocity(h)
                lead = self._lead(self.glide_seconds)
                lx, ly = (tx + v[0] * lead, ty + v[1] * lead) if v else (tx, ty)
                if d > self.glide_max:
                    camctl.look_at(tx, ty, seconds=0)
                else:
                    self._glide(lx, ly, self.glide_seconds)
            c2, hp = camctl.target_now(), camctl.unit_pos(h['addr'])
            # ★ 滑过去的这段时间英雄走远了（出发那一刻还站着、没有提前量；3 倍速一秒近千）：
            #   先接着追一下再锁 —— 按这一段实测的速度带提前量。不追的话，锁的那一刻偏多少就一路偏多少
            #   （5-030 3:09 英雄一路贴在画面上沿）。
            if c2 and hp and hp0 and self.follow_fix and math.hypot(
                    c2[0] - hp[0] - off[0], c2[1] - hp[1] - off[1]) > self.resync_err:
                dt = max(time.time() - t0, 0.2)
                v2 = ((hp[0] - hp0[0]) / dt, (hp[1] - hp0[1]) / dt)
                lead = self._lead(self.resync_seconds)
                self._glide(hp[0] + off[0] + v2[0] * lead, hp[1] + off[1] + v2[1] * lead,
                            self.resync_seconds)
                self._bump('跟随·上锁前英雄走远了，接着追一下')
                c2, hp = camctl.target_now(), camctl.unit_pos(h['addr'])
            if c2 and hp:
                ok = camctl.follow(h['addr'], offx=c2[0] - hp[0], offy=c2[1] - hp[1])
            self._resync_at = now
        if not ok and not camctl.follow(h['addr'], offy=off[1]):
            return False
        if src == 'event':
            self.stats['creeps'] += 1
        self.mode = 'hero'
        self.hero_addr = h['addr']
        self.hero_seen = now
        self.follow_src = src
        self.frame_at = None        # 镜头归引擎跟随管，放开后由下一次切镜重新算
        self.fight_ids = set()
        self.anchor = self.lock_want = None      # 英雄跟随自己管（_hero_step），不走锚点那一套
        self.shot = {'x': h['x'], 'y': h['y'], 'score': 999.0, 'kind': 'hero', 'since': now}
        self.shot_at = now
        self.shot_last_event = now
        self._apply_size(size, reason, now)
        self.note = note
        self.shot_why = note
        self._bump('切镜·跟拍英雄打野' if src == 'event' else '切镜·按大脑意图跟英雄·' + reason)
        LEDGER.shot({'t': round(now, 2), 'gt': LEDGER.game_time(), 'ev': 'follow',
                     'kind': 'hero', 'x': round(h['x']), 'y': round(h['y']),
                     'score': 999.0, 'how': 'follow', 'addr': h['addr'],
                     'type': h['type'], 'escort': escort, 'away': round(away),
                     'reason': reason, 'why': note, 'rivals': [], 'mode': 'hero',
                     'src': src, 'size': self.size})
        return True

    def _hero_intent(self, heroes, now):
        """大脑这一刻打算让英雄去哪。返回 (目标点, 用哪个英雄, 人话, 景别, 身份) 或 None。

        09-18 设计要求：按英雄的意图提前跟上英雄视角。
        原来的跟随要等"英雄身边真的在打"才锁 —— 出发、赶路、到营地这一整段都看不到，
        镜头切过去时第一波已经打完了。而大脑在英雄**出发时**就发布了打算：
          * attack.state = creep + 营地坐标（原话「打野（营地强度 6）」）
          * attack.state = attack / gather + 出击或集结的点
          * 意图原话里有「英雄…回家」（残血撤退）—— 跟那个血最少的英雄
        """
        d = INTENTS.read()
        if not d or not heroes:
            return None
        atk = d.get('attack') or {}
        state = atk.get('state')
        for it in (d.get('intents') or []):
            why = it.get('why') or ''
            if '英雄' in why and '回家' in why:
                h = min(heroes, key=lambda u: u['hp'] / float(max(1, u['hpMax'])))
                return ((h['x'], h['y']), h, '跟英雄撤退：' + why, 'mid', ('retreat', h['addr']))
        tgt, why = None, ''
        camp = atk.get('camp') or {}
        if state == 'creep' and isinstance(camp.get('x'), (int, float)):
            tgt, why = (camp['x'], camp['y']), '大脑要带英雄去打野（营地强度 %s）' % camp.get('strength')
        elif state in ('attack', 'gather'):
            for it in (d.get('intents') or []):
                if it.get('kind') in ('attack', 'attack_move') and \
                        isinstance(it.get('x'), (int, float)):
                    tgt, why = (it['x'], it['y']), '大脑要出击：' + (it.get('why') or '')
                    break
        if not tgt:
            return None
        cand = [u for u in heroes if now >= self.hero_block.get(u['addr'], 0.0)]
        if not cand:
            return None
        h = min(cand, key=lambda u: math.hypot(u['x'] - tgt[0], u['y'] - tgt[1]))
        key = (state, int(tgt[0] // 1500), int(tgt[1] // 1500))
        return tgt, h, why, 'mid', key

    def _fight_now(self, away_from=None):
        """这一拍有没有一场有我方参与、分数够得上"打起来了"的交火。返回它的人话。

        ★ 09-18 以前这里叫 _big_fight_elsewhere：只看跟随的英雄**以外**、而且要 36 分以上。
          英雄自己身边打起来反而继续锁着英雄 —— 而要求恰恰相反：
          交战时不应一直锁定英雄视角，要把交战内容完整放在画面中央。
        away_from：只算离这个点 cell×1.5 以外的仗。只有网页上手动打开 heroCreepLock
        （打野时跟英雄）才用得上 —— 那条路锁上的那一拍英雄本来就在打，不排除它自己身边，
        就会锁上、下一拍放开、反复闪。
        """
        for c in self.last_clusters:
            if c['mineN'] < 1 or c['w'] < self.hero_break_w:
                continue
            if away_from and math.hypot(c['x'] - away_from['x'],
                                        c['y'] - away_from['y']) <= self.cell * 1.5:
                continue
            return self._why(c)
        return ''

    def _hero_intent_step(self, heroes, now):
        """按大脑的意图提前锁英雄。同一个打算只锁一次（跟完、被放开之后不再为它锁）。"""
        if not self.hero_intent:
            return False
        got = self._hero_intent(heroes, now)
        if not got:
            return False
        tgt, h, why, size, key = got
        for k in [k for k, t in self.hero_intent_done.items() if now - t > 60.0]:
            del self.hero_intent_done[k]
        if key in self.hero_intent_done:
            return False
        if now < self.hero_block.get(h['addr'], 0.0):
            return False
        # 这一刻正看着一场还在打的仗、或者别处正打着大仗 —— 不为"要去打野"把它拽走
        # （不记"跟过了"，等仗打完还来得及跟）
        if self._fight_now() or (
                self.shot and self.shot.get('kind') == 'combat'
                and now - self.shot_last_event < self.stale_after):
            self._bump('意图跟随·正在看仗，先不跟')
            return False
        self.hero_intent_done[key] = now
        self.follow_key = key
        return self._hero_lock(h, now, why, 'intent', why.split('（')[0].split('：')[0],
                               size=size)

    def _hero_intent_alive(self, heroes, now):
        """意图跟随期间：大脑还在坚持同一个打算吗。"""
        got = self._hero_intent(heroes, now)
        return bool(got) and got[4] == self.follow_key

    def _hero_end(self, why, cooldown=True):
        """放开跟随。

        ★ 09-18 起用 camctl.unfollow（跟随对象传 0）解除，镜头原地不动、高度不变。
          原来以为"解除跟随只有 ResetToGameCamera 这一条路"，而它会把高度角度一起恢复成
          游戏默认（1650），只好紧跟着再推回当前档 —— 每次放开英雄画面都会抽一下焦距。
          实测：跟随对象传 0 就能解除跟随，这条路不碰高度。
        """
        now = time.time()
        self._bump('跟随结束·' + why)
        LEDGER.shot({'t': round(now, 2), 'gt': LEDGER.game_time(), 'ev': 'release',
                     'kind': 'hero', 'how': 'release', 'addr': self.hero_addr,
                     'heldPrev': round(now - self.shot_at, 1) if self.shot_at else None,
                     'reason': why, 'why': why, 'rivals': [], 'mode': 'hero'})
        camctl.unfollow()
        if cooldown and self.hero_addr:
            self.hero_block[self.hero_addr] = time.time() + self.hero_cooldown
        self.mode = 'auto'
        self.hero_addr = None
        self._remember_aim()
        self.shot_at = 0.0          # 下一个事件可以立刻切，不受最短停留约束
        if self.shot:
            self.shot['score'] = 0.0
        self.note = why
        return False

    # -- 景别 --------------------------------------------------------------
    def _size_for(self, best, now):
        """这个镜头该用哪一档。返回 (档, 为什么)。

        判据只用场面本身的量（几个人），不猜"好不好看"：
          * 全景：大团战（双方加起来 >= wide_crowd 人，中景框不下）；
                  预判相遇/集结/家被威胁（要把"正在过来的"和"等着的"一起框进来）；
                  开局头几秒的闲时镜头（定场）
          * 其余：中景
        （原来的"特写"档 09-18 已经去掉，见 __init__ 景别那段。）
        """
        if not self.sizes_enabled:
            return 'mid', ''
        kind = best.get('kind')
        if kind == 'anticipate':
            if best.get('bk') in ('meet', 'gather', 'threat'):
                return 'wide', '预判：把正在过来的和等着的一起框进来'
            return 'mid', ''
        cl = best.get('cl')
        if cl:
            if cl['crowd'] >= self.wide_crowd:
                return 'wide', '大团战（双方 %d 人）' % cl['crowd']
            # 装不装得下按投影算（camframe）：同样 1500 深，靠镜头那边比远处占的屏幕多得多
            if cl.get('pts') and not self._frame(best, self.distance)[2]:
                return 'wide', '场面太大，中景装不下（%d 人，东西 %d 南北 %d）' % (
                    cl.get('n', 0), cl.get('bw', 0), cl.get('bh', 0))
        if kind == 'idle' and self.match_at and now - self.match_at < self.opening_wide_s:
            return 'wide', '开局定场'
        return 'mid', ''

    def _apply_size(self, size, why, now):
        """换档。**只在档位真的变了才下命令**（每次都发，高度就会一直在推拉）。"""
        if size != 'wide':
            size = 'mid'
        if size == self.size:
            return False
        dist = self.dist_wide if size == 'wide' else self.distance
        camctl.set_field(camctl.F_DISTANCE, dist, self.size_seconds)
        self._bump('景别·换到' + ('全景' if size == 'wide' else '中景'))
        self.size, self.cur_distance, self.size_since = size, dist, now
        self.size_why = why
        return True

    # -- 运镜 --------------------------------------------------------------
    def _anchor_for(self, best):
        """跟着谁走（锁定跟随）。只给"跟着一队人走"的镜头：部队、英雄、预判；打仗、看基地不锁。

        在场面中心（best 的 x, y）anchor_radius 以内，取离中心最近的自家会动的非农民单位
        （开局以来挪过 = _moved，或英雄）。返回单位 dict，找不到返回 None。
        为什么打仗不锁：锁住的是一个人，打仗时他前后挪、被拉残血拉走，镜头会跟着晃；
        仗由构图（_track）框住，不需要跟某一个人。
        """
        kind = best.get('kind')
        why = best.get('why') or ''
        if kind not in ('idle', 'anticipate') or '基地' in why:
            return None
        cx, cy = best['x'], best['y']
        r2 = self.anchor_radius ** 2
        pool = [u for u in self.rows_now
                if u['hp'] > 0 and u['owner'] == self.own_owner and u['type'] not in WORKER_TYPES
                and (u['hero'] or u['addr'] in self._moved)
                and (u['x'] - cx) ** 2 + (u['y'] - cy) ** 2 < r2]
        if not pool:
            return None
        return min(pool, key=lambda u: (u['x'] - cx) ** 2 + (u['y'] - cy) ** 2)

    def _follow_anchor(self, cur):
        """每拍开头：该上锁就上锁、该解锁就解锁，锁着就把"在看哪儿"跟着单位挪。

        ★ 只在单位**真的在走**的时候锁（09-18 实机）：第一版滑到位就锁，可那队兵其实站着，
          原地挪步调整队形，镜头跟着前后晃几十到一百 —— 画面上就是反复挪一小段，像抖动。
          现在：
          * 想锁的单位（lock_want，这个镜头要跟的那队人里离中心最近的）这一拍挪了 lock_move 以上
            才锁：偏移 = 镜头目标 - 它**现在**的位置，位移为 0，引擎不会有任何跳动
            （不能按滑镜那一刻的位置锁：滑镜 0.5 秒里人走了几百，引擎超过约 600 直接瞬移，实测）；
          * 锁着的：frame_at（镜头目标）和 shot（在看哪儿）跟着它挪，不另外读镜头；
            它每拍挪不到 still_move、累计 unlock_still_s 秒 —— 停下了，解锁，镜头停住；再走起来再锁；
          * 锁住的单位死了/没了：解锁，镜头停在原地。
        """
        if self.mode != 'auto':
            self.lock_want = None
            return
        prev = self.prev or {}
        a = self.anchor
        if not a:
            w = self.lock_want
            if not w or not self.frame_at:
                return
            u = cur.get(w['addr'])
            if not u or u['hp'] <= 0:
                self.lock_want = None
                return
            pu = prev.get(w['addr'])
            if not pu or math.hypot(u['x'] - pu['x'], u['y'] - pu['y']) < self.lock_move:
                return                               # 站着：不锁，镜头不动
            if self._lock_now(u['addr'], u['type'], '走起来了，锁住跟着走', want=w.get('want')):
                self._bump('锁定·走起来了，锁住跟着走')
            return
        u = cur.get(a['addr'])
        if not u or u['hp'] <= 0:
            self._bump('锁定·锚点单位没了，镜头停在原地')
            self._unlock_here('锁住的单位没了')
            return
        dx, dy = u['x'] - a['ax'], u['y'] - a['ay']
        self.frame_at = (u['x'] + a['ox'], u['y'] + a['oy'])
        if self.shot:
            self.shot['x'], self.shot['y'] = a['sx'] + dx, a['sy'] + dy
        now = time.time()
        pu = prev.get(a['addr'])
        if pu and math.hypot(u['x'] - pu['x'], u['y'] - pu['y']) < self.still_move:
            a['still'] += now - a['seen']
        else:
            a['still'] = 0.0
        a['seen'] = now
        if a['still'] >= self.unlock_still_s:
            want = self.lock_want            # lock_want 留着：再走起来再锁
            self._unlock_here('停下了，解锁')
            self.lock_want = want
            self._bump('锁定·停下了，解锁（镜头停住，不跟着原地挪步晃）')

    def _unlock_here(self, why):
        """解锁，镜头停在它此刻所在的地方 —— 而且导播**记的**镜头位置（frame_at）和纠偏的基准（aim）
        都改成此刻的**实际**位置。锁着的时候镜头归引擎拉，只有放开这一刻知道它到底在哪。

        ★ 09-18 晚（5-021 在二矿位置打野时，视角一直停在上方的草地上）：
          锁住一队兵从集合点跟到营地，野怪一打起来"就地换成交火镜头"、解锁 —— 那条路只调了
          camctl.unfollow()，纠偏的基准还是**上锁那一刻**的位置；下一拍 _fix_drift 看镜头"被挪走了"
          一千多，把它拉回集合点。frame_at 却还是营地，于是构图以为仗就在画面里，一直不重新对准。
          实测：镜头跟到 (-4671,-2516)，解锁后一秒被拉回 (-4162,1184)，差 3700，
          停了 6 秒。三处解锁各写各的（同一条规则写在三处，只改一处等于没改），现在都走这里。
        """
        a = self.anchor
        camctl.unfollow()
        self.anchor = self.lock_want = None
        cam = camctl.target_now()
        if cam:
            self.frame_at = (cam[0], cam[1])
        if self.frame_at:
            self.aim = self.frame_at
        if a:
            self._log_move('unlock', a['type'], why)

    def _move(self, tx, ty, best, seconds, stay=None):
        """把镜头目标挪到 (tx, ty)。返回 (怎么挪的, 挪了多远)。

          * glide_max 以内：滑过去（'glide'，camctl.glide 段与段重叠，晚到了也不停在半路）；
          * 更远：直接切（'cut'）。
        挪完如果这是"跟着一队人走"的镜头（_anchor_for），那队人走起来就零位移上锁（_follow_anchor）。
        推镜和定位都会先解掉引擎里的锁定（实测），不用另外解。
        """
        cur = self._cam_at() if self.frame_at else None     # 实际在哪（记的会错，见 _cam_at）
        if cur is None:
            x = camctl.CTL.read_f(camctl.N_TARGET_X)
            y = camctl.CTL.read_f(camctl.N_TARGET_Y)
            cur = (x, y) if x is not None and y is not None else None
        d = math.hypot(tx - cur[0], ty - cur[1]) if cur else 1e9
        a = self._anchor_for(best) if self.smooth_move else None
        # 离得很近就不动镜头（09-18 实机账本：「当前落点 4 秒没动静」切到原地附近，发了一次只挪 2~40 的滑镜，
        #   观感就是镜头轻轻一顿）。切镜时 stay = cut_stay（500）：新落点离现在的画面这么近，本来就在画里，
        #   只换"在看哪儿"的说法，不挪镜头；真有人出画 _track 会接着管。
        # 想要的偏移 = 构图目标 - 锚点现在的位置：锁上之后按它核对有没有跑偏（_keep_framed）
        want = (tx - a['x'], ty - a['y']) if a else None
        if d < (stay if stay is not None else self.move_min):
            self.lock_want = {'addr': a['addr'], 'want': want} if a else self.lock_want
            self._bump('运镜·离得很近，不动镜头')
            return 'stay', d
        v = self._velocity(a) if a else None
        # 那队人正在走：滑向"滑完时他们会到的地方"，滑完立刻锁 —— 不然滑到位停一下、下一拍认出在走
        #   才锁上再跟，画面是"走 - 停 - 走"（09-18 实机轨迹：到位后停 0.3~0.5 秒再动）。
        #   提前量按 glide 实际耗时（_lead：量出来的墙钟时长，没量过按 seconds × lead_factor）。
        lx, ly = tx, ty
        if v and d <= self.glide_max:
            lead = self._lead(seconds)
            lx, ly = tx + v[0] * lead, ty + v[1] * lead
        if d > self.glide_max:
            camctl.look_at(tx, ty, seconds=0)
            how = 'cut'
        else:
            self._glide(lx, ly, seconds)
            how = 'glide'
        self.anchor = None
        self.frame_at = (lx, ly)
        self.lock_want = {'addr': a['addr'], 'want': want} if a else None
        if v:
            # 滑完立刻零位移上锁（读镜头和单位此刻的实际位置），之后跟着走
            self._lock_now(a['addr'], a['type'], '在走，滑完直接锁上接着跟', best, want=want)
        self._bump('运镜·' + {'glide': '滑', 'cut': '切'}[how]
                   + ('（在走，滑完直接锁上接着跟）' if v else '（这队人走起来就锁住跟着走）' if a else ''))
        return how, d

    def _keep_framed(self, now):
        """锁着跟随的时候，被跟的人跑偏了就追上（见 __init__「跟随跑偏了就追上」）。返回追了没有。

        * 英雄跟随：想要的位置 = 英雄在可见区正中（camframe.aim_offset）；偏 resync_err 以上、或出了舒服区就追；
        * 锚点（跟一队人）：想要的偏移是构图那一刻算的（anchor['want']）；锚点只是离中心最近的那个人，
          不一定在正中，所以只在它出了舒服区才追。
        追 = 滑到"滑完时他会在的地方 + 想要的偏移"（提前量按量出来的滑镜时长），滑完零位移锁上 ——
        一下平滑的追赶，不是跳。两次之间至少 resync_min_s。
        """
        if not self.follow_fix or now - self._resync_at < self.resync_min_s:
            return False
        dist = self.cur_distance or self.distance
        if self.mode == 'hero' and self.hero_addr:
            addr, want, strict, typ = self.hero_addr, camframe.aim_offset(dist), True, '英雄'
        elif self.mode == 'auto' and self.anchor and self.anchor.get('want'):
            addr, want, strict, typ = self.anchor['addr'], self.anchor['want'], False, self.anchor['type']
        else:
            return False
        cam, up = camctl.target_now(), camctl.unit_pos(addr)
        if not cam or not up:
            return False
        p = camframe.project(up[0] - cam[0], up[1] - cam[1], dist)
        left, right, top, bottom = self.comfy
        inside = bool(p) and left <= p[0] <= right and top <= p[1] <= bottom
        err = math.hypot(cam[0] - up[0] - want[0], cam[1] - up[1] - want[1])
        if inside and (not strict or err < self.resync_err):
            return False
        u = (self.prev or {}).get(addr)
        v = self._velocity(u) if u else None
        lead = self._lead(self.resync_seconds) if v else 0.0
        vx, vy = v or (0.0, 0.0)
        tx, ty = up[0] + want[0] + vx * lead, up[1] + want[1] + vy * lead
        self._glide(tx, ty, self.resync_seconds)          # 滑镜会解掉引擎里的锁，下面重新锁
        c2, p2 = camctl.target_now(), camctl.unit_pos(addr)
        if self.mode == 'hero':
            if c2 and p2:
                camctl.follow(addr, offx=c2[0] - p2[0], offy=c2[1] - p2[1])
            else:
                camctl.follow(addr, offx=want[0], offy=want[1])
        elif c2 and p2:
            camctl.follow(addr, offx=c2[0] - p2[0], offy=c2[1] - p2[1])
            self.anchor['ox'], self.anchor['oy'] = c2[0] - p2[0], c2[1] - p2[1]
            self.frame_at = self.aim = (c2[0], c2[1])
        else:
            self.anchor = None                            # 读不到：不锁（lock_want 留着，走起来再锁）
            self.frame_at = (tx, ty)
        self._resync_at = now
        why = '出画了' if not p else '跑出舒服区' if not inside else '偏了 %d' % err
        self._bump('跟随·跑偏了，追上（%s）' % ('英雄' if self.mode == 'hero' else '锚点'))
        self._log_move('resync', typ, '跟随跑偏了，追上：' + why, err=round(err),
                       scr=[round(p[0], 2), round(p[1], 2)] if p else None)
        return True

    def _live_fight_any(self, now, near=None):
        """有没有一场**正在打**的仗（热度过 live_min、有我方的人）。返回人话；没有返回 ''。
        near 给了单位就只算它身边（fight_radius + 400）那一场。

        ★ 09-18 深夜（5-030 3:09）：跟着英雄去打野，到了营地开打，镜头还锁着英雄 —— 放开跟随的门槛是
          单拍交火 18 分，而打野那一团每拍只有 5~8 分（热度累加起来早就算"正在打"了）。
          「打仗时不锁英雄、框住整场仗」对打野一样成立。
        ★ 09-20 扩到**任何地方**：实测跟英雄的拍里有 11 拍别处正在打（最远 5596），最长连着 2.3 秒 ——
          单拍 18 分那个门槛同样挡住了它们。有仗就去看仗，别跟着英雄走路。
        """
        r = self.fight_radius + 400.0
        best = None
        for f in self._live_fights(now):
            if not f.get('mineN'):
                continue
            if near and math.hypot(f['x'] - near['x'], f['y'] - near['y']) > r:
                continue
            if best is None or f['heat'] > best['heat']:
                best = f
        if not best:
            return ''
        for c in self.last_clusters:
            if c.get('fid') == best['id']:
                return self._why(c)
        return '我方 %d vs %s %d' % (best.get('mineN', 0), '敌方' if best.get('pvp') else '野怪',
                                    best.get('foeN', 0) or best.get('creepN', 0))

    def _glide(self, x, y, seconds):
        """滑镜，顺带量它实际花了多少墙钟秒（进程调用会把名义时长拖长）—— 提前量要按实际的算。"""
        t0 = time.time()
        camctl.glide(x, y, seconds=seconds, ease='inout')
        took = time.time() - t0
        if took > 0.05:                      # 假镜头（测试）不花时间，不记
            self.glide_wall = took if not self.glide_wall else self.glide_wall * 0.7 + took * 0.3
        return took

    def _lead(self, seconds):
        """在走的人：滑镜提前量（秒）。按量出来的滑镜实际耗时，没量过就按 seconds × lead_factor。"""
        return max(seconds * self.lead_factor, self.glide_wall)

    def _lock_now(self, addr, typ, reason, best=None, want=None):
        """零位移上锁：偏移 = 镜头**此刻实际**的位置 - 单位**此刻实际**的位置（各读一次）。
        want：这个镜头想要的偏移（构图目标 - 单位位置），_keep_framed 拿它核对锁上之后有没有跑偏。

        ★ 09-18 实机账本：原来按导播记的"镜头应该在哪"（frame_at）算偏移，锁的那一刻镜头跳了 231、331 ——
          记的和实际差这么多（滑镜被拖长、镜头被地图边界挡住……）。引擎的平滑挪 700 就是一帧到位，
          所以偏移必须按实际位置算。读不到就不锁（镜头不动总比跳一下好）。
        """
        cam = camctl.target_now()
        up = camctl.unit_pos(addr)
        if not cam or not up:
            self._bump('锁定·读不到实际位置，不锁')
            return False
        ox, oy = cam[0] - up[0], cam[1] - up[1]
        camctl.SMOOTH['factor'] = self.smooth_factor
        camctl.follow(addr, offx=ox, offy=oy)
        src = best or self.shot
        sx, sy = (src['x'], src['y']) if src else cam
        self.anchor = {'addr': addr, 'ox': ox, 'oy': oy, 'ax': up[0], 'ay': up[1],
                       'sx': sx, 'sy': sy, 'type': typ, 'still': 0.0, 'seen': time.time(), 'want': want}
        self.frame_at = cam
        self._log_move('lock', typ, reason)
        return True

    def _velocity(self, u):
        """这个单位现在在走吗：上一拍到这一拍挪了 lock_move 以上就算，返回 (vx, vy) 每墙钟秒；不在走 None。"""
        po = (self.prev_old or {}).get(u['addr'])
        dt = self.prev_t - self.prev_old_t
        if not po or dt <= 0 or not self.prev_old_t:
            return None
        dx, dy = u['x'] - po['x'], u['y'] - po['y']
        if math.hypot(dx, dy) < self.lock_move:
            return None
        return dx / dt, dy / dt

    def _log_move(self, ev, what, reason, **kw):
        """不换镜头的镜头动作也记进切镜账本（重新对准 / 就地升级 / 上锁 / 解锁）。
        原来账本只有切镜，"切完 1 秒又挪了一下"是谁下的只能从 3 秒一批的计数器去猜。
        网页的"最近切镜"只列 cut/follow/release，这几种不会混进去。"""
        rec = {'t': round(time.time(), 2), 'gt': LEDGER.game_time(), 'ev': ev, 'what': what,
               'reason': reason, 'mode': self.mode, 'size': self.size}
        if self.shot:
            rec.update(kind=self.shot.get('kind'), x=round(self.shot['x']), y=round(self.shot['y']))
        rec.update(kw)
        LEDGER.shot(rec)

    def _same_place(self, a, b):
        return math.hypot(a['x'] - b['x'], a['y'] - b['y']) < self.cell * 1.5

    def _track(self, best, now, urgent=False):
        """只有打斗快出画了才重新对焦，而且是快速滑一下，不是慢慢漂。

        ★ 只跟**同一场仗**。原来这里不看是不是同一处 —— "最短停留"那条分支调它的时候，
          只要最高分的候选离得够远（>= 1600）就滑过去：
          * 最短停留等于没挡住（挡住的反而是近处的）；
          * 走的是 0.45 秒的滑镜，而设计上超过 3500 就该硬切 —— 实测一次滑了 5242，
            镜头在半秒里被甩过大半张地图；
          * 不算一次切镜：shot 的类型、分数、原因都还是上一个镜头的，账本里没有这一笔。
          09-18 十局账本：157 次镜头大跳里 17 次是这样换了地方却没记账的。
          判据放在这里（而不是调用方）：_track 的定义就是"跟住同一场仗"，
          以后谁调它都不会再误用。
        """
        if not self.shot:
            return
        # ★ 同一场仗按身份认（_current_fight 标的 current / 同一个仗编号），不按"离上次对准的地方多远"：
        #   09-18 深夜 5-033 29:00，仗一边打一边往东北挪了 4500，_fight_policy 认出"同一场仗铺得开，交给构图跟"，
        #   这里却因为离上次对准的地方超过 2100 判"不是同一场仗"不跟 —— 两边互相推，镜头在原地停了 20 游戏秒，
        #   直到画面空了才被强制切走（画面上：部队和敌人已经打到很远处，镜头还一直落在后面）。
        same = best.get('current') or (best.get('fid') and best.get('fid') == self.shot.get('fid'))
        if not same and not self._same_place(best, self.shot):
            self._bump('不跟·别处的候选不是同一场仗')
            return
        ids = (best.get('cl') or {}).get('ids')
        if ids:
            self.fight_ids = ids
        # 仗打大了、中景装不下 —— 往大里换（全景）；仗收拢了、中景宽宽松松装得下**持续** shrink_after_s
        # 才推回中景（_roomy_at_mid）。原来只放大不缩小（来回推拉比装不下还难受），可 09-18 起同一处
        # 不再切第二刀、一个镜头能跟很久 —— 对照世界里全景从 8% 涨到 33%，仗早散了还挂在全景上。
        size, size_why = self._size_for(best, now)
        grow = size == 'wide' and self.size != 'wide'
        pts = (best.get('cl') or {}).get('pts') or []
        shrink = not grow and self._roomy_at_mid(best, pts, now)
        tx, ty, _ = self._frame(best, self.dist_wide if grow else self.distance if shrink else None)
        # 两种情况要重新对：有参战的人已经在可见区外面了；或者整场仗挪了 reaim_dist 以上
        # 按镜头**实际**在哪算（_cam_at），不按记的 frame_at —— 两者差上千的时候，按记的算永远"还在画面里"
        at = self._cam_at() if self.frame_at else None
        out = 0
        if pts and at:
            out = len(pts) - camframe.visible(pts, at[0], at[1], self.cur_distance or self.distance)
        moved = math.hypot(tx - at[0], ty - at[1]) if at else 1e9
        # ★ 防"两段式抖动"（09-18：近距离挪镜头总是分两段，先挪一下、很短间隔内
        #   又挪一小段，像抖动）。实机轨迹：每次切过去之后约 1.5 秒必补一刀
        #   「仗挪了/有人出画了，重新对准」，第二下只挪 100~400 —— 刚开打那一刻挨过打的人还没认全，
        #   一两拍后认全了，构图就挪一点。现在：
        #   * 挪不到 reaim_small（350）一律不动 —— 略偏一点比抖一下好；
        #   * 刚对过焦 reaim_settle_s（3 秒）内只在"四分之一以上的人出画了"时才补，
        #     否则等满 3 秒；两次之间最少 reaim_min_s。
        many_out = bool(pts) and out >= max(1, self.many_out_frac * len(pts))
        if (self.fight_follow and not grow and not shrink and pts
                and self.shot.get('kind') in ('combat', 'event')):
            self._follow_fight(best, tx, ty, moved, out, now)
            return
        if not grow and not shrink:
            if moved < self.reaim_small:
                if out:
                    self._bump('不跟·有人出画但只差一点，不抖这一下')
                return
            if not many_out and moved < self.reaim_dist:
                return
        since = now - self.last_reaim
        if since < (1.0 if urgent else self.reaim_min_s) or (
                not urgent and since < self.reaim_settle_s and not (grow or shrink or many_out)):
            self._bump('不跟·刚对过焦，先不动')
            return
        if grow:
            self._apply_size(size, size_why, now)
        elif shrink:
            self._apply_size('mid', '仗收拢了，推回中景', now)
        self._roomy_since = 0.0
        self._move(tx, ty, best, self.reaim_seconds)
        self.shot['x'], self.shot['y'] = best['x'], best['y']
        self.last_reaim = now
        label = ('换全景装下整场仗' if grow else '仗收拢了，推回中景' if shrink else
                 '有人出画了，重新对准' if out else '仗挪了，重新对准')
        self._bump('构图·' + label)
        self._log_move('reaim', (best.get('cl') or {}).get('n'), label, dist=round(moved),
                       out=out, urgent=urgent)
        self._remember_aim()

    def _follow_fight(self, best, tx, ty, moved, out, now):
        """打起来了：镜头中心 = 打架的中心，仗挪到哪跟到哪（09-18 深夜的要求：一旦开打，
        镜头中心就对准交战中心）。

        原来按"防两段式抖动"的规矩：挪不到 600 不动、对过焦 4 秒内除非三分之一的人出画不补 ——
        对一场站着打的仗是对的，对一场边打边走的仗就是"镜头永远慢半拍、一挪一大截"。现在：
          * 离打架中心不到 fight_dead（250）不动 —— 人原地挪步、框子伸缩的小晃不跟；
          * 否则**每拍**都往打架中心推：用引擎的定时推镜（线性、不阻塞，时长 fight_follow_s），
            下一拍再按新的中心改道 —— 仗一直在挪，镜头就一直在平滑地走，不是走一下停一下；
          * 远到 glide_max 以外（仗整个换了地方）才直接切。
        推镜途中纠偏不插手（pan_until，见 _fix_drift）。
        """
        if moved < self.fight_dead:
            if out:
                self._bump('跟仗·有人出画但离中心不到 %d，不晃' % self.fight_dead)
            return
        if moved > self.glide_max:
            self._move(tx, ty, best, self.reaim_seconds)
            self._remember_aim()
            how = '切'
        else:
            camctl.look_at(tx, ty, seconds=self.fight_follow_s)
            self.anchor = self.lock_want = None           # 推镜会解掉引擎里的锁
            self.frame_at = (tx, ty)                      # aim 不动：推完镜头到了 frame_at，纠偏自己会更新基准
            self.pan_until = now + self.fight_follow_s
            how = '推'
        self.shot['x'], self.shot['y'] = best['x'], best['y']
        self.last_reaim = now
        self._bump('构图·跟着仗走（%s）' % how)
        self._log_move('reaim', (best.get('cl') or {}).get('n'), '跟着仗走', dist=round(moved), out=out, how=how)

    def _roomy_at_mid(self, best, pts, now):
        """现在是全景、而这场仗在中景里宽宽松松装得下（投影后占可见区不到 roomy_frac），
        并且已经这样持续 shrink_after_s、离上次换景别也有 shrink_after_s —— 该推回中景了。"""
        cl = best.get('cl') or {}
        if (self.size != 'wide' or not pts or not self.sizes_enabled
                or best.get('kind') not in ('combat', 'event') or cl.get('crowd', 0) >= self.wide_crowd):
            self._roomy_since = 0.0
            return False
        _, _, fits, ws, hs = camframe.frame(pts, self.distance)
        roomy = (fits and ws <= self.roomy_frac * (camframe.VIS_R - camframe.VIS_L)
                 and hs <= self.roomy_frac * (camframe.VIS_BOT - camframe.VIS_TOP))
        if not roomy:
            self._roomy_since = 0.0
            return False
        if not self._roomy_since:
            self._roomy_since = now
        return (now - self._roomy_since >= self.shrink_after_s
                and now - self.size_since >= self.shrink_after_s)

    def _cut_to(self, best, now, reason=''):
        held_prev = round(now - self.shot_at, 1) if self.shot_at else None
        d = 1e9
        if self.shot:
            d = math.hypot(best['x'] - self.shot['x'], best['y'] - self.shot['y'])
        # 先定景别：镜头目标要按"切过去之后那一档"的距离算（camframe）
        size, size_why = self._size_for(best, now)
        self._apply_size(size, size_why, now)
        self._roomy_since = 0.0             # 新镜头：推回中景的迟滞从头算
        ax, ay, _ = self._frame(best)
        self.fight_ids = (best.get('cl') or {}).get('ids') or set()
        self._hot = None
        # 近的锁单位交给引擎缓过去；中距离滑（带缓动，最后一段引擎落地）；远的直接切（_move）
        how, _moved_by = self._move(ax, ay, best, self.glide_seconds,
                                    stay=self.cut_stay if self.shot else None)
        key = {'glide': 'glides', 'cut': 'cuts', 'stay': 'stays'}[how]
        self.stats[key] = self.stats.get(key, 0) + 1
        self.last_reaim = now
        old = self.fights.get((self.shot or {}).get('fid'))
        if old and old['id'] != best.get('fid'):
            self.left_fights[old['id']] = now      # 防来回跳：刚离开的仗 pingpong_s 内不切回去
        self.shot = {'x': best['x'], 'y': best['y'], 'fid': best.get('fid'),
                     'score': best['score'], 'kind': best['kind'], 'since': now}
        self.shot_at = now
        self.shot_last_event = now      # 刚切过去，先给它一个完整的过期窗口
        self._remember_aim()
        self.stats['shots'] += 1
        self.combat_since = now if best['kind'] == 'combat' else 0.0
        self.shot_why = best.get('why', '')
        self._bump('切镜·' + (reason.split('（')[0] or '未注明'))
        LEDGER.shot({'t': round(now, 2), 'gt': LEDGER.game_time(), 'ev': 'cut',
                     'kind': best['kind'], 'x': round(best['x']), 'y': round(best['y']),
                     'score': round(best['score'], 1),
                     'how': how, 'anchor': (self.anchor or {}).get('type'),
                     'dist': None if d > 1e8 else round(d),
                     'heldPrev': held_prev, 'reason': reason,
                     'why': best.get('why', ''), 'rivals': best.get('rivals') or [],
                     'bk': best.get('bk'), 'mode': self.mode,
                     'size': self.size, 'sizeWhy': self.size_why,
                     'aim': [round(ax), round(ay)],
                     'fightN': (best.get('cl') or {}).get('n')})
        self._maybe_orbit(best, now)

    def _maybe_orbit(self, best, now):
        if not self.orbit_enabled:          # 09-15 起默认关：镜头旋转让人头晕
            return
        if best['kind'] != 'combat':
            self.combat_since = 0.0
            return
        if not self.combat_since:
            self.combat_since = now
            return
        if now - self.combat_since < self.combat_hold:
            return
        if now - self.last_orbit < self.orbit_cooldown:
            return
        # 转之前先让镜头稳住一下，否则推镜和转圈会叠在一起
        if now - self.shot_at < 1.5:
            return
        if camctl.orbit_point(best['x'], best['y'], 360.0, self.orbit_seconds):
            self.orbit_until = now + self.orbit_seconds + 0.3
            self.last_orbit = now
            self.stats['orbits'] += 1
            self.note = '环绕交战点'

    def _end_orbit(self):
        """转完精确转回基准偏航 —— 不还原的话下一个镜头的朝向是随机的。"""
        self.orbit_until = 0.0
        if self.shot:
            camctl.look_at(self.shot['x'], self.shot['y'], seconds=0)
            self.frame_at = (self.shot['x'], self.shot['y'])
        if self.base:
            camctl.set_field(camctl.F_YAW, self.base['yaw'], 0.8)
        self._remember_aim()
        self.shot_at = time.time()          # 转完当作一个新镜头的开始
        self.note = ''


    # -- 存盘 --------------------------------------------------------------
    def save(self):
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as fh:
                json.dump({'enabled': self.running(), 'distance': self.distance,
                           'minShot': self.min_shot, 'ownOnly': self.own_only,
                           'heroFollow': self.hero_follow,
                           'fogCheck': self.fog_check,
                           'staleAfter': self.stale_after,
                           'idleSeconds': self.idle_seconds,
                           'orbitSeconds': self.orbit_seconds}, fh)
        except Exception:                                   # noqa: BLE001
            pass

    def load(self):
        """返回存盘里的"该不该开"。参数顺带读回来。"""
        try:
            with open(STATE_FILE, encoding='utf-8') as fh:
                d = json.load(fh)
        except Exception:                                   # noqa: BLE001
            return False
        self.distance = float(d.get('distance', self.distance))
        self.min_shot = float(d.get('minShot', self.min_shot))
        self.own_only = bool(d.get('ownOnly', self.own_only))
        self.hero_follow = bool(d.get('heroFollow', self.hero_follow))
        self.fog_check = bool(d.get('fogCheck', self.fog_check))
        self.stale_after = float(d.get('staleAfter', self.stale_after))
        self.idle_seconds = float(d.get('idleSeconds', self.idle_seconds))
        self.orbit_seconds = float(d.get('orbitSeconds', self.orbit_seconds))
        return bool(d.get('enabled'))


DIRECTOR = Director()


def autostart():
    """服务启动时调一次：上次是开着的就自动接着开。"""
    if DIRECTOR.load():
        DIRECTOR.start()
        return True
    return False


if __name__ == '__main__':
    import sys
    if not DIRECTOR.start():
        print('起不来：', DIRECTOR.note)
        sys.exit(1)
    print('导播已开始，Ctrl-C 停止。基准镜头：', DIRECTOR.base)
    try:
        while True:
            time.sleep(3)
            s = DIRECTOR.status()
            shot = s['shot']
            print('  %-7s 分 %-6s 停 %-5s  切%d 滑%d 绕%d  %s'
                  % (shot['kind'] if shot else '-',
                     round(shot['score'], 1) if shot else '-',
                     s['heldFor'], s['stats']['cuts'], s['stats']['glides'],
                     s['stats']['orbits'], s['note']))
    except KeyboardInterrupt:
        DIRECTOR.stop()
        print('\n已停止，镜头已复位。')
