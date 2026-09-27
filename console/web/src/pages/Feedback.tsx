// 反馈与建议（09-26）：遇到问题、有想法，直接从远见发给 War3AI.com。
// 后端 console/server/feedback.py：POST /api/feedback/preview、POST /api/feedback、GET /api/feedback/history、POST /api/feedback/retry/{id}。
// 每次提交由闭源运行时签名、附带本机识别码（加盐哈希，反推不出系统里的真实编号），服务端验签、防重放、限流（server/feedback/app.py）。
import { useState } from "react";
import { Bug, CircleCheck, CircleX, Clock, Eye, Lightbulb, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { Badge, Card, Dialog, Empty, Seg, useFetch } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { toast } from "../lib/store";
import { lang, locale, tx } from "../lib/i18n";

type Kind = "issue" | "suggestion";
interface HistoryRow { id: string; at: number; kind: Kind; title: string; ok: boolean; ref?: string | null; error?: string | null; pending?: boolean; diag?: boolean }

const CATS: Record<Kind, { value: string; label: string }[]> = {
  issue: [
    { value: "deploy", label: tx("安装 / 启动") }, { value: "game", label: tx("游戏 / 实例") }, { value: "ai", label: tx("AI / 方案") },
    { value: "console", label: tx("远见界面") }, { value: "speech", label: tx("气泡 / 大模型") }, { value: "other", label: tx("其它") },
  ],
  suggestion: [
    { value: "feature", label: tx("新功能") }, { value: "improve", label: tx("改进") }, { value: "docs", label: tx("文档") }, { value: "other", label: tx("其它") },
  ],
};

/** 服务端 / 本机返回的错误码 -> 给人看的一句话 */
function errorText(code: string | null | undefined): string {
  switch (code) {
    case "offline": return tx("连不上 War3AI.com：已经存在本机，网络好了在下面的记录里点「重试」。");
    case "busy": case "http-500": case "http-502": case "http-503": case "http-504": return tx("服务器正忙：已经存在本机，稍后在下面的记录里点「重试」。");
    case "http-404": case "http-405": return tx("War3AI.com 的反馈服务还没开通：已经存在本机，开通后在下面的记录里点「重试」。");
    case "runtime-missing": case "runtime-old": return tx("运行时的命令行工具不在或太旧（它负责给提交签名）：双击 start.bat 同步运行时后再试。");
    case "too-fast": return tx("提交得太快了，15 秒后再试。");
    case "too-many": return tx("这一小时提交得太多了，晚点再来。");
    case "rate-limited": return tx("服务器限流了（同一台电脑每小时最多 6 条），晚点再试。");
    case "stale": case "bad-signature": return tx("签名没通过：检查一下电脑的时间是否准确，或者双击 start.bat 更新运行时。");
    case "replay": return tx("这一条已经提交过了。");
    case "bad-body": case "bad-headers": return tx("内容格式不对（字数超了？）。");
    case "too-large": case "diag-too-large": return tx("内容太长了：删掉一些，或者不带诊断信息。");
    default: return tx("提交失败：{0}", code ?? "?");
  }
}

function fieldError(msg: string): string {
  if (msg === "empty") return tx("标题和内容都要写。");
  if (msg.startsWith("too-long")) return tx("字数超了：标题最多 120 字，内容最多 8000 字，联系方式最多 120 字。");
  return msg;
}

export default function Feedback() {
  const [kind, setKind] = useState<Kind>("issue");
  const [category, setCategory] = useState("other");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [contact, setContact] = useState("");
  const [diag, setDiag] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const hist = useFetch<{ rows: HistoryRow[]; pending: number; endpoint: string }>("/api/feedback/history", 0);

  const switchKind = (k: Kind) => { setKind(k); setCategory("other"); setDiag(k === "issue"); };
  const body = () => ({ kind, category, title, text, contact, diag, lang });
  const wrap = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try { await fn(); } catch (e) { toast(e instanceof ApiError && e.status === 422 ? fieldError(e.message) : (e as Error).message, "error", 7000); }
    setBusy(null);
  };
  const doPreview = () => wrap("preview", async () => setPreview(await api.post("/api/feedback/preview", body())));
  const submit = () => wrap("submit", async () => {
    const r = await api.post<{ ok: boolean; ref?: string; error?: string }>("/api/feedback", body());
    if (r.ok) {
      toast(tx("已提交，编号 {0}。谢谢！", r.ref ?? ""), "success", 7000);
      setTitle(""); setText("");
    } else {
      toast(errorText(r.error), (r as { record?: { pending?: boolean } }).record?.pending ? "info" : "error", 9000);
    }
    hist.reload();
  });
  const retry = (id: string) => wrap("retry" + id, async () => {
    const r = await api.post<{ ok: boolean; ref?: string; error?: string }>(`/api/feedback/retry/${id}`);
    toast(r.ok ? tx("已提交，编号 {0}。谢谢！", r.ref ?? "") : errorText(r.error), r.ok ? "success" : "error", 7000);
    hist.reload();
  });

  const ready = title.trim() && text.trim();
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("反馈与建议")}</h1>
          <p className="page-desc">{tx("遇到问题、有想法，都可以直接告诉我们：提交会发到 War3AI.com，开发者会看到。")}</p>
        </div>
      </div>

      <div className="grid g-main-side" style={{ alignItems: "start" }}>
        <Card title={kind === "issue" ? tx("报告问题") : tx("提交建议")} icon={kind === "issue" ? <Bug size={15} /> : <Lightbulb size={15} />}
          actions={<Seg value={kind} onChange={switchKind} options={[
            { value: "issue", label: tx("报告问题"), icon: <Bug size={13} /> },
            { value: "suggestion", label: tx("提交建议"), icon: <Lightbulb size={13} /> },
          ]} />}>
          <div className="col gap12">
            <div className="row wrap" style={{ gap: 8 }}>
              <label className="sp-field"><span>{tx("类别")}</span>
                <select className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
                  {CATS[kind].map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </label>
              <input className="input grow" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)}
                placeholder={kind === "issue" ? tx("一句话说清楚问题，比如：点开始测试后游戏没进局") : tx("一句话说清楚想要什么")} />
            </div>
            <textarea className="input" rows={9} maxLength={8000} value={text} onChange={(e) => setText(e.target.value)}
              placeholder={kind === "issue" ? tx("发生了什么？怎么能再现？本来期望是什么？有报错就把报错贴上。") : tx("想要什么功能 / 改进？在什么场景下用？为什么？")} />
            <div className="faint" style={{ fontSize: 11.5, textAlign: "right" }}>{text.length} / 8000</div>
            <input className="input" maxLength={120} value={contact} onChange={(e) => setContact(e.target.value)}
              placeholder={tx("联系方式（邮箱 / QQ，可不填；填了方便我们回复你）")} />
            <label className="sp-check">
              <input type="checkbox" checked={diag} onChange={(e) => setDiag(e.target.checked)} />
              {tx("附带诊断信息：环境自检结果、各服务状态、实例概况、远见和运行时日志的最后几十行（路径里的用户名会替换掉）")}
            </label>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn" disabled={!!busy || !ready} onClick={doPreview}>{busy === "preview" ? <span className="spinner sm" /> : <Eye size={14} />}{tx("预览要发送的内容")}</button>
              <button className="btn primary" disabled={!!busy || !ready} onClick={submit}>{busy === "submit" ? <span className="spinner sm" /> : <Send size={14} />}{tx("提交")}</button>
            </div>
          </div>
        </Card>

        <Card title={tx("会发送什么")} icon={<ShieldCheck size={15} />}>
          <div className="col gap8" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
            <div>{tx("会发送：你写的标题、内容、联系方式；远见、运行时、游戏和 Windows 的版本号；勾选时附带的诊断信息。")}</div>
            <div>{tx("本机识别码：由运行时从 Windows 算出来的一串哈希，反推不出你电脑的真实编号，只用来防止被刷、把同一台电脑的反馈归在一起。")}</div>
            <div>{tx("每次提交都由运行时签名，War3AI.com 只收签名对的；同一台电脑每小时最多 6 条。")}</div>
            <div className="faint">{tx("不会发送：存档、录像、地图、账号密码、你电脑上的其它文件。")}</div>
            {hist.data?.endpoint && <div className="faint mono" style={{ fontSize: 11.5 }}>{hist.data.endpoint}</div>}
          </div>
        </Card>
      </div>

      <Card title={tx("提交记录（这台电脑）")} icon={<Clock size={15} />} style={{ marginTop: 14 }} bodyClass="card-body tight"
        actions={<button className="btn sm ghost" onClick={hist.reload}><RefreshCw size={13} />{tx("刷新")}</button>}>
        {!hist.data?.rows.length ? <Empty title={tx("还没有提交过")} /> : (
          <table className="table">
            <thead><tr><th>{tx("时间")}</th><th>{tx("类型")}</th><th>{tx("标题")}</th><th>{tx("状态")}</th><th /></tr></thead>
            <tbody>
              {hist.data.rows.map((r) => (
                <tr key={r.id}>
                  <td className="mono faint">{new Date(r.at * 1000).toLocaleString(locale)}</td>
                  <td><Badge tone={r.kind === "issue" ? "warning" : "info"}>{r.kind === "issue" ? tx("问题") : tx("建议")}</Badge></td>
                  <td className="ellipsis" style={{ maxWidth: 420 }}>{r.title}</td>
                  <td>{r.ok ? <span className="success row" style={{ gap: 4 }}><CircleCheck size={13} />{tx("已送达 {0}", r.ref ?? "")}</span>
                    : r.pending ? <span className="warning row" style={{ gap: 4 }}><Clock size={13} />{tx("没发出去，待重试")}</span>
                      : <span className="danger row" style={{ gap: 4 }} title={errorText(r.error)}><CircleX size={13} />{tx("失败")}</span>}</td>
                  <td className="num">{r.pending && <button className="btn sm" disabled={!!busy} onClick={() => retry(r.id)}>
                    {busy === "retry" + r.id ? <span className="spinner sm" /> : <RefreshCw size={13} />}{tx("重试")}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Dialog open={!!preview} title={tx("将要发送的内容")} onClose={() => setPreview(null)} wide
        footer={<>
          <span className="faint" style={{ marginRight: "auto", fontSize: 12 }}>{preview ? tx("约 {0} KB", Math.max(1, Math.round(preview.bytes / 1024))) : ""}</span>
          <button className="btn" onClick={() => setPreview(null)}>{tx("关闭")}</button>
          <button className="btn primary" disabled={!!busy} onClick={() => { setPreview(null); submit(); }}><Send size={14} />{tx("提交")}</button>
        </>}>
        {preview && (
          <div className="col gap8">
            <div className="faint" style={{ fontSize: 12.5 }}>
              {tx("发往：")}<span className="mono">{preview.endpoint}</span> · {tx("本机识别码：")}<span className="mono">{preview.machine ?? tx("（取不到：{0}）", preview.error ?? "?")}</span>
            </div>
            <pre className="svc-log mono">{JSON.stringify(preview.payload, null, 2)}</pre>
          </div>
        )}
      </Dialog>
    </div>
  );
}
