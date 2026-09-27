import { useEffect, useMemo, useRef, useState } from "react";
import { Keyboard, NotebookPen, Pause, Plus, Save, Trash2 } from "lucide-react";
import { Badge, Card, Confirm, Empty, Field, Seg, useFetch } from "../components/ui";
import { api, action } from "../lib/api";
import { toast, useApp } from "../lib/store";
import { ATTACK_STATE, DEFENSE_MODE, OUTCOME, fmtTime } from "../lib/game";
import type { Issue, IssueContext, IssueStatus, MatchSummary } from "../lib/types";
import { locale, tx } from "../lib/i18n";

// 问题备忘（farsight/server/issues.py）。09-19 新增：记录已发现的问题，可选进程和时间点（关联该时间点附近的事件）；
// 游戏里按快捷键直接记下当前进程的问题，同时暂停游戏，再按一次继续。
const STATUS: Record<IssueStatus, { label: string; tone: "warning" | "info" | "success" | "" }> = {
  draft: { label: tx("待填写"), tone: "warning" },
  open: { label: tx("待处理"), tone: "info" },
  fixed: { label: tx("已解决"), tone: "success" },
  wontfix: { label: tx("不是问题"), tone: "" },
};
// 分类存进问题记录的是中文原文（换语言也能按分类筛），显示时再 tx()
const CATEGORIES = ["战斗", "打野", "经济运营", "建造", "英雄技能", "发呆卡住", "指挥冲突", "界面显示", "其它"];
const WINDOWS = [15, 30, 60, 120];
type Filter = "todo" | IssueStatus | "all";

function parseClock(s: string): number | null {
  const m = s.trim().match(/^(\d+):(\d{1,2})$/);
  if (m) return Number(m[1]) * 60 + Number(m[2]);
  const n = Number(s);
  return s.trim() && Number.isFinite(n) ? n : null;
}

function when(ts: number) {
  const d = new Date(ts * 1000);
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function exactMatch(name: string | null | undefined) {
  const m = name?.match(/^match-\d{4}(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}-\d+-([0-9a-f]{8})/);
  return m ? `${m[1]}-${m[2]} ${m[3]}:${m[4]} · ${m[5]}` : "";
}

function where(x: { inst: number | null; game: number | null; gameTime: number | null; match?: string | null }) {
  return [x.inst != null ? tx("实例 {0}", x.inst) : "", x.game != null ? tx("第 {0} 局", String(x.game).padStart(3, "0")) : "",
    x.gameTime != null ? fmtTime(x.gameTime) : "", exactMatch(x.match)]
    .filter(Boolean).join(" · ");
}

export default function Issues() {
  const inst = useApp((s) => s.inst);
  const pending = useApp((s) => s.issuesPending);
  const { data, reload } = useFetch<{ issues: Issue[]; pending: number }>("/api/issues", 5000);
  const [filter, setFilter] = useState<Filter>("todo");
  const [sel, setSel] = useState<string | null>(null);        // 问题 id，"new" = 新建
  // 编辑器里有没保存的修改：切到别的条目前先问一句（以前直接丢）
  const dirty = useRef(false);
  const [pendingSel, setPendingSel] = useState<string | null>(null);
  const pick = (id: string) => {
    if (id === sel) return;
    if (dirty.current) setPendingSel(id);
    else setSel(id);
  };
  useEffect(() => { reload(); }, [pending]);                     // 游戏里按了快捷键：列表跟着刷新
  const all = data?.issues ?? [];
  const list = all.filter((x) => filter === "all" || (filter === "todo" ? x.status === "draft" || x.status === "open" : x.status === filter));
  const cur = sel && sel !== "new" ? all.find((x) => x.id === sel) ?? null : null;
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    all.forEach((x) => { c[x.status] = (c[x.status] ?? 0) + 1; });
    return c;
  }, [all]);
  const pauseHere = () => action(async () => {
    const r = await api.post<any>(`/api/instances/${inst}/issue-toggle`);
    if (!r?.ok) throw new Error(r?.error ?? r?.note ?? r?.summary ?? tx("没做成"));
    toast(r.summary?.includes("resumed") ? tx("实例 {0} 已继续", inst) : tx("实例 {0} 已暂停，记下一条（几秒后出现在列表里）", inst), "success");
    setTimeout(reload, 1500);
  });
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("问题备忘")}</h1>
          <p className="page-desc">
            {tx("测试中发现的问题记在这里。")}<b>{tx("游戏窗口里按 Pause/Break")}</b>{tx("（设置页「对局运行 · 问题备忘」可换键）：游戏暂停、打开任务面板（里面是本局对局日志）， 并自动记下实例号、局号和游戏时间点，出现在下面的「待填写」里；再按一次继续。也可以手动新建：选实例、哪一局、哪个时间点，下面会列出那前后的事件。")}
          </p>
        </div>
        <div className="right row" style={{ gap: 8 }}>
          <button className="btn" title={tx("等同在实例的游戏里按一下快捷键：暂停并记一条 / 继续")} onClick={pauseHere}><Pause size={14} />{tx("暂停并记一条（实例 {0}）", inst)}</button>
          <button className="btn primary" onClick={() => pick("new")}><Plus size={14} />{tx("新建")}</button>
        </div>
      </div>
      <div className="grid g-list-detail" style={{ gap: 16, alignItems: "start" }}>
        <Card title={tx("问题（{0}）", list.length)} icon={<NotebookPen size={15} />} bodyClass="card-body tight"
          actions={<Seg value={filter} onChange={setFilter} options={[
            { value: "todo", label: tx("待办 {0}", (counts.draft ?? 0) + (counts.open ?? 0)) }, { value: "draft", label: tx("待填写 {0}", counts.draft ?? 0) },
            { value: "fixed", label: tx("已解决") }, { value: "all", label: tx("全部") }]} />}>
          {!list.length ? <Empty icon={<Keyboard size={28} />} title={tx("没有问题")}>{tx("游戏里按 Pause/Break 就会出现在这里")}</Empty> : (
            <div style={{ maxHeight: "calc(100vh - 220px)", overflow: "auto" }}>
              {list.map((x) => (
                <div key={x.id} className={`issue-row ${sel === x.id ? "on" : ""}`} onClick={() => pick(x.id)}>
                  <div className="row" style={{ gap: 6 }}>
                    <Badge tone={STATUS[x.status]?.tone}>{STATUS[x.status]?.label ?? x.status}</Badge>
                    {x.category && <Badge>{tx(x.category)}</Badge>}
                    <span className="ellipsis" style={{ fontWeight: 600 }}>{x.title || <span className="faint">{tx("（还没写描述）")}</span>}</span>
                  </div>
                  <div className="faint row" style={{ gap: 6, fontSize: 11.5, marginTop: 3 }}>
                    <span title={x.source === "hotkey" ? tx("游戏里按快捷键记的") : tx("网页上新建的")}>{x.source === "hotkey" ? "⌨" : "✎"}</span>
                    <span>{where(x)}</span>
                    <span className="right mono">{when(x.at || x.created)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
        {sel ? <IssueEditor key={sel} issue={cur} defaultInst={inst} onDirty={(d) => { dirty.current = d; }}
            onSaved={(x) => { dirty.current = false; reload(); setSel(x.id); }} onDeleted={() => { dirty.current = false; reload(); setSel(null); }} />
          : <Card><Empty icon={<NotebookPen size={30} />} title={tx("选一条问题，或者新建")}>{tx("右边会显示那个时间点前后的 AI 动作、状态、敌情和日志")}</Empty></Card>}
      </div>
      <Confirm open={pendingSel != null} title={tx("有没保存的修改")} danger confirm={tx("不保存，切换")} onClose={() => setPendingSel(null)}
        body={tx("这条问题改过的内容还没保存，切走就丢了。")}
        onConfirm={() => { dirty.current = false; setSel(pendingSel); }} />
    </div>
  );
}

function IssueEditor({ issue, defaultInst, onSaved, onDeleted, onDirty }: {
  issue: Issue | null; defaultInst: number; onSaved: (x: Issue) => void; onDeleted: () => void; onDirty: (d: boolean) => void;
}) {
  const instances = useApp((s) => s.instances);
  const [title, setTitle] = useState(issue?.title ?? "");
  const [note, setNote] = useState(issue?.note ?? "");
  const [category, setCategory] = useState(issue?.category ?? "");
  const [status, setStatus] = useState<IssueStatus>(issue?.status ?? "open");
  const [inst, setInstNo] = useState<number>(issue?.inst ?? defaultInst);
  const [match, setMatch] = useState<string | null>(issue?.match ?? null);
  const [clock, setClock] = useState(issue?.gameTime != null ? fmtTime(issue.gameTime) : "");
  // 查附近事件用的时间点：输入框解析得出来才更新（打到一半的「3:」不算），清空 = 用那一局最新的
  const [t, setT] = useState<number | null>(issue?.gameTime ?? null);
  const [win, setWin] = useState<number>(issue?.window ?? 30);
  const [del, setDel] = useState(false);
  const { data: matches } = useFetch<MatchSummary[]>(`/api/i/${inst}/matches?limit=40`, 0, [inst]);
  useEffect(() => { if (!match && matches?.length) setMatch(matches[0].name); }, [matches]);   // 新建：默认最新一局
  const editClock = (v: string) => {
    setClock(v);
    if (!v.trim()) setT(null);
    else { const p = parseClock(v); if (p != null) setT(p); }
  };
  const ctxUrl = match ? `/api/i/${inst}/matches/${match}/around?w=${win}${t != null ? `&t=${t}` : ""}` : null;
  const { data: ctx, loading } = useFetch<IssueContext>(ctxUrl, 0, [ctxUrl]);
  useEffect(() => { if (t == null && ctx) { setClock(fmtTime(ctx.t)); setT(ctx.t); } }, [ctx]);   // 没填时间点：用那一局最新的
  const dirty = !issue ? !!(title.trim() || note.trim()) :
    title !== (issue.title ?? "") || note !== (issue.note ?? "") || category !== (issue.category ?? "") || status !== issue.status;
  useEffect(() => { onDirty(dirty); }, [dirty]);
  const clockBad = !!clock.trim() && parseClock(clock) == null;
  const canSave = (!!issue || !!(title.trim() || note.trim())) && !clockBad;   // 新建的至少写一句；已有的（快捷键记的草稿）只改状态也能存
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!canSave || saving) return;
    setSaving(true);
    await action(async () => {
      // 状态没动过就不发：补了描述的「待填写」由后端自动转「待处理」（issues.update）
      const body = { title, note, category, inst, match, gameTime: t, window: win, ...(!issue || status !== issue.status ? { status } : {}) };
      const x = issue ? await api.put<Issue>(`/api/issues/${issue.id}`, body) : await api.post<Issue>("/api/issues", body);
      onSaved(x);
      return x;
    }, tx("已保存"));
    setSaving(false);
  };
  const instNums = [...new Set([...instances.map((i) => i.inst), inst])].sort((a, b) => a - b);
  return (
    <div className="col gap16">
      <Card title={issue ? <span className="row" style={{ gap: 8 }}>{issue.source === "hotkey" ? tx("游戏里记的问题") : tx("问题")}<Badge tone={STATUS[issue.status].tone}>{STATUS[issue.status].label}</Badge></span> : tx("新建问题")}
        actions={<div className="row" style={{ gap: 6 }}>
          {issue && <button className="btn ghost sm" onClick={() => setDel(true)}><Trash2 size={13} />{tx("删除")}</button>}
          <button className="btn primary sm" onClick={save} disabled={!canSave || saving}
            title={!canSave ? (clockBad ? tx("游戏时间点格式不对") : tx("至少写一句描述")) : undefined}>
            {saving ? <span className="spinner sm" /> : <Save size={13} />}{tx("保存")}
          </button>
        </div>}>
        <div className="col gap12">
          {issue?.source === "hotkey" && (
            <div className="faint" style={{ fontSize: 12 }}>
              {tx("{0} 在游戏里按快捷键记下 · {1} pid {2}{3}", when(issue.at), issue.exe ?? tx("游戏"), issue.pid ?? "—", issue.kernel ? tx(" · 内核 #{0}", issue.kernel) : "")}
            </div>
          )}
          <input className="input" placeholder={tx("一句话说是什么问题（例：英雄残血还跟着出击）")} value={title} onChange={(e) => setTitle(e.target.value)} />
          <textarea className="input" rows={4} placeholder={tx("详细描述：看到了什么、期望怎样、怎么复现……")} value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row wrap" style={{ gap: 6 }}>
            {CATEGORIES.map((c) => (
              <button key={c} className={`btn sm ${category === c ? "primary" : ""}`} onClick={() => setCategory(category === c ? "" : c)}>{tx(c)}</button>
            ))}
          </div>
          <div className="row wrap" style={{ gap: 16 }}>
            <Field label={tx("状态")}><Seg value={status} onChange={setStatus} options={(Object.keys(STATUS) as IssueStatus[]).map((k) => ({ value: k, label: STATUS[k].label }))} /></Field>
          </div>
          <div className="row wrap" style={{ gap: 16, alignItems: "flex-end" }}>
            <Field label={tx("实例")}>
              <select className="select" value={inst} onChange={(e) => { setInstNo(Number(e.target.value)); setMatch(null); editClock(""); }}>
                {instNums.map((n) => <option key={n} value={n}>{tx("实例 {0}", n)}</option>)}
              </select>
            </Field>
            <Field label={tx("哪一局")}>
              <select className="select" style={{ maxWidth: 340 }} value={match ?? ""} onChange={(e) => { setMatch(e.target.value); editClock(""); }}>
                {match && !(matches ?? []).some((m) => m.name === match) && <option value={match}>{match}</option>}
                {(matches ?? []).map((m) => (
                  <option key={m.name} value={m.name}>
                    #{String(m.game ?? "").padStart(3, "0")} {m.date?.slice(4, 6)}-{m.date?.slice(6)} {m.time?.slice(0, 2)}:{m.time?.slice(2, 4)} · {m.result ? (OUTCOME[m.result.outcome]?.label ?? m.result.outcome) : m.live ? tx("进行中") : tx("没收尾")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={tx("游戏时间点")}>
              <div className="row" style={{ gap: 6 }}>
                <input className="input mono" style={{ width: 84, ...(clockBad ? { borderColor: "var(--danger)" } : {}) }} placeholder="mm:ss" value={clock}
                  onChange={(e) => editClock(e.target.value)} aria-invalid={clockBad} />
                <button className="btn sm" title={tx("这一局记到的最新时间（进行中的局 = 现在）")} onClick={() => editClock("")}>{tx("最新")}</button>
              </div>
              {clockBad && <span className="danger" style={{ fontSize: 11.5 }}>{tx("写成 分:秒（例 12:30）或秒数")}</span>}
            </Field>
            <Field label={tx("看前后多少秒")}><Seg value={win} onChange={setWin} options={WINDOWS.map((w) => ({ value: w, label: `±${w}` }))} /></Field>
          </div>
        </div>
      </Card>
      <ContextView ctx={ctx} loading={loading} />
      <Confirm open={del} title={tx("删除这条问题")} danger confirm={tx("删除")} body={tx("删掉就没了（游戏里记的标记不会再被收回来）。只是想关掉的话，把状态改成「已解决」或「不是问题」。")}
        onClose={() => setDel(false)} onConfirm={() => issue && action(() => api.del(`/api/issues/${issue.id}`), tx("已删除")).then((r) => r && onDeleted())} />
    </div>
  );
}

function ContextView({ ctx, loading }: { ctx: IssueContext | null; loading: boolean }) {
  if (!ctx) return <Card title={tx("附近事件")}><Empty title={loading ? tx("读取中…") : tx("先选实例和哪一局")} /></Card>;
  const near = ctx.states.reduce<IssueContext["states"][number] | null>((b, s) => (!b || Math.abs(s.t - ctx.t) < Math.abs(b.t - ctx.t) ? s : b), null);
  const foe = ctx.intel.reduce<IssueContext["intel"][number] | null>((b, s) => (!b || Math.abs(s.t - ctx.t) < Math.abs(b.t - ctx.t) ? s : b), null);
  return (
    <>
      <Card title={tx("那一刻（{0}，这一局记到 {1}）", fmtTime(ctx.t), fmtTime(ctx.lastT))}>
        {!near ? <Empty title={tx("这段没有状态记录")} /> : (
          <div className="kv">
            <span>{tx("资源")}</span><span>{tx("金 {0} · 木 {1} · 人口", near.gold, near.lumber)} {near.food}</span>
            <span>{tx("我方")}</span><span>{tx("兵 {0} · 英雄 {1} · 工人", near.army, near.heroes)} {near.workers}</span>
            <span>{tx("强度")}</span><span>{tx("我")} <b className="success">{near.ownStr ?? "—"}</b> {tx("/ 敌")} <b className="danger">{near.foeStr ?? "—"}</b></span>
            <span>{tx("出击 / 守家")}</span><span>{ATTACK_STATE[near.attack ?? ""] ?? near.attack ?? "—"} · {DEFENSE_MODE[near.defense ?? ""] ?? near.defense ?? "—"}</span>
            {near.why && Object.keys(near.why).length > 0 && <><span>{tx("出击层理由")}</span><span className="muted">{Object.entries(near.why).map(([k, v]) => `${k}×${v}`).join("，")}</span></>}
            {foe && <><span>{tx("看到的敌军")}</span><span className="muted">{foe.army} {tx("个")}{foe.composition ? `：${foe.composition}` : ""}</span></>}
          </div>
        )}
      </Card>
      <Card title={tx("AI 动作（{0} ~ {1}，{2} 条）", fmtTime(ctx.t - ctx.window), fmtTime(ctx.t + ctx.window), ctx.actions.length)} bodyClass="">
        {!ctx.actions.length ? <Empty title={tx("这段时间 AI 没记动作")} /> : (
          <div className="timeline" style={{ maxHeight: 420, overflow: "auto" }}>
            {ctx.actions.map((a, i) => (
              <div key={i} className="tl-item" style={Math.abs(a.t - ctx.t) <= 2 ? { background: "var(--accent-softer)" } : undefined}>
                <span className="tl-t">{fmtTime(a.t)}</span><span>{a.text}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
      {Object.entries(ctx.logs).map(([key, lg]) => (
        <Card key={key} title={tx("{0}日志（{1} 行）", lg.label, lg.lines.length)} bodyClass="">
          <div className="timeline" style={{ maxHeight: 320, overflow: "auto" }}>
            {lg.lines.map((l, i) => (
              <div key={i} className="tl-item"><span className="tl-t">{new Date(l.wall * 1000).toLocaleTimeString(locale, { hour12: false })}</span><span className="muted" style={{ fontSize: 12 }}>{l.text}</span></div>
            ))}
          </div>
        </Card>
      ))}
    </>
  );
}
