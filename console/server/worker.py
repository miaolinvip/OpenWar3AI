# -*- coding: utf-8 -*-
"""Farsight 实例 worker：一个游戏实例一个进程。

    python worker.py <实例号>

为什么一个实例一个进程：w3state / w3cmd / w3claim / camctl / director 全都按**进程级环境变量**
（WAR3_PID / WAR3_INST / WAR3_INST_DIR）认实例，camctl 的 CTL、director 的 DIRECTOR 还是模块级单例 ——
一个进程里同时管两个实例会静默连错（同一份进程级状态同时代表两个实例，错了也不报）。

协议（stdin/stdout，一行一个 JSON）：
    请求  {"id": 7, "op": "command", "args": {...}}
    回包  {"id": 7, "ok": true, "result": ...} / {"id": 7, "ok": false, "error": "..."}
    推送  {"event": "snapshot", "data": {...}}   4 Hz，连上游戏才推
          {"event": "status", "data": {...}}     连接状态变化时

⚠ 模块里到处有 print（w3state 的警告、camctl 的提示）—— 协议走 dup 出来的原始 stdout，sys.stdout 指到 stderr，
  否则一行杂讯就把 JSON 流打断。
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402

INST = int(sys.argv[1]) if len(sys.argv) > 1 else 5
INST_DIR = C.inst_dir(INST)
os.environ["WAR3_INST"] = str(INST)
os.environ["WAR3_INST_DIR"] = INST_DIR
os.environ.pop("WAR3_PID", None)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for p in (C.MODDIR, C.ANALYTICS):
    if p not in sys.path:
        sys.path.insert(0, p)

_proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n", buffering=1)
sys.stdout = sys.stderr
_out_lock = threading.Lock()


def emit(obj) -> None:
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_default)
    with _out_lock:
        _proto.write(line + "\n")
        _proto.flush()


def _default(o):
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


ORDERS = {"smart": 0x000D0003, "stop": 0x000D0004, "attack": 0x000D000F, "move": 0x000D0012,
          "patrol": 0x000D0016, "hold": 0x000D0019, "harvest": 0x000D0032}


class Game:
    """连着哪个游戏进程、快照、命令通道、时钟、仲裁表。游戏重开换 pid 时整套换掉。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.pid = 0
        self.ses = None
        self.clock = None
        self.claims = None
        self.last_snap_at = 0.0
        self.last_error = None
        self.snaps = 0

    def farm_pid(self) -> int:
        try:
            text = open(os.path.join(C.FARM_DIR, f"inst{INST}.out"), "rb").read().decode("utf-8", "replace").replace("\0", "")
        except OSError:
            return 0
        if "control channel: ok" not in text:
            return 0
        m = re.findall(r"WAR3_PID=(\d+)", text)
        return int(m[-1]) if m else 0

    def ensure(self) -> bool:
        import psutil
        pid = self.farm_pid()
        alive = bool(pid) and psutil.pid_exists(pid)
        with self.lock:
            if not alive:
                if self.pid:
                    self._drop("游戏进程没了")
                return False
            if pid == self.pid and self.ses is not None:
                return True
            self._drop("换了游戏进程")
            os.environ["WAR3_PID"] = str(pid)
            from w3state import Session
            self.ses = Session()
            self.pid = pid
            self.last_error = None
            try:
                import camctl
                camctl.CTL.pid = 0              # camctl 缓存 5 秒 pid，换局立刻重找
            except Exception:  # noqa: BLE001
                pass
            emit({"event": "status", "data": self.status()})
            return True

    def _drop(self, why: str) -> None:
        self.ses, self.clock, self.claims, self.pid = None, None, None, 0
        self.last_error = why
        emit({"event": "status", "data": self.status()})

    def status(self) -> dict:
        return {"inst": INST, "pid": self.pid, "connected": self.ses is not None and self.ses.cmd is not None,
                "lastSnapAgo": round(time.monotonic() - self.last_snap_at, 1) if self.last_snap_at else None,
                "error": self.last_error, "snaps": self.snaps}

    def cmd(self):
        if self.ses is None or self.ses.cmd is None:
            raise RuntimeError("还没连上游戏")
        return self.ses.cmd

    def claim(self, addr: int, seconds: float) -> bool:
        if self.claims is None:
            from w3claim import PRIO, Claims
            self.claims = Claims("console", self.pid)
            self._prio = PRIO["manual"]
        return self.claims.take(addr, seconds, prio=self._prio)

    def read(self):
        with self.lock:
            if self.ses is None:
                return None, None
            snap = self.ses.read()
            if self.ses.cmd is not None and self.clock is None:
                from fightodds import GameClock
                self.clock = GameClock(self.ses.cmd)
            t = self.clock.read() if (self.clock is not None and snap is not None) else None
            return snap, t


G = Game()


def snapshot_payload(snap, t) -> dict:
    units = []
    for u in snap.units:
        units.append([u.addr, u.type, u.owner, round(u.x), round(u.y), int(u.hp), int(u.hp_max),
                      u.order if (u.order is not None and u.order_known) else -1,
                      -1 if u.has_task is None else int(bool(u.has_task))])
    return {"pid": G.pid, "t": t, "me": snap.me, "inGame": snap.in_game, "gold": snap.gold, "lumber": snap.lumber,
            "foodUsed": snap.food_used, "foodCap": snap.food_cap, "epoch": snap.epoch, "ageMs": snap.age_ms,
            "units": units}


def snapshot_loop(stop: threading.Event) -> None:
    last_ensure = 0.0
    last_connected = None
    last_status_at = 0.0
    while not stop.is_set():
        now = time.monotonic()
        try:
            if now - last_ensure > 2.0:
                last_ensure = now
                G.ensure()
            snap, t = G.read()
            # ★ 09-18 导播台一直显示「读不到镜头 / 实例 5 的游戏没连上」—— ensure() 换局时刚建 Session 就发了一次状态，
            #   那一刻命令通道还没连上（connected=False），之后通道连上了却没人再发，网页一直停在「没连上」，useCam 不去取镜头。
            #   连没连上一变就补发一次。
            connected = G.status()["connected"]
            if connected != last_connected or now - last_status_at > 5.0:
                last_connected = connected
                last_status_at = now
                emit({"event": "status", "data": G.status()})
            if snap is not None:
                G.last_snap_at = time.monotonic()
                G.snaps += 1
                emit({"event": "snapshot", "data": snapshot_payload(snap, t)})
        except Exception as exc:  # noqa: BLE001
            G.last_error = f"{type(exc).__name__}: {exc}"
        stop.wait(0.25)


# ── 请求 ──
def op_status(a):
    return G.status()


def op_unit(a):
    """选中单位的详情：订单、任务、魔法、英雄等级/经验、技能（含冷却）、背包。每项单独 try，读不到的给 None。"""
    addr = int(a["addr"])
    code = str(a.get("type", ""))
    with G.lock:
        cmd = G.cmd()
        out = {"addr": addr, "type": code}

        def safe(key, fn):
            try:
                out[key] = fn()
            except Exception as exc:  # noqa: BLE001
                out[key] = None
                out.setdefault("errors", {})[key] = f"{type(exc).__name__}: {exc}"

        safe("order", lambda: cmd.current_order(addr))
        safe("task", lambda: cmd.current_task(addr))
        safe("mana", lambda: cmd.unit_state(addr, 2))
        safe("manaMax", lambda: cmd.unit_state(addr, 3))
        if code[:1].isupper():
            safe("level", lambda: cmd.hero_level(addr))
            safe("xp", lambda: cmd.hero_xp(addr))
            safe("skillPoints", lambda: cmd.skill_points(addr))
            safe("inventory", lambda: cmd.inventory(addr))
        abil = []
        try:
            for ab in cmd.abilities(addr):
                c = ab["code"]
                if c in ("Aatk", "Amov", "AInv", "Ahar", "Arep", "Abdl", "Abds"):
                    continue
                cd = None
                if ab["flags"] & 0x200:
                    try:
                        cd = cmd.cooldown_left(ab["addr"])
                    except Exception:  # noqa: BLE001
                        cd = None
                abil.append({"code": c, "level": ab["level"], "cooldown": cd, "cooling": bool(ab["flags"] & 0x200),
                             "name": C.ability_names().get(c), "cast": C.cast_info(c), "buff": c[:1] == "B"})
        except Exception as exc:  # noqa: BLE001
            out.setdefault("errors", {})["abilities"] = str(exc)
        out["abilities"] = abil
        try:
            p = cmd.player_of(addr)
            out["player"] = p
        except Exception:  # noqa: BLE001
            pass
    return out


def op_command(a):
    """手动下令。先抢租约（优先级 manual 95，租 hold 秒）—— 租约期内 AI 各层都不会覆盖这条令。"""
    kind = str(a["kind"])
    units = [int(u) for u in a.get("units", [])]
    hold = float(a.get("hold", 8.0))
    x, y = a.get("x"), a.get("y")
    target = int(a["target"]) if a.get("target") else 0
    code = str(a.get("code") or "")
    order = int(a["order"]) if a.get("order") is not None else None
    results = []
    with G.lock:
        cmd = G.cmd()
        for u in units:
            ok, err = False, None
            try:
                if not G.claim(u, hold):
                    err = "租约被占"
                elif kind in ("smart", "attack", "move", "patrol"):
                    if target:
                        ok = cmd.target_order(u, target, ORDERS[kind], float(x or 0), float(y or 0))
                    else:
                        ok = cmd.move_to(u, float(x), float(y), ORDERS[kind])
                elif kind == "stop":
                    ok = cmd.stop(u)
                elif kind == "hold":
                    ok = cmd.immediate_order(u, ORDERS["hold"])
                elif kind == "train":                       # 训练 / 研究 / 升级：建筑 + 四字码
                    ok = cmd.immediate_order(u, C.rawcode(code))
                elif kind == "build":
                    ok = cmd.build(u, C.rawcode(code), float(x), float(y))
                elif kind == "cast":
                    if order is None:
                        info = C.cast_info(code)
                        order = info["order"] if info else None
                    if order is None:
                        err = "不认识的技能"
                    elif target:
                        ok = cmd.cast_target(u, order, target, float(x or 0), float(y or 0))
                    elif x is not None and y is not None:
                        ok = cmd.cast_point(u, order, float(x), float(y))
                    else:
                        ok = cmd.immediate_order(u, order)
                elif kind == "learn":
                    ok = cmd.learn(u, C.rawcode(code))
                elif kind == "use_item":
                    slot = int(a.get("slot", 0))
                    ok = cmd.use_item(u, cmd.USESLOT1 + slot, float(x) if x is not None else None,
                                      float(y) if y is not None else None, target)
                elif kind == "harvest":
                    ok = cmd.target_order(u, target, ORDERS["harvest"])
                elif kind == "rally":
                    ok = cmd.rally(u, float(x), float(y))
                elif kind == "release":
                    G.claims and G.claims.give_back(u)
                    ok = True
                else:
                    err = f"不认识的命令 {kind}"
                r = getattr(cmd, "last_receipt", None)
                if not ok and err is None and r is not None:
                    err = r.reason                            # 引擎为什么没接（W3P 回执）
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            results.append({"addr": u, "ok": bool(ok), "error": err})
    return {"kind": kind, "results": results, "okCount": sum(1 for r in results if r["ok"])}


def op_leases(a):
    with G.lock:
        if G.pid == 0:
            return []
        if G.claims is None:
            G.claim(0, 0)
        from w3claim import MODULES
        names = {v: k for k, v in MODULES.items()}
        return [{"addr": addr, "owner": names.get(o, str(o)), "leftMs": left} for addr, o, left in G.claims.held()]


def op_techs(a):
    codes = a.get("codes") or []
    unit = int(a["unit"])
    with G.lock:
        cmd = G.cmd()
        p = cmd.player_of(unit)
        return {c: cmd.tech_count(p, c) for c in codes}


# ── 双方经济（全知：对手的资源人类玩家看不到；对手的电脑 AI 同样看得见我方全图）──
# 09-23 P3：原来这里自己调 JASS native Player/GetPlayerState 再自己去读种族/难度；
# 现在读 W3P 世界块的 players（运行时每次发布时采全部 16 家：金木、人口、累计采集、种族）。
# 维护费档和电脑难度世界块里没有，给 None（要就在运行时里加字段，不在开放层读内存）。


def op_players(a):
    """场上每个玩家（0~11，有单位的）的资源、人口、累计采集、种族。"""
    with G.lock:
        cmd = G.cmd()
        ws = cmd.snapshot(fresh=True)
        if ws is None:
            return {"players": []}
        owners = {}
        for u in ws.units:
            if u.owner < 12 and u.alive:
                owners[u.owner] = owners.get(u.owner, 0) + 1
        out = []
        for owner in sorted(owners):
            p = ws.players[owner]
            row = {"owner": owner, "me": owner == ws.me, "units": owners[owner]}
            if p.known:
                row.update({"gold": p.gold, "lumber": p.lumber, "foodCap": p.food_cap, "foodUsed": p.food_used,
                            "goldGathered": p.gold_gathered, "lumberGathered": p.lumber_gathered, "race": p.race})
            row.update({"upkeep": None, "difficulty": None})
            out.append(row)
        return {"me": ws.me, "t": ws.clock, "players": out}


# ── 镜头 / 导播 ──
_director_ready = False


def _director():
    global _director_ready
    import camctl  # noqa: F401
    import director
    if not _director_ready:
        director.STATE_FILE = os.path.join(INST_DIR, "director-state.json")
        try:
            director.DIRECTOR.load()
            # 人在导播台上调过的参数（director-config.json）优先于存盘 —— 不读的话，
            # 没有导播在跑时参数面板显示的是自动调参留下的值，而不是人调的
            director.DIRECTOR.load_config(force=True)
        except Exception:  # noqa: BLE001
            pass
        _director_ready = True
    return director.DIRECTOR


def _lock_holder() -> int:
    try:
        v = int((open(os.path.join(INST_DIR, "director.lock"), encoding="utf-8").read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0
    import psutil
    return v if (v and v != os.getpid() and psutil.pid_exists(v)) else 0


def op_cam(a):
    import camctl
    G.ensure()
    return camctl.do(str(a.get("op")), a.get("payload") or {})


def op_cam_status(a):
    import camctl
    G.ensure()
    s = camctl.status()
    holder = _lock_holder()
    s["directorHost"] = "external" if holder else ("console" if _director_ready and _director().running() else "none")
    s["directorHolderPid"] = holder or None
    # 没有独立导播在跑（直播栈停着、或者导播进程还没拉起来）时也给出参数和状态 ——
    # 原来这里只在控制台托管过导播之后才有，导播台的参数面板会全显示成滑块最小值
    s["director"] = _director().status()
    if holder:
        # 独立导播进程（director_inst.py）每 3 秒写一份运行状态 —— 控制台里那个 DIRECTOR 不是它，不看这个会一直显示「已停止」
        live = C.load_json(os.path.join(INST_DIR, "director-live.json"), None)
        if isinstance(live, dict) and time.time() - (live.get("wall") or 0) < 20:
            s["director"] = dict(s.get("director") or {}, **{k: v for k, v in live.items() if k not in ("wall", "pid")})
            s["directorLive"] = True
    return s


def _write_director_config(cfg: dict) -> dict:
    """把网页上调的参数写进 inst<N>/director-config.json。

    导播（不管是独立进程还是控制台托管的那份）在自己的循环里看这个文件的修改时间，
    变了就热加载 —— 所以改参数**不用停导播、不用打断对局**。
    09-18 之前只有一条路：点"接管"，而接管 = terminate 掉正在跑的导播进程，
    对局中途镜头会停十几秒。
    """
    path = os.path.join(INST_DIR, "director-config.json")
    cur = C.load_json(path, {}) or {}
    cur.update({k: v for k, v in cfg.items() if v is not None})
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, ensure_ascii=False)
    os.replace(tmp, path)
    return cur


def op_director(a):
    action = str(a.get("action", "status"))
    holder = _lock_holder()
    if action == "configure":
        # 只写文件，不碰内存里的对象 —— 两种托管方式共用同一条路
        # （同一件事别走两条路径，否则总有一条会被忘掉）
        wrote = _write_director_config(a.get("config") or {})
        return {"ok": True, "config": wrote, "host": "external" if holder else "console",
                "note": "已写给独立导播进程，几秒内生效" if holder else "已保存"}
    if action == "start" and holder and not a.get("takeover"):
        return {"ok": False, "error": f"这个实例的导播在独立进程 {holder} 里跑（director_inst.py），要接管吗", "holder": holder}
    if a.get("takeover") and holder:
        import psutil
        try:
            psutil.Process(holder).terminate()
            time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"停不掉独立导播 {holder}：{exc}"}
    d = _director()
    if action == "start":
        with open(os.path.join(INST_DIR, "director.lock"), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        if a.get("config"):
            d.configure(**a["config"])
        d.start()
    elif action == "stop":
        d.stop()
    return {"ok": True, "status": d.status()}


def op_director_shots(a):
    """导播最近的切镜：账本 inst<N>/director/shots-<这一局>.jsonl 的尾巴。

    导播台的「最近 20 次切镜」就读这个 —— 每一行都带着当时的分数、人话原因和落选候选，
    是导播自己写的账（war3-analytics/dirledger.py），这里只读不改。
    这一局是哪份账本，以导播发布的 ledger.stamp 为准；读不到就取最新的那份。
    """
    import glob
    limit = max(1, min(500, int(a.get("limit") or 20)))
    d = os.path.join(INST_DIR, "director")
    want = str(a.get("stamp") or "")
    if want:
        # 回看某一局。局名只认数字和横杠 —— 它会拼进文件路径，不能让网页传 ../ 进来
        if not re.fullmatch(r"[0-9-]{8,40}", want):
            return {"stamp": None, "shots": [], "total": 0, "error": "局名不对"}
        p = os.path.join(d, f"shots-{want}.jsonl")
        return _read_shots(p, want, limit)
    stamp = None
    live = C.load_json(os.path.join(INST_DIR, "director-live.json"), None)
    if isinstance(live, dict):
        stamp = (live.get("ledger") or {}).get("stamp")
    if not stamp and _director_ready:
        try:
            import dirledger
            stamp = dirledger.LEDGER.stamp
        except Exception:  # noqa: BLE001
            stamp = None
    path = os.path.join(d, f"shots-{stamp}.jsonl") if stamp else ""
    if not path or not os.path.exists(path):
        files = sorted(glob.glob(os.path.join(d, "shots-*.jsonl")), key=os.path.getmtime)
        path = files[-1] if files else ""
        stamp = os.path.basename(path)[len("shots-"):-len(".jsonl")] if path else None
    if not path:
        return {"stamp": None, "shots": [], "total": 0}
    return _read_shots(path, stamp, limit)


def _read_shots(path: str, stamp, limit: int) -> dict:
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue                  # 导播正写到一半的最后一行
                if r.get("ev") in ("cut", "follow", "release"):
                    rows.append(r)
    except OSError as exc:
        return {"stamp": stamp, "shots": [], "total": 0, "error": str(exc)}
    return {"stamp": stamp, "shots": rows[-limit:], "total": len(rows)}


def op_director_stamps(a):
    """这个实例有账本的那些局（最新的在前），给导播台的"回看哪一局"用。"""
    import glob
    d = os.path.join(INST_DIR, "director")
    out = []
    for p in sorted(glob.glob(os.path.join(d, "shots-*.jsonl")), key=os.path.getmtime, reverse=True)[:40]:
        n = 0
        try:
            with open(p, encoding="utf-8") as fh:
                n = sum(1 for line in fh if '"ev":"cut"' in line or '"ev":"follow"' in line)
        except OSError:
            pass
        out.append({"stamp": os.path.basename(p)[len("shots-"):-len(".jsonl")],
                    "mtime": os.path.getmtime(p), "shots": n})
    return {"stamps": out}


def op_end_game(a):
    """动作 22：进程内结束这一局（farm -Keep 会按 next_game.json 起下一局）。返回值历来是 False，看进程退没退。"""
    with G.lock:
        cmd = G.cmd()
        try:
            cmd.action(22, 0)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True}


def op_issue_toggle(a):
    """动作 57 command 1：等同在游戏里按一下问题快捷键 —— 暂停并记一条 / 继续（运行时的动作 57）。
    DLL 照样往 issue_hotkey_<pid>.log 记一行，新大脑监督器照样把它记成问题标记。网页「问题备忘」的「暂停并记一条」用它。"""
    with G.lock:
        cmd = G.cmd()
        ok = cmd.action(57, 1, timeout=3.0)
        summary = cmd.ctl.summary                    # 属性（w3chan.Channel.summary），不是方法
    return {"ok": ok, "summary": summary,
            "note": None if ok or "toggled" in summary else "这个游戏的 DLL 还是旧版（没有动作 57），下一局开局换上新 DLL 就有"}


OPS = {"status": op_status, "unit": op_unit, "command": op_command, "leases": op_leases, "techs": op_techs, "players": op_players,
       "cam": op_cam, "cam_status": op_cam_status, "director": op_director, "end_game": op_end_game,
       "director_shots": op_director_shots, "director_stamps": op_director_stamps, "issue_toggle": op_issue_toggle}


def handle(req: dict) -> None:
    rid = req.get("id")
    fn = OPS.get(req.get("op"))
    if fn is None:
        emit({"id": rid, "ok": False, "error": f"不认识的 op {req.get('op')}"})
        return
    try:
        emit({"id": rid, "ok": True, "result": fn(req.get("args") or {})})
    except Exception as exc:  # noqa: BLE001
        emit({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=4)})


def main() -> int:
    stop = threading.Event()
    threading.Thread(target=snapshot_loop, args=(stop,), daemon=True).start()
    emit({"event": "hello", "data": {"inst": INST, "workerPid": os.getpid()}})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        # 慢请求（镜头序列、导播停止要等线程）放线程里，不堵后面的请求
        threading.Thread(target=handle, args=(req,), daemon=True).start()
    stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
