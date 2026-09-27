import { useEffect, useMemo, useState } from "react";
import {
  Crosshair, Eye, Footprints, Hand, Layers as LayersIcon, LocateFixed, MousePointer2, Move, Route, Shield, Sparkles, Square, Target, Unlock, Video,
} from "lucide-react";
import MapView, { DEFAULT_LAYERS, type Layers } from "../components/MapView";
import { Bar, Badge, Empty, Switch, UnitIcon, pageHidden } from "../components/ui";
import { store, useApp, toast } from "../lib/store";
import { api, cam, command, I } from "../lib/api";
import { useCam, useInstanceMap, useUnits } from "../lib/hooks";
import { BUILD, CATEGORY_LABEL, TRAIN, category, hex, orderLabel, ownerColor, ownerLabel, unitName } from "../lib/game";
import type { Unit, UnitDetail } from "../lib/types";
import { tx } from "../lib/i18n";

const LAYER_LABEL: Record<keyof Layers, string> = {
  terrain: tx("地形"), units: tx("单位"), creeps: tx("野怪"), armies: tx("军团"), threat: tx("城镇威胁"), target: tx("出击/打野目标"), intents: tx("AI 意图"), camera: tx("镜头视野"), grid: tx("网格"), hp: tx("血条"),
};

function loadLayers(): Layers {
  try { return { ...DEFAULT_LAYERS, ...JSON.parse(localStorage.getItem("farsight.layers") || "{}") }; } catch { return DEFAULT_LAYERS; }
}

export default function Battlefield() {
  const meta = useApp((s) => s.meta);
  const snapshot = useApp((s) => s.snapshot);
  const brain = useApp((s) => s.brain);
  const selection = useApp((s) => s.selection);
  const armed = useApp((s) => s.armed);
  const inst = useApp((s) => s.inst);
  const mapInfo = useInstanceMap();
  const camStatus = useCam(1500);
  const { units, me } = useUnits();
  const [layers, setLayers] = useState<Layers>(loadLayers);
  const [showLayers, setShowLayers] = useState(false);
  const byAddr = useMemo(() => new Map(units.map((u) => [u.addr, u])), [units]);
  const selected = selection.map((a) => byAddr.get(a)).filter(Boolean) as Unit[];
  const own = selected.filter((u) => u.owner === me);
  const movers = own.filter((u) => !["building", "hall", "tower", "mine"].includes(category(u.type, meta)));

  useEffect(() => localStorage.setItem("farsight.layers", JSON.stringify(layers)), [layers]);

  const armedLabel = !armed ? null : armed.kind === "build" ? tx("放置建筑：{0}", unitName(armed.code, meta)) : armed.kind === "cast" ? tx("施放：{0}", meta?.abilities[armed.code] ?? armed.code) :
    ({ move: tx("移动到…"), attack: tx("攻击（点地面 = 攻击移动）"), patrol: tx("巡逻到…"), smart: tx("右键到…"), rally: tx("设置集结点") } as Record<string, string>)[armed.kind];

  const issue = async (kind: string, x: number, y: number, target: Unit | null, extra: Record<string, unknown> = {}) => {
    const addrs = (extra.units as number[]) ?? movers.map((u) => u.addr);
    if (!addrs.length) { toast(tx("先选中我方可移动的单位"), "info"); return; }
    await command({ kind, units: addrs, x: Math.round(x), y: Math.round(y), target: target?.addr, ...extra }, ({ smart: tx("右键"), move: tx("移动"), attack: tx("攻击"), patrol: tx("巡逻") } as Record<string, string>)[kind] ?? kind);
  };

  const onPrimary = (x: number, y: number, target: Unit | null, shift: boolean) => {
    const a = store.get().armed;
    if (!a) return false;
    if (a.kind === "build") {
      const worker = own.find((u) => category(u.type, meta) === "worker");
      if (!worker) toast(tx("选中一个我方工人再放建筑"), "info");
      else command({ kind: "build", units: [worker.addr], code: a.code, x: Math.round(x), y: Math.round(y) }, tx("建造 {0}", unitName(a.code, meta)));
    } else if (a.kind === "cast") {
      command({ kind: "cast", units: [a.caster], code: a.code, order: a.order, x: Math.round(x), y: Math.round(y), target: a.target === "unit" ? target?.addr : undefined }, tx("施放 {0}", meta?.abilities[a.code] ?? a.code));
    } else if (a.kind === "rally") {
      const blds = own.filter((u) => ["building", "hall"].includes(category(u.type, meta)));
      command({ kind: "rally", units: blds.map((u) => u.addr), x: Math.round(x), y: Math.round(y) }, tx("集结点"));
    } else {
      issue(a.kind, x, y, target);
    }
    if (!shift) store.set({ armed: null });
    return true;
  };

  const onSecondary = (x: number, y: number, target: Unit | null) => {
    if (store.get().armed) { store.set({ armed: null }); return; }
    if (movers.length) issue("smart", x, y, target);
    else if (own.some((u) => ["building", "hall"].includes(category(u.type, meta))))
      command({ kind: "rally", units: own.map((u) => u.addr), x: Math.round(x), y: Math.round(y) }, tx("集结点"));
  };

  // 快捷键
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || e.ctrlKey || e.altKey || e.metaKey) return;
      const key = e.key.toLowerCase();
      if (key === "escape") store.set({ armed: null, selection: e.shiftKey ? [] : store.get().selection });
      if (!movers.length) return;
      if (key === "m") store.set({ armed: { kind: "move" } });
      if (key === "a") store.set({ armed: { kind: "attack" } });
      if (key === "p") store.set({ armed: { kind: "patrol" } });
      if (key === "s") command({ kind: "stop", units: movers.map((u) => u.addr) }, tx("停止"));
      if (key === "h") command({ kind: "hold", units: movers.map((u) => u.addr) }, tx("保持位置"));
      if (key === "c" && selected[0]) cam("look", { x: selected[0].x, y: selected[0].y, seconds: 0.3 });
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [movers, selected]);

  return (
    <div className="battle">
      <div className="battle-map">
        {!snapshot && <div className="center" style={{ position: "absolute", inset: 0, zIndex: 2 }}><Empty icon={<Eye size={34} />} title={tx("还没有快照")}>{tx("实例 {0} 的游戏没连上，或者还在加载", inst)}</Empty></div>}
        <MapView
          snapshot={snapshot} meta={meta} mapInfo={mapInfo} brain={brain} cam={camStatus?.cam ?? null} selection={selection} layers={layers}
          armedLabel={armedLabel} fitKey={`${inst}|${mapInfo?.path ?? ""}`}
          onSelect={(addrs, add) => store.set((s) => ({ selection: add ? Array.from(new Set([...s.selection, ...addrs])) : addrs, focus: addrs[0] ?? null }))}
          onPrimary={onPrimary}
          onSecondary={onSecondary}
          onDouble={(x, y) => cam("look", { x, y, seconds: 0.35 })}
        />
        <div className="map-toolbar">
          <div className="glass row" style={{ padding: "4px 6px", gap: 4 }}>
            <button className={`btn sm ghost ${showLayers ? "active" : ""}`} onClick={() => setShowLayers(!showLayers)}><LayersIcon size={14} />{tx("图层")}</button>
            <span className="faint" style={{ fontSize: 12, padding: "0 6px" }}>{mapInfo?.name ?? "—"}</span>
            {brain && <Badge tone="accent">AI：{({ home: tx("在家"), creep: tx("打野"), gather: tx("集结"), attack: tx("出击"), retreat: tx("撤退") } as Record<string, string>)[brain.attack.state] ?? brain.attack.state}</Badge>}
          </div>
          {showLayers && (
            <div className="glass" style={{ padding: 10, display: "grid", gridTemplateColumns: "repeat(2, auto)", gap: "6px 14px" }}>
              {(Object.keys(LAYER_LABEL) as (keyof Layers)[]).map((k) => (
                <label key={k} className="row" style={{ gap: 8, fontSize: 12.5, cursor: "pointer" }}>
                  <Switch on={layers[k]} onChange={(v) => setLayers({ ...layers, [k]: v })} />{LAYER_LABEL[k]}
                </label>
              ))}
            </div>
          )}
        </div>
        {armedLabel && <div className="map-mode glass"><Crosshair size={14} style={{ verticalAlign: -2 }} /> {armedLabel} <span className="faint" style={{ fontWeight: 400 }}>{tx("· 左键确定 · 右键/Esc 取消 · Shift 连续")}</span></div>}
        <div className="map-hint glass">
          <span><MousePointer2 size={12} /> {tx("左键选 / 拖框")}</span>
          <span>{tx("右键下令")}</span>
          <span>{tx("滚轮缩放 · 中键/空格拖动")}</span>
          <span>{tx("双击 = 游戏镜头看这里")}</span>
        </div>
      </div>
      <aside className="battle-side">
        <SidePanel selected={selected} me={me} movers={movers} />
      </aside>
    </div>
  );
}

function SidePanel({ selected, me, movers }: { selected: Unit[]; me: number; movers: Unit[] }) {
  const meta = useApp((s) => s.meta);
  const armed = useApp((s) => s.armed);
  if (!selected.length)
    return (
      <div style={{ padding: 16 }}>
        <Empty icon={<MousePointer2 size={30} />} title={tx("在地图上选中单位")}>
          {tx("左键点选或拖框；选中我方单位后可以右键下令、施法、训练、建造。")}<br />{tx("手动命令会以最高优先级（95）租下这个单位，租期内 AI 不会覆盖。")}
        </Empty>
        <Legend me={me} />
      </div>
    );
  const u = selected[0];
  const single = selected.length === 1;
  const mineSel = selected.filter((x) => x.owner === me);
  const groups = new Map<string, Unit[]>();
  selected.forEach((x) => groups.set(x.type, [...(groups.get(x.type) ?? []), x]));
  return (
    <div style={{ overflow: "auto", padding: 14 }}>
      {single ? <UnitCard u={u} me={me} /> : (
        <div>
          <div className="row" style={{ marginBottom: 10 }}>
            <b style={{ fontSize: 15 }}>{tx("已选 {0} 个单位", selected.length)}</b>
            <button className="btn ghost sm right" onClick={() => store.set({ selection: [] })}>{tx("清空")}</button>
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            {[...groups.entries()].map(([t, list]) => (
              <span key={t} className="unit-chip" onClick={() => store.set({ selection: list.map((x) => x.addr) })} title={tx("只选这一种")}>
                <UnitIcon code={t} color={ownerColor(list[0].owner, me)} size={22} />
                {unitName(t, meta)} <b>×{list.length}</b>
              </span>
            ))}
          </div>
          <div style={{ marginTop: 12 }}>
            <Bar value={selected.reduce((s, x) => s + x.hp, 0)} max={selected.reduce((s, x) => s + x.hpMax, 0)} />
            <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>{tx("总血量 {0} /", selected.reduce((s, x) => s + x.hp, 0))} {selected.reduce((s, x) => s + x.hpMax, 0)}</div>
          </div>
        </div>
      )}

      {movers.length > 0 && (
        <>
          <div className="section-label"><Swords14 />{tx("命令 · {0} 个单位", movers.length)}</div>
          <div className="cmd-grid">
            <Cmd icon={<Move size={17} />} label={tx("移动")} hot="M" on={armed?.kind === "move"} onClick={() => store.set({ armed: { kind: "move" } })} />
            <Cmd icon={<Target size={17} />} label={tx("攻击")} hot="A" on={armed?.kind === "attack"} onClick={() => store.set({ armed: { kind: "attack" } })} />
            <Cmd icon={<Route size={17} />} label={tx("巡逻")} hot="P" on={armed?.kind === "patrol"} onClick={() => store.set({ armed: { kind: "patrol" } })} />
            <Cmd icon={<Square size={16} />} label={tx("停止")} hot="S" onClick={() => command({ kind: "stop", units: movers.map((x) => x.addr) }, tx("停止"))} />
            <Cmd icon={<Shield size={17} />} label={tx("保持")} hot="H" onClick={() => command({ kind: "hold", units: movers.map((x) => x.addr) }, tx("保持位置"))} />
            <Cmd icon={<Hand size={17} />} label={tx("右键")} onClick={() => store.set({ armed: { kind: "smart" } })} on={armed?.kind === "smart"} />
            <Cmd icon={<Unlock size={17} />} label={tx("交还 AI")} onClick={() => command({ kind: "release", units: movers.map((x) => x.addr) }, tx("交还 AI"))} />
            <Cmd icon={<LocateFixed size={17} />} label={tx("镜头看")} hot="C" onClick={() => cam("look", { x: u.x, y: u.y, seconds: 0.3 })} />
          </div>
        </>
      )}

      {single && u.owner === me && TRAIN[u.type] && (
        <>
          <div className="section-label"><Sparkles size={12} />{tx("训练 / 研究")}</div>
          <div className="cmd-grid">
            {TRAIN[u.type].map((code) => (
              <Cmd key={code} icon={<UnitIcon code={code} color={code[0] === "R" ? "#64748b" : ownerColor(me, me)} size={22} />} label={unitName(code, meta).slice(0, 5)}
                title={`${unitName(code, meta)}（${code}）${meta?.units[code]?.gold != null ? tx(" · {0} 金 {1} 木", meta.units[code].gold, meta.units[code].lumber) : ""}`}
                onClick={() => command({ kind: "train", units: [u.addr], code }, tx("训练 {0}", unitName(code, meta)))} />
            ))}
            <Cmd icon={<Footprints size={17} />} label={tx("集结点")} on={armed?.kind === "rally"} onClick={() => store.set({ armed: { kind: "rally" } })} />
          </div>
        </>
      )}

      {mineSel.some((x) => BUILD[x.type]) && (
        <>
          <div className="section-label"><Sparkles size={12} />{tx("建造（选位置后左键）")}</div>
          <div className="cmd-grid">
            {BUILD[mineSel.find((x) => BUILD[x.type])!.type].map((code) => (
              <Cmd key={code} icon={<UnitIcon code={code} color="#64748b" size={22} />} label={unitName(code, meta).slice(0, 5)} on={armed?.kind === "build" && armed.code === code}
                title={`${unitName(code, meta)}${meta?.units[code]?.gold != null ? tx(" · {0} 金 {1} 木", meta.units[code].gold, meta.units[code].lumber) : ""}`}
                onClick={() => store.set({ armed: { kind: "build", code } })} />
            ))}
          </div>
        </>
      )}
      <div className="faint" style={{ fontSize: 11.5, marginTop: 14, lineHeight: 1.6 }}>
        {tx("手动命令按控制台设置的租期（默认 8 秒）占住单位，AI 各层在租期内让开；点「交还 AI」立刻释放。")}
      </div>
    </div>
  );
}

function Swords14() { return <Crosshair size={12} />; }

function Cmd({ icon, label, hot, on, onClick, title, disabled }: { icon: React.ReactNode; label: string; hot?: string; on?: boolean; onClick: () => void; title?: string; disabled?: boolean }) {
  return (
    <button className={`cmd ${on ? "on" : ""}`} onClick={onClick} title={title ?? label} disabled={disabled}>
      {icon}
      <span className="ellipsis" style={{ maxWidth: "100%" }}>{label}</span>
      {hot && <span className="hot">{hot}</span>}
    </button>
  );
}

function UnitCard({ u, me }: { u: Unit; me: number }) {
  const meta = useApp((s) => s.meta);
  const inst = useApp((s) => s.inst);
  const armed = useApp((s) => s.armed);
  const [d, setD] = useState<UnitDetail | null>(null);
  const cat = category(u.type, meta);
  useEffect(() => {
    let alive = true;
    let busy = false;
    setD(null);
    const run = () => {
      if (busy || pageHidden()) return;                     // 上一次没回来 / 页面藏起来：跳过这一轮
      busy = true;
      api.get<UnitDetail>(I(`/unit/${u.addr}?type=${u.type}`)).then((r) => alive && setD(r)).catch(() => {}).finally(() => { busy = false; });
    };
    run();
    const id = setInterval(run, 1500);
    return () => { alive = false; clearInterval(id); };
  }, [u.addr, inst]);
  const stats = meta?.units[u.type];
  const skills = (d?.abilities ?? []).filter((a) => !a.buff && !a.code.startsWith("AI"));
  const buffs = (d?.abilities ?? []).filter((a) => a.buff);
  return (
    <div>
      <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
        <UnitIcon code={u.type} color={ownerColor(u.owner, me)} size={44} />
        <div className="grow">
          <div style={{ fontWeight: 700, fontSize: 15.5, fontFamily: "var(--font-display)" }}>{unitName(u.type, meta)}</div>
          <div className="row wrap" style={{ gap: 5, marginTop: 3 }}>
            <Badge tone={u.owner === me ? "success" : u.owner >= 12 ? "" : "danger"}>{ownerLabel(u.owner, me)}</Badge>
            <Badge>{CATEGORY_LABEL[cat]}</Badge>
            {d?.level != null && <Badge tone="accent">{d.level} {tx("级")}</Badge>}
            <span className="mono faint">{u.type}</span>
          </div>
        </div>
      </div>
      <div style={{ marginTop: 12 }} className="col gap4">
        <div className="row"><span className="faint" style={{ width: 34, fontSize: 12 }}>{tx("生命")}</span><div className="grow"><Bar value={u.hp} max={u.hpMax} /></div><span className="mono" style={{ width: 84, textAlign: "right" }}>{u.hp}/{u.hpMax}</span></div>
        {d?.manaMax ? <div className="row"><span className="faint" style={{ width: 34, fontSize: 12 }}>{tx("魔法")}</span><div className="grow"><Bar value={d.mana ?? 0} max={d.manaMax} kind="mana" /></div><span className="mono" style={{ width: 84, textAlign: "right" }}>{Math.round(d.mana ?? 0)}/{Math.round(d.manaMax)}</span></div> : null}
        {d?.xp != null && d.level != null && <div className="row"><span className="faint" style={{ width: 34, fontSize: 12 }}>{tx("经验")}</span><div className="grow"><Bar value={d.xp} max={[0, 200, 500, 900, 1400, 2000, 2700, 3500, 4400, 5400][d.level] ?? d.xp} kind="xp" /></div><span className="mono" style={{ width: 84, textAlign: "right" }}>{d.xp}</span></div>}
      </div>
      <div className="kv" style={{ marginTop: 12 }}>
        <span>{tx("订单")}</span><span>{orderLabel(d?.order?.order ?? u.order, meta)}{d?.order && d.order.x ? <span className="faint mono"> → ({Math.round(d.order.x)}, {Math.round(d.order.y)})</span> : null}</span>
        <span>{tx("任务")}</span><span>{d?.task?.cls ?? (u.hasTask === 1 ? tx("有") : u.hasTask === 0 ? tx("空闲") : "—")}</span>
        <span>{tx("位置")}</span><span className="mono">({u.x}, {u.y})</span>
        {stats?.armor != null && <><span>{tx("护甲 / 攻击")}</span><span>{stats.armor} · {stats.dmgAvg ?? "—"}（{stats.atk}{tx("）· 射程")} {stats.range}</span></>}
        <span>{tx("地址")}</span><span className="mono">{hex(u.addr)}</span>
      </div>
      {skills.length > 0 && (
        <>
          <div className="section-label"><Sparkles size={12} />{tx("技能")}{d?.skillPoints ? <Badge tone="warning">{d.skillPoints} {tx("个技能点")}</Badge> : null}</div>
          <div className="cmd-grid">
            {skills.map((a) => {
              const canCast = u.owner === me && a.cast;
              return (
                <button key={a.code} className={`cmd ${armed?.kind === "cast" && armed.code === a.code ? "on" : ""}`} disabled={!canCast}
                  title={tx("{0}（{1}）{2} 级{3}", a.name ?? a.code, a.code, a.level, a.cast ? ` · ${a.cast.orderName} · ${a.cast.target === "point" ? tx("对地") : a.cast.target === "unit" ? tx("对单位") : tx("立即")}` : tx(" · 被动/不支持手动"))}
                  onClick={() => {
                    if (!a.cast) return;
                    if (a.cast.target === "instant") command({ kind: "cast", units: [u.addr], code: a.code, order: a.cast.order }, tx("施放 {0}", a.name ?? a.code));
                    else store.set({ armed: { kind: "cast", code: a.code, order: a.cast.order, target: a.cast.target, caster: u.addr } });
                  }}>
                  <span style={{ fontWeight: 700, fontSize: 12 }}>{(a.name ?? a.code).slice(0, 4)}</span>
                  <span className="faint">Lv{a.level}</span>
                  {a.cooling && a.cooldown != null && a.cooldown > 0 && <span className="cd">{Math.ceil(a.cooldown)}</span>}
                </button>
              );
            })}
          </div>
        </>
      )}
      {d?.inventory && (
        <>
          <div className="section-label">{tx("物品栏")}</div>
          <div className="cmd-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
            {d.inventory.map((it, i) => (
              <button key={i} className="cmd" disabled={!it || u.owner !== me} title={it ? tx("{0}（{1}）· 点击使用", unitName(it, meta), it) : tx("空")}
                onClick={() => it && command({ kind: "use_item", units: [u.addr], slot: i }, tx("使用 {0}", unitName(it, meta)))}>
                {it ? <><span style={{ fontWeight: 600 }}>{unitName(it, meta).slice(0, 4)}</span><span className="faint mono">{it}</span></> : <span className="faint">{tx("空")}</span>}
              </button>
            ))}
          </div>
        </>
      )}
      {buffs.length > 0 && (
        <div className="row wrap" style={{ gap: 5, marginTop: 10 }}>
          {buffs.map((b) => <Badge key={b.code} tone="info" title={b.code}>{b.name ?? b.code}</Badge>)}
        </div>
      )}
      <div className="row" style={{ marginTop: 12, gap: 6 }}>
        <button className="btn sm" onClick={() => cam("look", { x: u.x, y: u.y, seconds: 0.3 })}><LocateFixed size={13} />{tx("镜头看")}</button>
        <button className="btn sm" onClick={() => cam("follow", { target: { addr: u.addr, x: u.x, y: u.y } })}><Video size={13} />{tx("镜头跟随")}</button>
        <button className="btn sm ghost" onClick={() => cam("release")}>{tx("取消跟随")}</button>
      </div>
    </div>
  );
}

function Legend({ me }: { me: number }) {
  const items = [
    { c: ownerColor(me, me), t: tx("我方单位（圆）/ 建筑（方）") },
    { c: ownerColor(me === 0 ? 1 : 0, me), t: tx("对手") },
    { c: "#6b2b2b", t: tx("野怪") },
    { c: "#f5c542", t: tx("金矿（菱形）") },
  ];
  return (
    <div className="card" style={{ marginTop: 16, padding: 12 }}>
      <div className="section-label" style={{ marginTop: 0 }}>{tx("图例")}</div>
      <div className="col gap4" style={{ fontSize: 12.5 }}>
        {items.map((i) => <div key={i.t} className="row"><i style={{ width: 10, height: 10, borderRadius: 2, background: i.c, display: "inline-block" }} />{i.t}</div>)}
        <div className="row"><i style={{ width: 10, height: 10, borderRadius: 5, border: "2px solid #fff", display: "inline-block" }} />{tx("英雄（白边）")}</div>
        <div className="row"><i style={{ width: 14, height: 0, borderTop: "2px dashed #facc15", display: "inline-block" }} />{tx("AI 出击路线 / 打野营地")}</div>
        <div className="row"><i style={{ width: 10, height: 10, borderRadius: 5, border: "1.5px solid #ef4444", display: "inline-block" }} />{tx("敌方军团（箭头 = 移动方向）")}</div>
      </div>
    </div>
  );
}
