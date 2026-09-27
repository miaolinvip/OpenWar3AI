"""Versioned leases shared by Python modules and PowerShell.

Deadlines in files are Unix epoch milliseconds, never remaining durations.
Readers do not renew leases. Unknown/legacy schemas and other matches fail open
(no lock); expiry still releases a lock when its producer stops or crashes.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections.abc import Iterable, Mapping

SCHEMA = 1
MAX_LEASE_MS = 60_000
CLOCK_SKEW_MS = 2_000


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt
    _create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    _create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create_file.restype = wintypes.HANDLE


def _open_read(path):
    # Windows readers must permit FILE_SHARE_DELETE for atomic os.replace to work
    # while a reader holds the previous version. PowerShell uses the same flag.
    if os.name != "nt":
        return open(path, encoding="utf-8-sig")
    handle = _create_file(os.fspath(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    return os.fdopen(fd, "r", encoding="utf-8-sig")


def epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def _uint(value) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a unit identity")
    out = int(value, 16 if value.lower().startswith("0x") else 10) if isinstance(value, str) else int(value)
    if not 0 <= out <= 0xFFFFFFFF:
        raise ValueError("unit identity is outside uint32")
    return out


def _field(obj, *names):
    for name in names:
        value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def identity(unit) -> tuple[int, int] | None:
    lo = _field(unit, "handleLo", "handle_lo")
    hi = _field(unit, "handleHi", "handle_hi")
    if lo is None or hi is None:
        return None
    return _uint(lo), _uint(hi)


def _unit_map(units) -> dict[int, object] | None:
    if units is None:
        return None
    if isinstance(units, Mapping):
        return {_uint(addr): unit for addr, unit in units.items()}
    return {_uint(_field(unit, "addr")): unit for unit in units}


def write(path, units: Mapping, game_pid: int, epoch=None, *, now_ms=None) -> bool:
    """Atomically publish absolute deadlines; an empty units map clears leases."""
    now_ms = epoch_ms() if now_ms is None else int(now_ms)
    try:
        entries = {}
        for addr, entry in units.items():
            expires = int(entry["expiresAtMs"])
            if not now_ms < expires <= now_ms + MAX_LEASE_MS:
                continue
            value = {"expiresAtMs": expires}
            pair = identity(entry)
            if pair is not None:
                value.update(handleLo=pair[0], handleHi=pair[1])
            entries[str(_uint(addr))] = value
        payload = {"schema": SCHEMA, "gamePid": int(game_pid),
                   "writtenAtMs": now_ms, "units": entries}
        if epoch is not None:
            payload["epoch"] = str(epoch)
        directory = os.path.dirname(os.path.abspath(path))
        # Unique temp names also make concurrent/restarted producers safe.
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
            for attempt in range(3):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.001)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return False


def write_deadlines(path, deadlines: Mapping[int, float], now: float,
                    game_pid: int, epoch=None, units: Iterable | Mapping | None = None,
                    *, now_ms=None) -> bool:
    """Adapt monotonic deadlines without adding time spent earlier in this tick.

    Runtime samples both clocks at publication. The caller's ``now`` may precede
    a blocking command; using it with a fresh wall clock would extend the lease.
    Explicit ``now_ms`` pairs with ``now`` for deterministic/offline adapters.
    """
    if now_ms is None:
        now = time.monotonic()
        wall = epoch_ms()
    else:
        wall = int(now_ms)
    current = _unit_map(units)
    entries = {}
    for addr, deadline in deadlines.items():
        if not math.isfinite(deadline) or deadline <= now:
            continue
        if current is not None and addr not in current:
            continue
        entry = {"expiresAtMs": wall + int((deadline - now) * 1000)}
        pair = identity(current[addr]) if current is not None else None
        if pair is not None:
            entry.update(handleLo=pair[0], handleHi=pair[1])
        entries[addr] = entry
    return write(path, entries, game_pid, epoch, now_ms=wall)


def read(path, game_pid: int, epoch=None, units_by_addr=None, *, now_ms=None) -> dict[int, dict]:
    """Return live entries from this match, optionally validating unit generation.

    Legacy {addr: seconds} cannot identify a match and is deliberately rejected.
    A snapshot with identity metadata must match both handle components.
    """
    now_ms = epoch_ms() if now_ms is None else int(now_ms)
    try:
        with _open_read(path) as stream:
            doc = json.load(stream)
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            return {}
        if int(game_pid) <= 0 or int(doc["gamePid"]) != int(game_pid):
            return {}
        if epoch is not None and str(doc.get("epoch", "")) != str(epoch):
            return {}
        written = int(doc["writtenAtMs"])
        if written <= 0 or written > now_ms + CLOCK_SKEW_MS:
            return {}
        current = _unit_map(units_by_addr)
        out = {}
        for address, entry in doc["units"].items():
            try:
                addr = _uint(address)
                expires = int(entry["expiresAtMs"])
                if not now_ms < expires <= written + MAX_LEASE_MS or expires <= written:
                    continue
                if current is not None:
                    if addr not in current:
                        continue
                    # ⚠ 09-08：这里原来是 `identity(entry) != identity(current[addr])`
                    #   就扔。可 write() 上面明明白白写着"条目没有句柄对就不写"，
                    #   于是**任何不带句柄的条目都恒被扔掉** —— 读端和它自己的
                    #   写端约定相反。PowerShell 那个写端（reserved.json）正好不带，
                    #   结果 v119 那条"策略层预留的工人别碰"上线后一次都没生效：
                    #   mod_worker 183 次心跳的"预留跳过"全是 0，而文件一直在写。
                    #   条目带了句柄才校验；不带就只按地址认（写端已经这么承诺了）。
                    expected = identity(entry)
                    if expected is not None and expected != identity(current[addr]):
                        continue
                out[addr] = entry
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
        return out
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return {}
