import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDownUp, LocateFixed, Search, Swords, Users } from "lucide-react";
import { Badge, Bar, Card, Empty, Seg } from "../components/ui";
import { navigate, store, useApp } from "../lib/store";
import { useUnits } from "../lib/hooks";
import { CATEGORY_LABEL, category, hex, orderLabel, ownerColor, ownerLabel, unitName, type Category } from "../lib/game";
import { cam } from "../lib/api";
import type { Unit } from "../lib/types";
import { tx } from "../lib/i18n";

type SortKey = "owner" | "name" | "cat" | "hp" | "x" | "y" | "order";

export default function Units() {
  const meta = useApp((s) => s.meta);
  const selection = useApp((s) => s.selection);
  const { units, me } = useUnits();
  const [q, setQ] = useState("");
  const [who, setWho] = useState<"all" | "me" | "foe" | "neutral">("all");
  const [cat, setCat] = useState<"all" | Category>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "owner", dir: 1 });
  const parent = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    const k = q.trim().toLowerCase();
    let list = units.filter((u) => {
      if (who === "me" && u.owner !== me) return false;
      if (who === "foe" && (u.owner === me || u.owner >= 12)) return false;
      if (who === "neutral" && u.owner < 12) return false;
      const c = category(u.type, meta);
      if (cat !== "all" && c !== cat) return false;
      if (k && !(`${unitName(u.type, meta)} ${u.type} ${hex(u.addr)} ${orderLabel(u.order, meta)}`.toLowerCase().includes(k))) return false;
      return true;
    });
    const val = (u: Unit): number | string => {
      switch (sort.key) {
        case "owner": return u.owner * 10000 + (u.type.charCodeAt(0) || 0);
        case "name": return unitName(u.type, meta);
        case "cat": return category(u.type, meta);
        case "hp": return u.hpMax ? u.hp / u.hpMax : 0;
        case "x": return u.x;
        case "y": return u.y;
        case "order": return orderLabel(u.order, meta);
      }
    };
    list = [...list].sort((a, b) => {
      const va = val(a), vb = val(b);
      return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
    });
    return list;
  }, [units, q, who, cat, sort, me, meta]);

  const v = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 38, overscan: 12 });
  const cols = "44px minmax(150px,1.4fr) 90px 160px 120px 140px 110px 110px 90px";
  const sel = new Set(selection);
  const head = (key: SortKey, label: string) => (
    <div onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
      {label}{sort.key === key && <ArrowDownUp size={11} />}
    </div>
  );
  const summary = useMemo(() => {
    const c: Record<string, number> = { me: 0, foe: 0, neutral: 0 };
    units.forEach((u) => { if (u.owner === me) c.me++; else if (u.owner >= 12) c.neutral++; else c.foe++; });
    return c;
  }, [units, me]);

  return (
    <div className="page" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("单位数据")}</h1>
          <p className="page-desc">{tx("全图快照（4 次/秒）：我方 {0} · 对手 {1} · 中立/野怪 {2}。点一行选中，双击镜头看过去。", summary.me, summary.foe, summary.neutral)}</p>
        </div>
      </div>
      <Card className="grow" style={{ display: "flex", flexDirection: "column", minHeight: 0 }} bodyClass="" title={
        <div className="row wrap" style={{ gap: 8 }}>
          <div className="search"><Search size={14} /><input className="input" placeholder={tx("名称 / 四字码 / 地址 / 订单")} value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240 }} /></div>
          <Seg value={who} onChange={setWho} options={[{ value: "all", label: tx("全部") }, { value: "me", label: tx("我方") }, { value: "foe", label: tx("对手") }, { value: "neutral", label: tx("中立") }]} />
          <select className="select" value={cat} onChange={(e) => setCat(e.target.value as any)}>
            <option value="all">{tx("全部类别")}</option>
            {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
          </select>
          <Badge>{rows.length} {tx("行")}</Badge>
        </div>
      } actions={
        <button className="btn sm" disabled={!selection.length} onClick={() => navigate("battlefield")}><Swords size={13} />{tx("去战场指挥选中的")} {selection.length || ""}</button>
      }>
        {!units.length ? <Empty icon={<Users size={30} />} title={tx("没有快照")} /> : (
          <div ref={parent} style={{ overflow: "auto", flex: 1, minHeight: 0, height: "calc(100vh - 250px)" }}>
            <div className="vt-head" style={{ gridTemplateColumns: cols }}>
              <div />{head("name", tx("单位"))}{head("cat", tx("类别"))}{head("hp", tx("生命"))}{head("owner", tx("归属"))}{head("order", tx("订单"))}{head("x", "X")}{head("y", "Y")}<div>{tx("地址")}</div>
            </div>
            <div style={{ height: v.getTotalSize(), position: "relative" }}>
              {v.getVirtualItems().map((vi) => {
                const u = rows[vi.index];
                const c = category(u.type, meta);
                return (
                  <div key={u.addr} className={`vt-row ${sel.has(u.addr) ? "sel" : ""}`}
                    style={{ gridTemplateColumns: cols, position: "absolute", top: 0, left: 0, right: 0, height: vi.size, transform: `translateY(${vi.start}px)` }}
                    onClick={(e) => store.set((s) => ({ selection: e.shiftKey ? Array.from(new Set([...s.selection, u.addr])) : [u.addr], focus: u.addr }))}
                    onDoubleClick={() => cam("look", { x: u.x, y: u.y, seconds: 0.3 })}>
                    <div><i style={{ width: 10, height: 10, borderRadius: c === "hero" ? 5 : 2, background: ownerColor(u.owner, me), display: "inline-block", outline: c === "hero" ? "2px solid var(--text-3)" : undefined }} /></div>
                    <div><b style={{ fontWeight: 550 }}>{unitName(u.type, meta)}</b> <span className="mono faint">{u.type}</span></div>
                    <div className="muted">{CATEGORY_LABEL[c]}</div>
                    <div className="row" style={{ gap: 6 }}><div className="grow"><Bar value={u.hp} max={u.hpMax} /></div><span className="mono faint" style={{ fontSize: 11 }}>{u.hp}</span></div>
                    <div className="muted">{ownerLabel(u.owner, me)}</div>
                    <div className="muted">{orderLabel(u.order, meta)}</div>
                    <div className="mono">{u.x}</div>
                    <div className="mono">{u.y}</div>
                    <div className="mono faint" style={{ fontSize: 11 }}>
                      <button className="btn ghost icon sm" title={tx("镜头看")} onClick={(e) => { e.stopPropagation(); cam("look", { x: u.x, y: u.y, seconds: 0.3 }); }}><LocateFixed size={13} /></button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
