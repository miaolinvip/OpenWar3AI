// 事件与输入（09-25）：这一局发生了什么 —— 放技能、聊天和屏幕消息、点按钮、热键、点地面、选中、玩家离开、生产完成……
// 后端 GET /api/i/{n}/events?since=（console/server/eventsview.py）每秒取一次增量；右边是鼠标 / 悬停 / 本机选中。
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Keyboard, MessageSquare, MousePointerClick, Pause, Play, Search, Trash2, Users } from "lucide-react";
import { Badge, Card, Empty, FetchState, Switch, pageHidden, useShowMore } from "../components/ui";
import { api } from "../lib/api";
import { useApp } from "../lib/store";
import { fmtTime, ownerColor, ownerLabel, unitName } from "../lib/game";
import type { Meta } from "../lib/types";
import { tx } from "../lib/i18n";

interface Ev {
  seq: number; kind: string; clock: number; addr: number; type: string; owner: number; x: number; y: number; value: number;
  spell?: string; spellName?: string | null; level?: number; cooldown?: number;
  text?: string | null; frame?: number; chat?: { channel: string | null; sender: string; text: string } | null;
  source?: number; sourceType?: string | null; sourceOwner?: number | null; attack?: string; damageType?: string; raw?: number;
  done?: string; doneKind?: string; player?: number; slot?: string;
  item?: number; button?: string | null; mods?: string[]; hotkey?: number; vk?: number; swallowed?: boolean; count?: number;
}
interface Resp {
  ok: boolean; error?: string; pid?: number; head?: number; dropped?: number; events?: Ev[]; clock?: number | null;
  input?: { enabled: boolean; cursor: [number, number] | null; world: [number, number, number] | null; hover: number; clicks: number; keys: number; back: [number, number] } | null;
  selection?: { addr: number; type: string; owner: number; hp: number; hpMax: number }[];
}

type Cat = "unit" | "combat" | "prod" | "spell" | "msg" | "input" | "game";
const CATS: { key: Cat | "all"; label: string }[] = [
  { key: "all", label: tx("全部") }, { key: "spell", label: tx("技能") }, { key: "msg", label: tx("消息与聊天") },
  { key: "input", label: tx("界面与输入") }, { key: "unit", label: tx("单位与物品") }, { key: "combat", label: tx("战斗") },
  { key: "prod", label: tx("生产") }, { key: "game", label: tx("对局") },
];
const KINDS: Record<string, { label: string; cat: Cat; tone: string }> = {
  "unit.appeared": { label: tx("单位出现"), cat: "unit", tone: "" },
  "unit.died": { label: tx("单位死亡"), cat: "unit", tone: "danger" },
  "unit.removed": { label: tx("单位消失"), cat: "unit", tone: "" },
  "unit.damaged": { label: tx("掉血"), cat: "combat", tone: "" },
  "order.changed": { label: tx("换订单"), cat: "unit", tone: "" },
  "hero.levelup": { label: tx("英雄升级"), cat: "unit", tone: "success" },
  "owner.changed": { label: tx("换主人"), cat: "unit", tone: "warning" },
  "item.appeared": { label: tx("物品出现"), cat: "unit", tone: "" },
  "item.removed": { label: tx("物品消失"), cat: "unit", tone: "" },
  damage: { label: tx("伤害"), cat: "combat", tone: "" },
  killed: { label: tx("击杀"), cat: "combat", tone: "danger" },
  "production.done": { label: tx("生产完成"), cat: "prod", tone: "success" },
  "spell.cast": { label: tx("放技能"), cat: "spell", tone: "accent" },
  message: { label: tx("屏幕消息"), cat: "msg", tone: "info" },
  "player.left": { label: tx("玩家离开"), cat: "game", tone: "danger" },
  "selection.changed": { label: tx("选择变化"), cat: "input", tone: "" },
  "ui.click": { label: tx("点了按钮"), cat: "input", tone: "accent" },
  "ui.hover": { label: tx("悬停"), cat: "input", tone: "" },
  hotkey: { label: tx("热键"), cat: "input", tone: "accent" },
  "mouse.world": { label: tx("点地面"), cat: "input", tone: "accent" },
  "game.started": { label: tx("对局开始"), cat: "game", tone: "success" },
  "game.ended": { label: tx("离开对局"), cat: "game", tone: "warning" },
};
const DONE_KIND: Record<string, string> = { training: tx("训练"), research: tx("研究"), construction: tx("建造"), upgrade: tx("升级") };
const MAX_KEEP = 2000;
const plain = (s: string | null | undefined) => (s ?? "").replace(/\|c[0-9a-fA-F]{8}|\|r/g, "");
const btn = (b: string | null | undefined) => (b === "right" ? tx("右键") : tx("左键"));
const withMods = (mods?: string[]) => (mods?.length ? mods.map((m) => m[0].toUpperCase() + m.slice(1)).join("+") + "+" : "");

function Who({ type, owner, me, meta }: { type: string; owner: number; me: number; meta: Meta | null }) {
  if (!type) return null;
  const name = unitName(type, meta);          // meta 按界面语言取的，已经是这门语言的名字
  return (
    <span className="row" style={{ gap: 5, display: "inline-flex" }} title={`${name} · ${ownerLabel(owner, me)}`}>
      <span className="dot" style={{ background: ownerColor(owner, me) }} />
      <b style={{ fontWeight: 560 }}>{name}</b>
    </span>
  );
}

function detail(e: Ev, meta: Meta | null, me: number) {
  switch (e.kind) {
    case "spell.cast": {
      const name = (e.spell && meta?.abilities?.[e.spell]) || (e.spellName ? tx(e.spellName) : e.spell);
      return <>{name} <span className="faint">Lv{e.level} · {tx("冷却 {0} 秒", e.cooldown)}</span></>;
    }
    case "message":
      if (e.chat) return <><b>{e.chat.sender}</b>{e.chat.channel ? <span className="faint">（{e.chat.channel}）</span> : null}：{e.chat.text}</>;
      return <span>{plain(e.text) || tx("（消息已被覆盖）")}</span>;
    case "damage":
    case "killed":
      return (
        <>
          {e.sourceType ? <Who type={e.sourceType} owner={e.sourceOwner ?? 0} me={me} meta={meta} /> : <span className="faint">{tx("来源不明")}</span>}
          <span className="faint"> → </span>{e.kind === "damage" ? tx("{0} 点（{1}）", Math.round(e.value), e.attack ?? "") : tx("打死了它")}
        </>
      );
    case "production.done":
      return <>{e.done ? unitName(e.done, meta) : "?"} <span className="faint">{DONE_KIND[e.doneKind ?? ""] ?? ""} · {tx("{0} 秒", Math.round(e.value))}</span></>;
    case "player.left":
      return <>{tx("玩家 {0}", (e.player ?? 0) + 1)} <span className="faint">→ {e.slot === "left" ? tx("离开 / 被判负") : e.slot}</span></>;
    case "ui.click":
      return <>{tx("画板项 #{0}", e.item)} <span className="faint">· {withMods(e.mods)}{btn(e.button)}</span></>;
    case "ui.hover":
      return e.item ? tx("移到画板项 #{0}", e.item) : tx("移出");
    case "hotkey":
      return <>{tx("热键 #{0}", e.hotkey)} <span className="faint">· {withMods(e.mods)}{tx("键码 {0}", e.vk)}</span></>;
    case "mouse.world":
      return <>({e.x}, {e.y}) <span className="faint">· {withMods(e.mods)}{btn(e.button)}{e.swallowed ? tx(" · 游戏没收到") : ""}</span></>;
    case "selection.changed":
      return tx("选中 {0} 个", e.count ?? 0);
    case "owner.changed":
      return tx("→ {0}", ownerLabel(e.owner, me));
    default:
      return null;
  }
}

export default function Events() {
  const inst = useApp((s) => s.inst);
  const meta = useApp((s) => s.meta);
  const me = useApp((s) => s.snapshot?.me ?? 0);
  const [events, setEvents] = useState<Ev[]>([]);
  const [info, setInfo] = useState<Resp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cat, setCat] = useState<Cat | "all">("all");
  const [q, setQ] = useState("");
  const [noisy, setNoisy] = useState(false);
  const [paused, setPaused] = useState(false);
  const since = useRef(0);
  const pid = useRef<number | undefined>(undefined);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  // 换实例 / 切换高频事件：从头取
  useEffect(() => {
    setEvents([]);
    setInfo(null);
    setError(null);
    since.current = 0;
    pid.current = undefined;
    let alive = true;
    let busy = false;
    const tick = async () => {
      if (pausedRef.current || busy || pageHidden()) return;   // 上一次没回来 / 页面藏起来：这一秒不拉
      busy = true;
      try {
        const r = await api.get<Resp>(`/api/i/${inst}/events?since=${since.current}&limit=800${noisy ? "&noisy=1" : ""}`);
        if (!alive) return;
        if (!r.ok) { setError(r.error ?? "?"); setInfo(null); return; }
        setError(null);
        setInfo(r);
        const fresh = r.events ?? [];
        if (pid.current !== r.pid) {                // 游戏换了进程（新一局）：事件序号从头来
          pid.current = r.pid;
          setEvents(fresh.slice().reverse().slice(0, MAX_KEEP));
        } else if (fresh.length) {
          setEvents((cur) => fresh.slice().reverse().concat(cur).slice(0, MAX_KEEP));
        }
        since.current = Math.max(since.current, r.head ?? 0);
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        busy = false;
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => { alive = false; clearInterval(id); };
  }, [inst, noisy]);

  const counts = useMemo(() => {
    const m: Record<string, number> = { all: events.length };
    for (const e of events) { const c = KINDS[e.kind]?.cat ?? "game"; m[c] = (m[c] ?? 0) + 1; }
    return m;
  }, [events]);

  const matched = useMemo(() => {
    const k = q.trim().toLowerCase();
    return events.filter((e) => {
      if (cat !== "all" && (KINDS[e.kind]?.cat ?? "game") !== cat) return false;
      if (!k) return true;
      const hay = [e.kind, KINDS[e.kind]?.label, e.type, e.type ? unitName(e.type, meta) : "", e.spell, e.spellName, plain(e.text), e.chat?.sender, e.chat?.text]
        .filter(Boolean).join(" ").toLowerCase();
      return hay.includes(k);
    });
  }, [events, cat, q, meta]);
  // 每秒都有新事件：只画最新的一批，要看更早的再点「再显示」（以前一次画 600 条、每秒重画）
  const { shown, more } = useShowMore(matched, 200, `${inst}|${cat}|${q}|${noisy}`);

  const inp = info?.input;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("实例 {0} · 事件与输入", inst)}</h1>
          <p className="page-desc">{tx("这一局发生了什么：放技能、聊天和屏幕消息、点按钮、热键、点地面、选中、玩家离开、生产完成……每秒刷新。")}</p>
        </div>
        <div className="right row" style={{ gap: 6 }}>
          <button className={`btn sm ${paused ? "" : "active"}`} onClick={() => setPaused(!paused)}>
            {paused ? <><Play size={13} />{tx("已暂停")}</> : <><Pause size={13} />{tx("实时")}</>}
          </button>
          <button className="btn sm" onClick={() => setEvents([])} title={tx("只清网页上的列表，不影响游戏")}><Trash2 size={13} />{tx("清空列表")}</button>
        </div>
      </div>

      <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
        <div className="seg wrap">
          {CATS.map((c) => (
            <button key={c.key} className={cat === c.key ? "on" : ""} onClick={() => setCat(c.key)}>
              {c.label}{counts[c.key] ? <span className="faint" style={{ marginLeft: 4 }}>{counts[c.key]}</span> : null}
            </button>
          ))}
        </div>
        <div className="search" style={{ minWidth: 220 }}><Search size={14} /><input className="input" placeholder={tx("搜索：单位、技能、聊天内容…")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <label className="row" style={{ gap: 6, fontSize: 12.5 }} title={tx("换订单、掉血、每一下伤害、悬停：一局几万条，默认不显示")}>
          <Switch on={noisy} onChange={setNoisy} /> {tx("显示高频事件")}
        </label>
        {info?.dropped ? <Badge tone="warning" title={tx("读得太慢被覆盖的事件数")}>{tx("丢了 {0} 条", info.dropped)}</Badge> : null}
      </div>

      <div className="grid g-main-side" style={{ alignItems: "start" }}>
        <Card title={tx("事件流")} icon={<Activity size={15} />} actions={<span className="faint" style={{ fontSize: 12 }}>{tx("显示 {0} / {1} 条", matched.length, events.length)}</span>} bodyClass="card-body tight">
          {!info && !events.length ? <FetchState error={error} title={tx("正在连游戏…")} /> : !shown.length ? (
            <Empty title={events.length ? tx("没有符合筛选的事件") : tx("还没有事件")}>
              {cat === "input" ? tx("界面与输入的事件要有程序开了输入才会有（g.ui、玩法模组、网关的 ui.*）。") : null}
            </Empty>
          ) : (
            <div className="ev-list">
              {shown.map((e) => {
                const k = KINDS[e.kind];
                return (
                  <div key={e.seq} className="ev-row">
                    <span className="mono faint ev-t">{fmtTime(e.clock)}</span>
                    <Badge tone={k?.tone ?? ""}>{k?.label ?? e.kind}</Badge>
                    <span className="ev-who"><Who type={e.type} owner={e.owner} me={me} meta={meta} /></span>
                    <span className="ev-detail">{detail(e, meta, me)}</span>
                  </div>
                );
              })}
              {more}
            </div>
          )}
        </Card>

        <div className="col gap12">
          <Card title={tx("鼠标与输入")} icon={<MousePointerClick size={15} />}>
            {!inp ? <Empty title={tx("没有输入块")}>{tx("这个游戏的运行时太旧，或者还没开局。")}</Empty> : (
              <div className="kv">
                <span>{tx("输入接管")}</span><b className={inp.enabled ? "success" : "faint"}>{inp.enabled ? tx("开着（有程序在用按钮 / 热键 / 点地面）") : tx("没开")}</b>
                <span>{tx("鼠标（画面像素）")}</span><b className="mono">{inp.cursor ? `${inp.cursor[0]}, ${inp.cursor[1]}` : tx("不在游戏窗口里")}</b>
                <span>{tx("指着的地面")}</span><b className="mono">{inp.world ? `${inp.world[0]}, ${inp.world[1]}` : "—"}</b>
                <span>{tx("悬停的画板项")}</span><b className="mono">{inp.hover ? `#${inp.hover}` : "—"}</b>
                <span>{tx("拦下的点击")}</span><b className="mono">{inp.clicks}</b>
                <span>{tx("报过的热键")}</span><b className="mono">{inp.keys}</b>
              </div>
            )}
          </Card>
          <Card title={tx("本机选中")} icon={<Users size={15} />}>
            {!info?.selection?.length ? <Empty title={tx("没选中单位")} /> : (
              <div className="col gap8">
                {info.selection.map((u) => (
                  <div key={u.addr} className="row" style={{ gap: 8 }}>
                    <Who type={u.type} owner={u.owner} me={me} meta={meta} />
                    <span className="right mono faint">{u.hp}/{u.hpMax}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title={tx("这些事件从哪来")} icon={<MessageSquare size={15} />}>
            <div className="col gap8 muted" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
              <div>{tx("运行时每 50 ms 对比一次世界，再加上捕获到的伤害和屏幕消息，写进事件环；Bot、模组、网关、MCP 读的是同一份。")}</div>
              <div><Keyboard size={13} style={{ verticalAlign: -2 }} /> {tx("点按钮、热键、点地面：程序先开了输入（g.ui）才会有，游戏收不到被拦下的那一下。")}</div>
              <div>{tx("聊天：玩家在聊天框打的字会出现在「屏幕消息」里，发言人和内容已经拆好。")}</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
