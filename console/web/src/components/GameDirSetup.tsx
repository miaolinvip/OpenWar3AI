// 魔兽争霸 III 目录（09-26）：玩家自己设 —— 新装后控制中心最上面出这张卡片，「设置」页也有一份（以后换目录）。
// 帮着找候选（注册表 + 各盘常见目录）、弹系统的选文件夹窗口、保存前后都读 Game.dll 的版本；
// 版本不是运行时支持的（现在只有 1.27a）就给温馨提示：下载这个版本，更多版本即将支持，关注 War3AI.com。
// 后端 console/server/gamedir.py：GET /api/gamedir、/api/gamedir/detect、/api/gamedir/check，POST /api/gamedir、/browse、/extract。
import { useEffect, useState } from "react";
import { CircleCheck, CircleX, ExternalLink, FolderOpen, Gamepad2, Info, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { Badge, Card } from "./ui";
import { api } from "../lib/api";
import { toast } from "../lib/store";
import { tx } from "../lib/i18n";

export interface DirCheck { path: string; exists: boolean; hasExe: boolean; hasDll: boolean; version: string | null; supported: boolean; ok: boolean; maps: boolean }
export interface GameDirStatus extends DirCheck {
  configured: boolean; want: string[]; wantLabel: string; website: string; mapsDir: string; dataReady: boolean;
  job: { running: boolean; ok: boolean | null; log: string[]; startedAt: number | null; endedAt: number | null };
}

/** 版本不对时的温馨提示（控制中心、设置页、环境自检共用这段话） */
export function VersionTip({ version, wantLabel, website }: { version: string | null; wantLabel: string; website: string }) {
  return (
    <div className="gd-tip">
      <TriangleAlert size={16} className="warning" style={{ flex: "none", marginTop: 2 }} />
      <div>
        <b>{tx("温馨提示：检测到的游戏版本是 {0}，目前只支持 {1}。", version ?? "?", wantLabel)}</b>
        <div>{tx("请下载 {0} 版本的《魔兽争霸 III：冰封王座》，装好后在这里重新选择目录。", wantLabel)}</div>
        <div>{tx("我们即将更新、支持更多游戏版本，请关注")} <a href={website} target="_blank" rel="noreferrer">War3AI.com <ExternalLink size={11} /></a></div>
      </div>
    </div>
  );
}

function CheckLine({ c }: { c: DirCheck }) {
  if (!c.path) return null;
  if (!c.exists) return <div className="gd-line danger"><CircleX size={14} />{tx("这个目录不存在")}</div>;
  if (!c.ok) return <div className="gd-line danger"><CircleX size={14} />{tx("这里面没有 War3.exe 和 Game.dll：选魔兽争霸 III 的安装目录")}</div>;
  return (
    <div className={`gd-line ${c.supported ? "success" : "warning"}`}>
      {c.supported ? <CircleCheck size={14} /> : <TriangleAlert size={14} />}
      {tx("游戏版本 {0}", c.version ?? "?")} · {c.supported ? tx("支持") : tx("暂不支持")}
      {!c.maps && <span className="faint"> · {tx("没有 Maps 文件夹")}</span>}
    </div>
  );
}

export default function GameDirSetup({ status, onChanged, title }: { status: GameDirStatus | null; onChanged: () => void; title?: string }) {
  const [path, setPath] = useState("");
  const [check, setCheck] = useState<DirCheck | null>(null);
  const [cands, setCands] = useState<DirCheck[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { if (status?.path && !path) setPath(status.path); }, [status?.path]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 输入框改了：停一下再检查（不用点按钮就能看到版本对不对）
  useEffect(() => {
    if (!path.trim()) { setCheck(null); return; }
    const id = setTimeout(async () => {
      try { setCheck(await api.get<DirCheck>(`/api/gamedir/check?path=${encodeURIComponent(path.trim())}`)); } catch { setCheck(null); }
    }, 350);
    return () => clearTimeout(id);
  }, [path]);

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try { await fn(); } catch (e) { toast((e as Error).message, "error", 6000); }
    setBusy(null);
  };
  const detect = () => run("detect", async () => {
    const r = await api.get<{ candidates: DirCheck[] }>("/api/gamedir/detect");
    setCands(r.candidates);
    if (r.candidates.length && !path.trim()) setPath(r.candidates[0].path);
  });
  const browse = () => run("browse", async () => {
    toast(tx("选择文件夹的窗口已经弹出（可能在浏览器后面）"), "info", 4000);
    const r = await api.post<{ path: string | null }>("/api/gamedir/browse");
    if (r.path) setPath(r.path);
  });
  const save = (p = path) => run("save", async () => {
    const r = await api.post<{ ok: boolean; code?: string; check: DirCheck; extracting?: boolean }>("/api/gamedir", { path: p.trim() });
    if (!r.ok) { toast(r.code === "not-found" ? tx("这个目录不存在") : tx("这里面没有 War3.exe 和 Game.dll：选魔兽争霸 III 的安装目录"), "error", 6000); return; }
    setPath(r.check.path);
    toast(r.check.supported ? tx("游戏目录已保存") : tx("已保存，但这个游戏版本暂不支持：看下面的提示"), r.check.supported ? "success" : "info", 5000);
    if (r.extracting) toast(tx("正在从你的游戏里提取数据（一两分钟）"), "info", 5000);
    onChanged();
  });
  const extract = () => run("extract", async () => {
    const r = await api.post<{ started: boolean; error?: string }>("/api/gamedir/extract");
    if (r.error) toast(r.error, "error");
    onChanged();
  });

  const cur = status;
  const job = cur?.job;
  const dirty = !!path.trim() && path.trim() !== (cur?.path ?? "");
  return (
    <Card title={title ?? tx("魔兽争霸 III 目录")} icon={<Gamepad2 size={15} />}
      actions={cur?.configured
        ? (cur.supported ? <Badge tone="success">{tx("已设置 · {0}", cur.version ?? "")}</Badge> : cur.ok ? <Badge tone="warning">{tx("版本暂不支持")}</Badge> : <Badge tone="danger">{tx("目录不对")}</Badge>)
        : <Badge tone="warning">{tx("还没设置")}</Badge>}>
      <div className="col gap12">
        <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
          {tx("选你电脑上魔兽争霸 III 的安装目录（里面有 War3.exe 和 Game.dll）。地图（目录下的 Maps）、开局用的游戏都从这里找；运行时支持 {0}。", cur?.wantLabel ?? "1.27a")}
        </div>
        <div className="row wrap" style={{ gap: 6 }}>
          <input className="input grow mono" style={{ minWidth: 260 }} value={path} onChange={(e) => setPath(e.target.value)}
            placeholder={tx("例如 D:\\Games\\Warcraft III")} onKeyDown={(e) => e.key === "Enter" && dirty && save()} />
          <button className="btn" disabled={!!busy} onClick={browse}>{busy === "browse" ? <span className="spinner sm" /> : <FolderOpen size={14} />}{tx("浏览…")}</button>
          <button className="btn" disabled={!!busy} onClick={detect}>{busy === "detect" ? <span className="spinner sm" /> : <Search size={14} />}{tx("自动查找")}</button>
          <button className="btn primary" disabled={!!busy || !path.trim() || (!!check && !check.ok) || !dirty && !!cur?.configured} onClick={() => save()}>
            {busy === "save" ? <span className="spinner sm" /> : <CircleCheck size={14} />}{tx("保存")}
          </button>
        </div>
        {check && <CheckLine c={check} />}
        {cands && (
          cands.length ? (
            <div className="gd-cands">
              <div className="faint" style={{ fontSize: 12 }}>{tx("找到这些（点一个填进上面，再点保存）：")}</div>
              {cands.map((c) => (
                <button key={c.path} className={`gd-cand ${c.path === path ? "on" : ""}`} onClick={() => setPath(c.path)}>
                  <span className="mono grow ellipsis">{c.path}</span>
                  <Badge tone={c.supported ? "success" : "warning"}>{c.version ?? "?"}{c.supported ? "" : tx("（暂不支持）")}</Badge>
                </button>
              ))}
            </div>
          ) : <div className="faint" style={{ fontSize: 12.5 }}>{tx("没自动找到。点「浏览…」自己选，或者直接把路径粘贴到上面。")}</div>
        )}
        {cur?.configured && cur.ok && !cur.supported && <VersionTip version={cur.version} wantLabel={cur.wantLabel} website={cur.website} />}
        {check && check.ok && !check.supported && (!cur || check.path !== cur.path) && <VersionTip version={check.version} wantLabel={cur?.wantLabel ?? "1.27a"} website={cur?.website ?? "https://war3ai.com"} />}
        {cur?.configured && cur.ok && (
          <div className="gd-data">
            <Info size={14} className="faint" style={{ flex: "none" }} />
            <span className="grow">
              {job?.running ? tx("正在从你的游戏里提取数据…") : cur.dataReady ? tx("游戏数据已提取（单位、技能、物品的名字和数值）") : job?.ok === false ? tx("游戏数据提取失败：看下面的日志") : tx("游戏数据还没提取")}
              <span className="faint mono"> · {tx("地图目录：{0}", cur.mapsDir)}</span>
            </span>
            {!job?.running && <button className="btn sm ghost" onClick={extract}>{busy === "extract" ? <span className="spinner sm" /> : <RefreshCw size={13} />}{cur.dataReady ? tx("重新提取") : tx("提取")}</button>}
          </div>
        )}
        {job && (job.running || job.ok === false) && job.log.length > 0 && <pre className="svc-log mono" style={{ maxHeight: 180 }}>{job.log.slice(-40).join("\n")}</pre>}
      </div>
    </Card>
  );
}
