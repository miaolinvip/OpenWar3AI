# -*- coding: utf-8 -*-
"""远见「反馈与建议」（09-26）：把问题报告 / 建议发到 war3ai.com 的反馈服务（server/feedback/app.py）。

    POST /api/feedback/preview   {kind, title, text, contact, category, diag}   先看看会发出去什么（不发）
    POST /api/feedback           同上，真的发
    GET  /api/feedback/history   本机发过的（成功的带编号 FB-000123；没发出去的能重试）
    POST /api/feedback/retry/{id}

每次提交由闭源运行时签名（bin/war3_ui_command.exe --identity --sign …，见 native/src/ow3_identity.h）：
附带本机识别码（Windows MachineGuid 加盐后的哈希，反推不出来），服务端据此验签、防重放、按机器限流。
本机这边也限流（15 秒一条、每小时 10 条），诊断信息只在勾了才带，发之前能预览；路径里的用户名换成 %USERPROFILE%。
网络不通时存进 bin/feedback/outbox/，网页上点「重试」再发（每次重新签名：时间和 nonce 要新的）。
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import common as C

W = C.w3paths
DIR = os.path.join(C.BIN, "feedback")
OUTBOX = os.path.join(DIR, "outbox")
HISTORY = os.path.join(DIR, "history.jsonl")
MIN_GAP_S = 15
MAX_PER_HOUR = 10
LIMITS = {"title": 120, "text": 8000, "contact": 120, "category": 30}
NOWIN = 0x08000000
_lock = threading.Lock()


def version() -> str:
    try:
        with open(os.path.join(C.ROOT, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "0.0.0"


def service_url() -> str:
    """反馈 / 版本检查的服务器（openwar3.json 的 service_url，默认 https://war3ai.com）。
    只认 https；http 只允许本机（测试用），别让一个写错 / 被改的配置把内容明文发到别处。"""
    url = str(W.config().get("service_url") or W.WEBSITE).rstrip("/")
    u = urllib.parse.urlparse(url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")):
        return url
    return W.WEBSITE


# ── 脱敏：发出去的东西里不带本机用户名 ────────────────────────────────
_HOME = os.path.expanduser("~")
_USER = os.path.basename(_HOME)


def scrub(s: str) -> str:
    if not isinstance(s, str):
        return s
    if _HOME and len(_HOME) > 3:
        s = re.sub(re.escape(_HOME), "%USERPROFILE%", s, flags=re.I)
    if _USER and len(_USER) >= 3:
        s = re.sub(rf"(?i)(\\Users\\){re.escape(_USER)}\b", r"\1%USERNAME%", s)
    return s


def _scrub_obj(o):
    if isinstance(o, dict):
        return {k: _scrub_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_scrub_obj(v) for v in o]
    return scrub(o) if isinstance(o, str) else o


def _tail(path: str, lines: int, max_bytes: int = 16 * 1024) -> list[str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            rows = fh.read().decode("utf-8", "replace").replace("\0", "").splitlines()
    except OSError:
        return []
    if size > max_bytes and rows:
        rows = rows[1:]
    return [r[:300] for r in rows[-lines:]]


def client_info() -> dict:
    from . import services as S
    man = C.load_json(os.path.join(W.PREBUILT, "manifest.json"), None) or {}
    source = os.path.isfile(os.path.join(W.NATIVE, "build.ps1"))
    gd = W.game_dir()
    game = S.file_version(os.path.join(gd, "Game.dll")) if gd and os.path.isfile(os.path.join(gd, "Game.dll")) else None
    return {"version": version(), "runtime": "source" if source else str(man.get("version") or "?"),
            "game": game or "", "os": f"Windows {platform.release()} {platform.version()}"[:80], "python": platform.python_version()}


def diagnostics() -> dict:
    """附带的诊断信息：环境自检的结果、各服务状态、实例概况、远见后台和运行时日志的最后几十行。全部脱敏、≤ 40 KB。"""
    from . import instances as I
    from . import services as S
    h = S.health()
    out = {
        "health": [{"key": c["key"], "status": c["status"], "code": c["code"], "value": c["value"]} for c in h["checks"]],
        "services": [{"key": s["key"], "state": s["state"]} for s in S.services(W.port("console", 8866))["services"]],
        "instances": [{"inst": s["inst"], "alive": s["game"]["alive"], "ai": bool(s["brain"] or s.get("bot")),
                       "scheme": s.get("schemeRunning")} for s in I.status() if s["game"]["alive"] or s["farm"]][:12],
        "logs": {"console": _tail(S.CONSOLE_LOG, 60), "runtime": _tail(W.HOOK_LOG, 80)},
    }
    out = _scrub_obj(out)
    while len(json.dumps(out, ensure_ascii=False).encode("utf-8")) > 40 * 1024 and (out["logs"]["runtime"] or out["logs"]["console"]):
        for k in ("runtime", "console"):                    # 太大就从日志头上砍
            if out["logs"][k]:
                out["logs"][k] = out["logs"][k][len(out["logs"][k]) // 4 + 1:]
    return out


def build(body: dict) -> dict:
    """网页填的内容 -> 要发出去的正文（和服务端 Feedback 模型一一对应）。字段不对抛 ValueError（网页显示原因）。"""
    kind = body.get("kind")
    if kind not in ("issue", "suggestion"):
        raise ValueError("kind")
    fields = {k: str(body.get(k) or "").strip() for k in LIMITS}
    if not fields["title"] or not fields["text"]:
        raise ValueError("empty")
    for k, n in LIMITS.items():
        if len(fields[k]) > n:
            raise ValueError(f"too-long:{k}")
    payload = {"v": 1, "kind": kind, **fields, "lang": str(body.get("lang") or "")[:10], "client": client_info()}
    if body.get("diag"):
        payload["diag"] = diagnostics()
    return payload


def identity(body_hash: str | None = None, ts: str = "", nonce: str = "") -> dict:
    exe = W.CLI_EXE
    if not os.path.isfile(exe):
        return {"error": "runtime-missing"}
    args = [exe, "--identity"] + (["--sign", body_hash, "--ts", ts, "--nonce", nonce] if body_hash else [])
    try:
        r = subprocess.run(args, capture_output=True, timeout=10, creationflags=NOWIN)
        d = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return {"error": "runtime-old"}                  # 老版本的命令行工具没有 --identity：双击 start.bat 同步运行时
    if d.get("error") or not re.fullmatch(r"[0-9a-f]{32}", d.get("machine", "")):
        return {"error": "runtime-old"}
    return d


def preview(body: dict) -> dict:
    payload = build(body)
    ident = identity()
    return {"payload": payload, "machine": ident.get("machine"), "error": ident.get("error"),
            "endpoint": service_url() + "/api/v1/feedback", "bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))}


def _send(payload: dict) -> dict:
    """签名 + 发送。回 {ok, id, ref} 或 {ok: False, error: 代码, detail}；网络不通 error = offline。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    ident = identity(hashlib.sha256(raw).hexdigest(), ts, nonce)
    if ident.get("error"):
        return {"ok": False, "error": ident["error"]}
    req = urllib.request.Request(service_url() + "/api/v1/feedback", data=raw, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": f"OpenWar3-Farsight/{version()}",
        "X-OW3-Machine": ident["machine"], "X-OW3-Timestamp": ts, "X-OW3-Nonce": nonce,
        "X-OW3-Signature": ident["sig"], "X-OW3-Alg": ident.get("alg", "")})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read(4096) or b"{}")
        if d.get("ok"):
            return {"ok": True, "id": d.get("id"), "ref": d.get("ref"), "machine": ident["machine"]}
        return {"ok": False, "error": "server", "detail": str(d)[:200]}
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read(2048) or b"{}").get("error") or f"http-{e.code}"
        except ValueError:
            code = f"http-{e.code}"
        return {"ok": False, "error": code, "status": e.code}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "error": "offline", "detail": str(getattr(e, "reason", e))[:200]}


def _history() -> list[dict]:
    try:
        with open(HISTORY, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]
    except (OSError, ValueError):
        return []


def _write_history(rows: list[dict]) -> None:
    os.makedirs(DIR, exist_ok=True)
    tmp = HISTORY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows[-200:]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, HISTORY)


def _local_limit() -> str | None:
    now = time.time()
    tries = [r for r in _history() if now - r.get("at", 0) < 3600]
    if tries and now - max(r["at"] for r in tries) < MIN_GAP_S:
        return "too-fast"
    if len(tries) >= MAX_PER_HOUR:
        return "too-many"
    return None


def _retryable(code: str | None) -> bool:
    """这些错不怪提交的内容，过一阵再发多半能成：没网、服务器忙 / 出错、反馈服务还没开通（404 / 405）。"""
    return code in ("offline", "busy", "http-404", "http-405") or bool(code and re.fullmatch(r"http-5\d\d", code))


def submit(body: dict) -> dict:
    with _lock:
        limit = _local_limit()
        if limit:
            return {"ok": False, "error": limit}
        payload = build(body)
        r = _send(payload)
        rec = {"id": secrets.token_hex(6), "at": time.time(), "kind": payload["kind"], "title": payload["title"],
               "ok": r["ok"], "ref": r.get("ref"), "error": r.get("error"), "diag": "diag" in payload}
        if not r["ok"] and _retryable(r.get("error")):
            os.makedirs(OUTBOX, exist_ok=True)       # 服务器那边的问题（没网 / 忙 / 服务还没开通）：留着以后重试
            with open(os.path.join(OUTBOX, rec["id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            rec["pending"] = True
        rows = _history()
        rows.append(rec)
        _write_history(rows)
        return dict(r, record=rec)


def retry(rid: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{12}", rid):
        return {"ok": False, "error": "bad-id"}
    path = os.path.join(OUTBOX, rid + ".json")
    with _lock:
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return {"ok": False, "error": "gone"}
        r = _send(payload)
        rows = _history()
        for rec in rows:
            if rec.get("id") == rid:
                rec.update(ok=r["ok"], ref=r.get("ref"), error=r.get("error"), retriedAt=time.time())
                if r["ok"]:
                    rec.pop("pending", None)
        _write_history(rows)
        if r["ok"]:
            os.remove(path)
        return r


def history() -> dict:
    rows = list(reversed(_history()))[:50]
    return {"rows": rows, "pending": len(glob.glob(os.path.join(OUTBOX, "*.json"))), "endpoint": service_url()}
