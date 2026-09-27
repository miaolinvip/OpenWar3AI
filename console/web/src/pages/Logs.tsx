import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownToLine, Copy, Pause, Play, Search, ScrollText, WrapText } from "lucide-react";
import { Badge, Empty, useFetch } from "../components/ui";
import { api } from "../lib/api";
import { followLogs } from "../lib/live";
import { toast, useApp } from "../lib/store";
import { locale, tx } from "../lib/i18n";

interface Source { key: string; label: string; group: string; size: number; mtime: number }
const GROUPS: Record<string, string> = { scheme: tx("方案 / 模组"), brain: tx("新大脑"), module: tx("毫秒层模块"), game: tx("游戏与导播"), old: tx("老 AI") };

function tone(line: string) {
  if (/Traceback|Error|出错|失败|✘|ERROR|exception/i.test(line)) return "err";
  if (/⚠|warn|超时|被拒|没接下/i.test(line)) return "warn";
  if (/★|victory|胜利|局开始|局结束/.test(line)) return "star";
  return "";
}

export default function Logs() {
  const inst = useApp((s) => s.inst);
  const runsScheme = useApp((s) => !!s.instances.find((i) => i.inst === s.inst)?.bot?.length);
  const { data: sources } = useFetch<Source[]>(`/api/i/${inst}/logs`, 10000, [inst]);
  const [key, setKey] = useState(runsScheme ? "scheme_bot" : "brain_supervisor");
  // 选中的那份不存在（跑方案的实例没有新大脑日志、刚换了实例…）：换成最该看的一份，别停在空白页
  useEffect(() => {
    if (!sources?.length || sources.some((s) => s.key === key)) return;
    const prefer = runsScheme ? ["scheme_bot", "scheme_supervisor"] : ["brain_supervisor", "brain", "scheme_bot"];
    setKey((prefer.map((k) => sources.find((s) => s.key === k)).find(Boolean) ?? sources[0]).key);
  }, [sources, key, runsScheme]);
  const [lines, setLines] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [live, setLive] = useState(true);
  const [wrap, setWrap] = useState(false);
  const [stick, setStick] = useState(true);
  const parent = useRef<HTMLDivElement>(null);
  const liveRef = useRef(live);
  liveRef.current = live;

  useEffect(() => {
    let alive = true;
    setLines([]);
    api.get<{ lines: string[] }>(`/api/i/${inst}/logs/${key}?lines=3000`).then((r) => alive && setLines(r.lines)).catch(() => {});
    const off = followLogs([key], (k, rows) => {
      if (k !== key || !liveRef.current) return;
      setLines((cur) => { const next = cur.concat(rows); return next.length > 20000 ? next.slice(-15000) : next; });
    });
    return () => { alive = false; off(); };
  }, [inst, key]);

  const shown = useMemo(() => {
    const k = q.trim();
    if (!k) return lines;
    try {
      const re = new RegExp(k, "i");
      return lines.filter((l) => re.test(l));
    } catch {
      return lines.filter((l) => l.includes(k));
    }
  }, [lines, q]);

  const v = useVirtualizer({ count: shown.length, getScrollElement: () => parent.current, estimateSize: () => (wrap ? 40 : 20), overscan: 30 });
  useEffect(() => {
    if (stick && shown.length) v.scrollToIndex(shown.length - 1, { align: "end" });
  }, [shown.length, stick]);

  const hi = (line: string) => {
    const k = q.trim();
    if (!k) return line;
    const i = line.toLowerCase().indexOf(k.toLowerCase());
    if (i < 0) return line;
    return <>{line.slice(0, i)}<mark>{line.slice(i, i + k.length)}</mark>{line.slice(i + k.length)}</>;
  };

  const grouped = useMemo(() => {
    const m = new Map<string, Source[]>();
    (sources ?? []).forEach((s) => m.set(s.group, [...(m.get(s.group) ?? []), s]));
    return m;
  }, [sources]);

  return (
    <div className="split">
      <div className="list">
        <div style={{ padding: "14px 14px 8px" }}><h2 className="page-title" style={{ fontSize: 17 }}>{tx("日志")}</h2><div className="faint" style={{ fontSize: 12 }}>{tx("实例")} {inst}</div></div>
        {[...grouped.entries()].map(([g, list]) => (
          <div key={g}>
            <div className="nav-section" style={{ paddingLeft: 14 }}>{GROUPS[g] ?? g}</div>
            {list.map((s) => (
              <div key={s.key} className={`list-item ${key === s.key ? "on" : ""}`} onClick={() => setKey(s.key)}>
                <ScrollText size={14} className="faint" />
                <div className="grow">
                  <div className="ellipsis">{tx(s.label)}</div>
                  <div className="faint" style={{ fontSize: 11 }}>{(s.size / 1024).toFixed(0)} KB · {new Date(s.mtime * 1000).toLocaleTimeString(locale)}</div>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", flexDirection: "column", minHeight: 0, minWidth: 0 }}>
        <div className="row" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "var(--bg-elev)" }}>
          <div className="search grow" style={{ maxWidth: 420 }}><Search size={14} /><input className="input" style={{ width: "100%" }} placeholder={tx("过滤（支持正则）")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <Badge>{shown.length} {tx("/ {0} 行", lines.length)}</Badge>
          <div className="right row" style={{ gap: 4 }}>
            <button className={`btn sm ${live ? "active" : ""}`} onClick={() => setLive(!live)}>{live ? <><Pause size={13} />{tx("实时")}</> : <><Play size={13} />{tx("已暂停")}</>}</button>
            <button className={`btn sm ${stick ? "active" : ""}`} onClick={() => setStick(!stick)} title={tx("跟到最新")}><ArrowDownToLine size={13} />{tx("跟随")}</button>
            <button className={`btn sm ${wrap ? "active" : ""}`} onClick={() => setWrap(!wrap)}><WrapText size={13} />{tx("换行")}</button>
            <button className="btn sm" onClick={() => { navigator.clipboard.writeText(shown.join("\n")); toast(tx("已复制 {0} 行", shown.length), "success"); }}><Copy size={13} />{tx("复制")}</button>
          </div>
        </div>
        <div ref={parent} className="log-view" style={{ overflow: "auto", flex: 1, minHeight: 0 }}
          onWheel={(e) => { if (e.deltaY < 0) setStick(false); }}>
          {!shown.length ? (
            <Empty icon={<ScrollText size={30} />} title={lines.length ? tx("没有匹配「{0}」的行", q.trim()) : tx("没有内容")}>
              {!lines.length && sources && !sources.some((s) => s.key === key) ? tx("这个实例还没有这份日志") : null}
            </Empty>
          ) : (
            <div style={{ height: v.getTotalSize(), position: "relative", minWidth: wrap ? undefined : "max-content" }}>
              {v.getVirtualItems().map((vi) => {
                const line = shown[vi.index];
                return (
                  <div key={vi.index} ref={v.measureElement} data-index={vi.index} className={`log-line ${wrap ? "wrap" : ""} ${tone(line)}`}
                    style={{ position: "absolute", top: 0, left: 0, right: 0, transform: `translateY(${vi.start}px)` }}>
                    {hi(line)}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
