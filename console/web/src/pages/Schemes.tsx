import { useMemo, useRef, useState } from "react";
import { Bot, Brain, CloudUpload, Copy, Download, FolderOpen, Package, Play, Puzzle, ShieldAlert, ShieldCheck, Trash2, Trophy, Upload } from "lucide-react";
import { Badge, Card, Confirm, Dialog, Field, FetchState, Seg, useFetch } from "../components/ui";
import { ApiError, api, action } from "../lib/api";
import { toast, useApp } from "../lib/store";
import { fmtTime } from "../lib/game";
import type { Scheme, SchemeResult, SchemesOverview } from "../lib/types";
import { locale, localizeSchemes, tx } from "../lib/i18n";

// 09-24 目标：别人设计好的 AI 方案可以下载到本地，不影响自己的方案，随时加载来跑测试、对照研究，
//               在远见里直接切换。方案 = 文件夹 + scheme.json，打成 zip 分享（以后方案网站上传用同一个包）。
const SOURCE_LABEL: Record<Scheme["source"], string> = { mine: tx("我的方案"), installed: tx("已安装（别人分享的）"), builtin: tx("内置") };
const SOURCE_DESC: Record<Scheme["source"], string> = {
  mine: tx("自己写的或从别的方案复制来改的，可以随便改代码（点「打开文件夹」）。改完下一局就生效。"),
  installed: tx("从别人那导入的 zip。只读、和你自己的方案完全分开；想改就「复制到我的」。第一次运行前要确认信任 —— 方案是代码，权限和你本人一样。"),
  builtin: tx("随 OpenWar3 带的：参考大脑（完整 AI）、教学示例和玩法模组示例。只读，可以复制到我的再改。"),
};

export default function Schemes() {
  const { data: raw, reload, error } = useFetch<SchemesOverview>("/api/schemes", 5000);
  const data = useMemo(() => localizeSchemes(raw), [raw]);
  const [apply, setApply] = useState<Scheme | null>(null);
  const [forkOf, setForkOf] = useState<Scheme | null>(null);
  const [del, setDel] = useState<Scheme | null>(null);
  const [trust, setTrust] = useState<Scheme | null>(null);
  const [replace, setReplace] = useState<{ name: string; data: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const groups = useMemo(() => {
    const g: Record<string, Scheme[]> = { mine: [], installed: [], builtin: [] };
    for (const s of data?.schemes ?? []) g[s.source].push(s);
    return g;
  }, [data]);

  const doImport = async (name: string, b64: string, replaceIt = false) => {
    try {
      const r = await api.post<{ scheme: Scheme }>("/api/schemes/import", { filename: name, data: b64, replace: replaceIt });
      toast(tx("已导入「{0}」v{1}（作者 {2}）—— 运行前要先确认信任", r.scheme.name, r.scheme.version, r.scheme.author), "success", 6000);
      reload();
    } catch (e) {
      // 409 = 同一个 id 已经装过（后端 SchemeExists）：问要不要替换
      if (e instanceof ApiError && e.status === 409) setReplace({ name, data: b64 });
      else toast(tx("导入失败：{0}", (e as Error).message), "error", 8000);
    }
  };
  const onFile = (f: File | undefined) => {
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      const url = String(rd.result ?? "");
      doImport(f.name, url.slice(url.indexOf(",") + 1));
    };
    rd.readAsDataURL(f);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("AI 方案")}</h1>
          <p className="page-desc">{tx("每个方案是一套完整的 AI（一个文件夹 + scheme.json）。给每个实例选一个方案，一键切换；导入别人分享的 zip 跑测试、学习， 和自己的方案互不影响。战绩按方案自动统计。")}</p>
        </div>
        <div className="right row" style={{ gap: 8 }}>
          <input ref={fileRef} type="file" accept=".zip" style={{ display: "none" }} onChange={(e) => onFile(e.target.files?.[0])} />
          <button className="btn primary" onClick={() => fileRef.current?.click()}><Upload size={14} />{tx("导入方案（zip）")}</button>
          <button className="btn" disabled title={tx("以后：一键把你的方案分享到方案网站、浏览下载全球玩家的方案（包格式已经定好，就是「导出」出来的 zip）")}>
            <CloudUpload size={14} />{tx("分享到方案网站（即将支持）")}</button>
        </div>
      </div>

      {!data ? <FetchState error={error} onRetry={reload} title={tx("读取方案中…")} /> : (
        <div className="col gap16">
          {(["mine", "installed", "builtin"] as const).map((src) => (
            <Card key={src} title={SOURCE_LABEL[src]} icon={src === "builtin" ? <Brain size={15} /> : <Package size={15} />}
              actions={<span className="faint" style={{ fontSize: 12 }}>{src !== "builtin" ? data.dirs[src] : ""}</span>}>
              <div className="faint" style={{ fontSize: 12.5, marginBottom: 12 }}>{SOURCE_DESC[src]}</div>
              {groups[src].length === 0 ? (
                <div className="faint" style={{ fontSize: 13 }}>
                  {src === "mine" ? tx("还没有自己的方案：在下面「内置」里挑一个示例点「复制到我的」开始改。") : tx("还没导入过别人的方案：点右上角「导入方案」选一个 zip。")}
                </div>
              ) : (
                <div className="col gap12">
                  {groups[src].map((s) => (
                    <SchemeRow key={s.ref} s={s} data={data} onApply={setApply} onFork={setForkOf} onDelete={setDel} onTrust={setTrust} />
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      <ApplyDialog scheme={apply} data={data} onClose={() => setApply(null)} onDone={reload} />
      <ForkDialog scheme={forkOf} onClose={() => setForkOf(null)} onDone={reload} />
      <Confirm open={!!del} title={del ? tx("删除方案「{0}」", del.name) : ""} danger confirm={tx("删除")} onClose={() => setDel(null)}
        body={del?.source === "mine" ? tx("会删掉这个文件夹里你自己的代码（建议先「导出」备份）。") : tx("只删这个导入的副本，不影响你自己的方案。")}
        onConfirm={() => del && action(() => api.del(`/api/schemes?ref=${encodeURIComponent(del.ref)}`), tx("已删除")).then(reload)} />
      <Confirm open={!!trust} title={trust ? tx("信任「{0}」？", trust.name) : ""} confirm={tx("我信任它，允许运行")} onClose={() => setTrust(null)}
        body={<>
          <p>{tx("这是 {0} 写的代码（入口 {1}），运行时和你本人的权限一样：能读写你的文件、联网。", trust?.author ?? "?", trust?.entry ?? "?")}</p>
          <p>{tx("只信任你认识的作者或者读过代码的方案。可以先点「打开文件夹」看看代码。")}</p>
        </>}
        onConfirm={() => trust && action(() => api.post("/api/schemes/trust", { ref: trust.ref }), tx("已信任，可以运行了")).then(reload)} />
      <Confirm open={!!replace} title={tx("已经装过同一个方案")} confirm={tx("替换成这个版本")} onClose={() => setReplace(null)}
        body={tx("替换会覆盖之前导入的那一份（你自己的方案不受影响），替换后需要重新确认信任。")}
        onConfirm={() => replace && doImport(replace.name, replace.data, true)} />
    </div>
  );
}

function SchemeRow({ s, data, onApply, onFork, onDelete, onTrust }: {
  s: Scheme; data: SchemesOverview; onApply: (s: Scheme) => void; onFork: (s: Scheme) => void; onDelete: (s: Scheme) => void; onTrust: (s: Scheme) => void;
}) {
  const [open, setOpen] = useState(false);
  const st = data.stats[s.ref];
  const users = data.instances.filter((i) => (i.running ?? i.scheme) === s.ref);
  const rate = st && st.wins + st.losses ? Math.round((100 * st.wins) / (st.wins + st.losses)) : null;
  return (
    <div className="card" style={{ padding: 14, background: "var(--bg-sunken)" }}>
      <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
        <div className="grow">
          <div className="row wrap" style={{ gap: 8 }}>
            {s.kind === "brain" ? <Brain size={15} /> : s.kind === "mod" ? <Puzzle size={15} /> : <Bot size={15} />}
            <b style={{ fontSize: 14.5 }}>{s.name}</b>
            {s.version && <Badge>v{s.version}</Badge>}
            {s.author && <span className="faint" style={{ fontSize: 12 }}>{tx("作者")} {s.author}</span>}
            {s.kind === "brain" ? <Badge tone="accent">{tx("完整 AI（自带监督器）")}</Badge>
              : s.kind === "mod" ? <Badge tone="info" title={tx("不是 AI，是一套规则：你自己在游戏窗口里玩，模组出题（刷怪、奖励、屏幕上的按钮）。只能单人局（docs/MODS_ZH.md）")}>{tx("玩法模组")}</Badge>
              : <Badge>Bot</Badge>}
            {s.fair && <Badge tone="info" title={tx("只看得见视野里的东西（没有迷雾作弊）")}>{tx("公平模式")}</Badge>}
            {s.judge === false && s.kind !== "mod" && <Badge tone="accent" title={tx("给 RPG / 自定义地图用：不按对战规则判胜负。记得在实例的「下一局设置」里选一张 RPG 地图，你自己在游戏窗口里玩（docs/COMPANION_ZH.md）")}>{tx("RPG / 玩伴")}</Badge>}
            {!s.trusted && <Badge tone="warning"><ShieldAlert size={11} />{tx("未信任")}</Badge>}
            {s.error && <Badge tone="danger" title={s.error}>{tx("坏了：")}{s.error}</Badge>}
            {users.map((u) => <Badge key={u.inst} tone={u.running === s.ref ? "success" : ""} title={u.running === s.ref ? tx("正在这个实例上跑") : tx("这个实例选了它（下次开始测试生效）")}>
              {tx("实例")} {u.inst}{u.running === s.ref ? tx(" · 在跑") : tx(" · 已选")}</Badge>)}
          </div>
          {s.description && <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>{s.description}</div>}
          <div className="faint mono" style={{ fontSize: 11, marginTop: 4 }}>{s.ref}{s.entry ? ` · ${s.entry}${s.class ? `:${s.class}` : ""}` : ""}{s.forked_from ? tx(" · 复制自 {0}", s.forked_from) : ""}</div>
        </div>
        <div style={{ textAlign: "right", minWidth: 150 }}>
          {st && st.games ? (
            <>
              <div style={{ fontSize: 18, fontWeight: 700 }}>{rate == null ? "—" : `${rate}%`}<span className="faint" style={{ fontSize: 12, fontWeight: 400 }}> {tx("胜率")}</span></div>
              <div className="faint" style={{ fontSize: 12 }}>{st.games} {tx("局 · {0} 胜 {1} 负{2}", st.wins, st.losses, st.unknown ? tx(" · {0} 未定", st.unknown) : "")}
                {st.midGame ? <span title={tx("切换时这局已经在打，是半路接手的：不算进胜率")}> {tx("· {0} 半路接手", st.midGame)}</span> : null}</div>
              <div className="faint" style={{ fontSize: 12 }}>{tx("平均 {0} 游戏分钟", st.avgMinutes)}</div>
            </>
          ) : <div className="faint" style={{ fontSize: 12 }}>{tx("还没跑过")}</div>}
        </div>
      </div>
      <div className="row wrap" style={{ gap: 6, marginTop: 10 }}>
        <button className="btn sm primary" disabled={!!s.error} onClick={() => (s.trusted ? onApply(s) : onTrust(s))}><Play size={13} />{s.trusted ? tx("用到实例…") : tx("确认信任后使用")}</button>
        {s.kind !== "brain" && <button className="btn sm" onClick={() => onFork(s)}><Copy size={13} />{tx("复制到我的")}</button>}
        {s.kind !== "brain" && <a className="btn sm" href={`/api/schemes/export?ref=${encodeURIComponent(s.ref)}`}><Download size={13} />{tx("导出 zip")}</a>}
        {s.source !== "builtin" && <button className="btn sm" onClick={() => action(() => api.post("/api/schemes/open-folder", { ref: s.ref }))}><FolderOpen size={13} />{tx("打开文件夹")}</button>}
        {s.source === "installed" && !s.trusted && <button className="btn sm" onClick={() => onTrust(s)}><ShieldCheck size={13} />{tx("信任")}</button>}
        {s.kind !== "brain" && st?.games ? <button className="btn sm ghost" onClick={() => setOpen(!open)}><Trophy size={13} />{open ? tx("收起战绩") : tx("最近战绩")}</button> : null}
        {s.source !== "builtin" && <button className="btn sm ghost danger right" onClick={() => onDelete(s)}><Trash2 size={13} />{tx("删除")}</button>}
      </div>
      {open && <Results refName={s.ref} />}
    </div>
  );
}

function Results({ refName }: { refName: string }) {
  const { data, error, reload } = useFetch<{ rows: SchemeResult[] }>(`/api/schemes/results?ref=${encodeURIComponent(refName)}`, 0, [refName]);
  if (!data) return <FetchState error={error} onRetry={reload} />;
  if (!data.rows.length) return <div className="faint" style={{ fontSize: 12.5, marginTop: 10 }}>{tx("还没有记下来的战绩")}</div>;
  return (
    <table className="table" style={{ marginTop: 10 }}>
      <thead><tr><th>{tx("结束时间")}</th><th>{tx("实例")}</th><th>{tx("版本")}</th><th>{tx("结果")}</th><th>{tx("游戏时长")}</th><th>{tx("原因")}</th></tr></thead>
      <tbody>
        {data.rows.map((r) => (
          <tr key={`${r.inst}-${r.pid}-${r.end}`}>
            <td className="nowrap">{new Date(r.end * 1000).toLocaleString(locale, { hour12: false })}</td>
            <td>{r.inst}</td>
            <td className="mono">{r.version}</td>
            <td className="nowrap">{r.result === "win" ? <Badge tone="success">{tx("胜")}</Badge> : r.result === "loss" ? <Badge tone="danger">{tx("负")}</Badge> : <Badge>{tx("未定")}</Badge>}
              {r.midGame && <Badge title={tx("切换时这局已经在打：不算进胜率")}>{tx("半路接手")}</Badge>}</td>
            <td>{r.gameMinutes != null ? fmtTime(r.gameMinutes * 60) : "—"}</td>
            <td className="faint" style={{ fontSize: 12 }}>{r.reason}{r.errors ? tx(" · 出错 {0} 次", r.errors) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ApplyDialog({ scheme, data, onClose, onDone }: { scheme: Scheme | null; data: SchemesOverview | null; onClose: () => void; onDone: () => void }) {
  const instances = useApp((s) => s.instances);
  const cur = useApp((s) => s.inst);
  const [inst, setInst] = useState<number | null>(null);
  const [now, setNow] = useState<"now" | "next">("now");
  const [busy, setBusy] = useState(false);
  const nums = useMemo(() => [...new Set([...instances.map((i) => i.inst), ...(data?.instances ?? []).map((i) => i.inst), cur])].sort((a, b) => a - b), [instances, data, cur]);
  const target = inst ?? cur;
  const st = instances.find((i) => i.inst === target);
  const running = !!st && (st.game.alive || st.farm.length > 0);
  if (!scheme) return null;
  const go = async () => {
    setBusy(true);
    const r = await action(() => api.post<any>(`/api/instances/${target}/scheme`, { ref: scheme.ref, now: now === "now" }));
    setBusy(false);
    if (!r) return;
    if (!r.ok) { toast(r.error ?? r.start?.error ?? tx("没切成"), "error", 8000); return; }
    toast(r.applied === "now" ? tx("实例 {0} 已切到「{1}」，新 AI 接管当前这一局", target, scheme.name) : tx("实例 {0} 下次开始测试用「{1}」", target, scheme.name), "success", 6000);
    onDone();
    onClose();
  };
  return (
    <Dialog open title={tx("把「{0}」用到实例", scheme.name)} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{tx("取消")}</button><button className="btn primary" disabled={busy} onClick={go}><Play size={14} />{busy ? tx("切换中…") : tx("切换")}</button></>}>
      <div className="col gap12">
        <Field label={tx("实例")}>
          <div className="row wrap" style={{ gap: 6 }}>
            {nums.map((n) => {
              const s = instances.find((i) => i.inst === n);
              const on = !!s && (s.game.alive || s.farm.length > 0);
              return <button key={n} className={`btn sm ${target === n ? "primary" : ""}`} onClick={() => setInst(n)} title={s?.schemeRunning ? tx("在跑 {0}", s.schemeRunning) : ""}>
                {n}{on && <span className="dot success" style={{ marginLeft: 4 }} />}</button>;
            })}
          </div>
        </Field>
        <Field label={tx("什么时候生效")}>
          <Seg value={now} onChange={setNow} options={[{ value: "now", label: tx("立刻（接管当前这一局）") }, { value: "next", label: tx("下次开始测试") }]} />
        </Field>
        <div className="faint" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
          {running
            ? now === "now" ? tx("实例 {0} 正在跑{1}：会停掉当前 AI，新方案马上接管这一局（游戏不重开）；之后每一局都用新方案。", target, st?.schemeRunning ? `「${st.schemeRunning}」` : "")
              : tx("只记下来，当前这局照旧；下次开始测试 / AI 重起时换成新方案。")
            : tx("实例 {0} 没在跑：记下来，点「开始测试」时就用这个方案。", target)}
        </div>
      </div>
    </Dialog>
  );
}

function ForkDialog({ scheme, onClose, onDone }: { scheme: Scheme | null; onClose: () => void; onDone: () => void }) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  if (!scheme) return null;
  const valid = /^[a-z0-9][a-z0-9_-]{1,40}$/.test(id);
  const go = async () => {
    setBusy(true);
    const r = await action(() => api.post<any>("/api/schemes/fork", { ref: scheme.ref, id, name: name || undefined }), tx("已复制到「我的方案」：mine/{0}", id));
    setBusy(false);
    if (r) { onDone(); onClose(); setId(""); setName(""); }
  };
  return (
    <Dialog open title={tx("复制「{0}」到我的方案", scheme.name)} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>{tx("取消")}</button><button className="btn primary" disabled={!valid || busy} onClick={go}>{busy ? <span className="spinner sm" /> : <Copy size={14} />}{tx("复制")}</button></>}>
      <div className="col gap12">
        <Field label={tx("新方案的 id")} desc={id && !valid ? <span className="danger">{tx("id 不合法：只能用小写字母、数字、-、_，以字母或数字开头，2~41 个字符")}</span> : tx("小写字母、数字、-、_，2~41 个字符；以后导出分享时就是这个名字")}>
          <input className="input" value={id} placeholder="my-rush" onChange={(e) => setId(e.target.value.toLowerCase())}
            onKeyDown={(e) => { if (e.key === "Enter" && valid && !busy) go(); }} />
        </Field>
        <Field label={tx("显示名（可选）")}><input className="input" value={name} placeholder={tx("{0}（我的副本）", scheme.name)} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="faint" style={{ fontSize: 12.5 }}>{tx("复制出来的方案在 schemes/mine/{0}/，原方案不动。点「打开文件夹」改代码，改完下一局就生效。", id || "<id>")}</div>
      </div>
    </Dialog>
  );
}
