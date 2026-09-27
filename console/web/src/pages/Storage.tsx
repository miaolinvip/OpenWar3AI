// 数据与磁盘（09-26）：远见和底座一直在往盘上写 —— 战斗录制、对局记录、决策明细、日志……这一页看清占了多少、在哪、
// 最近 24 小时涨了多少、所在磁盘还剩多少、按这个速度还能撑几天。后端 GET /api/disk（console/server/disk.py），只读不删。
// 09-26 目标：把各类数据和日志的磁盘占用集中展示，一眼看清整套系统占了多少盘。
import { useState } from "react";
import { FolderOpen, HardDrive, RefreshCw } from "lucide-react";
import { Badge, Card, Empty, FetchState, Stat, useFetch } from "../components/ui";
import { api, action } from "../lib/api";
import type { DiskUsage } from "../lib/types";
import { locale, tx } from "../lib/i18n";

/** 每一类：名字、颜色、是什么、能不能删 */
const CATS: Record<string, { label: string; color: string; desc: string; del: string }> = {
  fights: { label: tx("战斗录制"), color: "#ef4444", desc: tx("毫秒层把每场战斗一帧一行录下来（inst*/fights/fight-*.jsonl），训练战斗模型、复盘用。录制没有上限，通常是最大的一块。"), del: tx("不训练战斗模型的话，旧的可以删") },
  matches: { label: tx("对局记录"), color: "#3b82f6", desc: tx("每局一个文件（inst*/matches/）：对局记录页、胜率统计、调参都靠它。"), del: tx("删了，对局记录页和胜率统计里就没有这些局了") },
  decisions: { label: tx("战斗决策明细"), color: "#f59e0b", desc: tx("「战斗决策」页的数据（inst*/decisions.jsonl）：每个单位每次决策的依据和耗时。"), del: tx("可以删，只影响「战斗决策」页能往回看多远") },
  logs: { label: tx("日志"), color: "#a78bfa", desc: tx("游戏运行时、大脑、毫秒层、气泡服务的日志（*.log / *.out）。新版运行时日志超过 32 MB 会自动换新文件。"), del: tx("旧日志可以删；正在跑的实例的日志删不掉也别删") },
  director: { label: tx("导播录制"), color: "#14b8a6", desc: tx("导播台的镜头记录（inst*/director/、director/director/）。"), del: tx("可以删") },
  streams: { label: tx("其它运行数据"), color: "#64748b", desc: tx("侦察记录、每拍耗时等 *.jsonl。"), del: tx("可以删，排查问题时才用得上") },
  state: { label: tx("实例状态文件"), color: "#94a3b8", desc: tx("大脑和毫秒层之间交换的小文件（单位表、预留、撤退名单……）。"), del: tx("别删，正在跑的实例要用") },
  farm: { label: tx("批量对局输出"), color: "#0ea5e9", desc: tx("批量开局脚本（runtime/farm.py）的输出。"), del: tx("可以删") },
  perf: { label: tx("性能采样"), color: "#84cc16", desc: tx("性能分析时录的采样数据（bin/perf）。"), del: tx("可以删") },
  backup: { label: tx("备份"), color: "#78716c", desc: tx("改代码前自动留的备份（bin/backup）。"), del: tx("确认不用回滚了就可以删") },
  program: { label: tx("程序文件"), color: "#6366f1", desc: tx("编出来的运行时 DLL、启动器等（bin/ 根目录）。"), del: tx("别删，删了要重新编译") },
  cache: { label: tx("远见缓存"), color: "#ec4899", desc: tx("地图预览图、对局摘要索引等（console/cache）。"), del: tx("可以删，会自动重建（对局记录第一次会慢几秒）") },
  tuning: { label: tx("调参数据"), color: "#d946ef", desc: tx("参考大脑自动调参的记录。"), del: tx("调参实验做完可以删") },
  schemes: { label: tx("AI 方案"), color: "#22c55e", desc: tx("你写的和导入的方案（schemes/）。"), del: tx("在「AI 方案」页删，别直接删文件") },
  gamedata: { label: tx("游戏数据"), color: "#eab308", desc: tx("从本机游戏里提取的单位、技能数据（data/game）。"), del: tx("别删，删了要重新提取") },
  other: { label: tx("其它"), color: "#9ca3af", desc: tx("没归进上面几类的文件。"), del: "" },
};
const cat = (k: string) => CATS[k] ?? { label: k, color: "#9ca3af", desc: "", del: "" };
const INST_COLS = ["fights", "matches", "decisions", "logs"];

export function fmtBytes(b: number | null | undefined): string {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  const u = ["KB", "MB", "GB", "TB"];
  let v = b / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
}

function ago(ts: number) {
  if (!ts) return "—";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return tx("刚刚");
  if (s < 3600) return tx("{0} 分钟前", Math.round(s / 60));
  if (s < 86400 * 2) return tx("{0} 小时前", Math.round(s / 3600));
  return tx("{0} 天前", Math.round(s / 86400));
}

function openIn(path: string) {
  action(() => api.post("/api/disk/open", { path }));
}

export default function Storage() {
  const [bump, setBump] = useState(0);
  const { data, error, reload } = useFetch<DiskUsage>(bump ? `/api/disk?refresh=1&n=${bump}` : "/api/disk", 60000, []);
  if (!data) return <div className="page"><PageHead onRefresh={() => setBump((x) => x + 1)} /><FetchState error={error} onRetry={reload} /></div>;
  const tot = data.total.bytes || 1;
  const drive = data.drive;
  const freePct = drive ? drive.free / drive.total : 1;
  const days = drive && data.total.growth24h > 0 ? drive.free / data.total.growth24h : null;
  const tight = freePct < 0.1 || (days != null && days < 14);
  const top = data.categories[0];
  return (
    <div className="page">
      <PageHead onRefresh={() => setBump((x) => x + 1)} data={data} />

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Stat label={tx("数据一共占")} icon={<HardDrive size={14} />} value={fmtBytes(data.total.bytes)}
          sub={tx("{0} 个文件", data.total.files.toLocaleString(locale))} />
        <Stat label={tx("最近 24 小时新增")} value={<span className={data.total.growth24h > 5 * 1024 ** 3 ? "warning" : ""}>{fmtBytes(data.total.growth24h)}</span>}
          sub={days != null ? tx("照这个速度，磁盘剩余空间还够 {0} 天", days >= 365 ? "365+" : Math.floor(days)) : tx("最近一天没写什么")} />
        <Stat label={tx("所在磁盘 {0} 剩余", drive?.path ?? "")} value={<span className={tight ? "danger" : "success"}>{drive ? fmtBytes(drive.free) : "—"}</span>}
          sub={drive ? tx("共 {0}，已用 {1}%", fmtBytes(drive.total), Math.round((drive.used / drive.total) * 100)) : ""} />
        <Stat label={tx("最大的一类")} value={top ? cat(top.key).label : "—"} sub={top ? tx("{0}，占 {1}%", fmtBytes(top.bytes), Math.round((top.bytes / tot) * 100)) : ""} />
      </div>

      {tight && (
        <div className="card" style={{ marginBottom: 14, padding: "12px 16px", borderColor: "var(--danger)", background: "var(--danger-soft)" }}>
          <b className="danger">{tx("磁盘空间紧张")}</b>{" "}
          <span>{days != null && days < 14
            ? tx("按最近 24 小时的写入速度，大约 {0} 天后写满。先看下面「按类别」里标了「可以删」的那几类。", Math.max(0, Math.floor(days)))
            : tx("所在磁盘剩余不到 10%。先看下面「按类别」里标了「可以删」的那几类。")}</span>
        </div>
      )}

      <Card title={tx("按类别")} style={{ marginBottom: 14 }}
        actions={<span className="faint" style={{ fontSize: 12 }}>{tx("远见不会自动删任何东西")}</span>}>
        <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", background: "var(--bg-sunken)", marginBottom: 14 }}>
          {data.categories.filter((c) => c.bytes / tot >= 0.002).map((c) => (
            <div key={c.key} title={`${cat(c.key).label} ${fmtBytes(c.bytes)}`} style={{ width: `${(c.bytes / tot) * 100}%`, background: cat(c.key).color }} />
          ))}
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>{tx("类别")}</th><th className="num">{tx("大小")}</th><th className="num">{tx("占比")}</th><th className="num">{tx("文件数")}</th><th className="num">{tx("24 小时新增")}</th><th>{tx("是什么 · 能不能删")}</th></tr></thead>
            <tbody>
              {data.categories.map((c) => {
                const m = cat(c.key);
                return (
                  <tr key={c.key}>
                    <td className="nowrap"><i style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: m.color, marginRight: 8, verticalAlign: "-1px" }} /><b>{m.label}</b></td>
                    <td className="num nowrap"><b>{fmtBytes(c.bytes)}</b></td>
                    <td className="num">{((c.bytes / tot) * 100).toFixed(c.bytes / tot < 0.01 ? 1 : 0)}%</td>
                    <td className="num">{c.files.toLocaleString(locale)}</td>
                    <td className="num nowrap">{c.growth24h ? fmtBytes(c.growth24h) : <span className="faint">—</span>}</td>
                    <td style={{ fontSize: 12.5, lineHeight: 1.55, minWidth: 280 }}>
                      <span className="muted">{m.desc}</span>
                      {m.del && <div className="faint">{m.del}</div>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={tx("按实例")} style={{ marginBottom: 14 }} bodyClass="card-body tight"
        actions={<span className="faint" style={{ fontSize: 12 }}>{tx("bin/inst<N>：每个实例自己的录制、对局、日志")}</span>}>
        {!data.instances.length ? <Empty title={tx("还没有实例数据")} /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr>
                <th>{tx("实例")}</th><th className="num">{tx("合计")}</th>
                {INST_COLS.map((k) => <th key={k} className="num">{cat(k).label}</th>)}
                <th className="num">{tx("其它")}</th><th className="num">{tx("24 小时新增")}</th><th>{tx("最后写入")}</th><th />
              </tr></thead>
              <tbody>
                {data.instances.map((it) => {
                  const rest = it.bytes - INST_COLS.reduce((s, k) => s + (it.cats[k] ?? 0), 0);
                  return (
                    <tr key={it.inst}>
                      <td className="nowrap"><b>{tx("实例 {0}", it.inst)}</b></td>
                      <td className="num nowrap"><b>{fmtBytes(it.bytes)}</b></td>
                      {INST_COLS.map((k) => <td key={k} className="num nowrap">{it.cats[k] ? fmtBytes(it.cats[k]) : <span className="faint">—</span>}</td>)}
                      <td className="num nowrap">{fmtBytes(rest)}</td>
                      <td className="num nowrap">{it.growth24h ? fmtBytes(it.growth24h) : <span className="faint">—</span>}</td>
                      <td className="nowrap faint">{ago(it.newest)}</td>
                      <td><button className="btn ghost sm" title={tx("在资源管理器里打开")} onClick={() => openIn(`bin/inst${it.inst}`)}><FolderOpen size={13} /></button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="grid g2" style={{ alignItems: "start" }}>
        <Card title={tx("按目录")} bodyClass="card-body tight">
          <table className="table">
            <thead><tr><th>{tx("目录")}</th><th className="num">{tx("大小")}</th><th className="num">{tx("文件数")}</th><th>{tx("最后写入")}</th><th /></tr></thead>
            <tbody>
              {data.dirs.slice(0, 30).map((d) => (
                <tr key={d.path}>
                  <td className="mono" style={{ fontSize: 12 }}>{d.path}</td>
                  <td className="num nowrap"><b>{fmtBytes(d.bytes)}</b></td>
                  <td className="num">{d.files.toLocaleString(locale)}</td>
                  <td className="nowrap faint">{ago(d.newest)}</td>
                  <td><button className="btn ghost sm" title={tx("在资源管理器里打开")} onClick={() => openIn(d.path)}><FolderOpen size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title={tx("最大的 {0} 个文件", data.largest.length)} bodyClass="card-body tight">
          <table className="table">
            <thead><tr><th>{tx("文件")}</th><th className="num">{tx("大小")}</th><th>{tx("类别")}</th><th>{tx("最后写入")}</th><th /></tr></thead>
            <tbody>
              {data.largest.map((f) => (
                <tr key={f.path}>
                  <td className="mono" style={{ fontSize: 12, maxWidth: 0, width: "100%" }} title={f.path}><div className="ellipsis">{f.path}</div></td>
                  <td className="num nowrap"><b>{fmtBytes(f.bytes)}</b></td>
                  <td className="nowrap"><Badge>{cat(f.key).label}</Badge></td>
                  <td className="nowrap faint">{ago(f.mtime)}</td>
                  <td><button className="btn ghost sm" title={tx("在资源管理器里打开")} onClick={() => openIn(f.path)}><FolderOpen size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

function PageHead({ onRefresh, data }: { onRefresh: () => void; data?: DiskUsage }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{tx("数据与磁盘")}</h1>
        <p className="page-desc">
          {tx("远见和底座写在盘上的东西：战斗录制、对局记录、决策明细、日志…… 各占多少、在哪、最近涨了多少。只统计，不删任何东西。")}
          {data && <span className="faint">{" "}{tx("（{0} 统计，用了 {1} 毫秒；每分钟自动更新）", new Date(data.scannedAt * 1000).toLocaleTimeString(locale, { hour12: false }), data.tookMs)}</span>}
        </p>
      </div>
      <div className="right row"><button className="btn sm" onClick={onRefresh}><RefreshCw size={13} />{tx("重新统计")}</button></div>
    </div>
  );
}
