import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Braces, Eraser, Play, Search, TerminalSquare } from "lucide-react";
import { Badge, Card, Empty, useFetch } from "../components/ui";
import { action, api } from "../lib/api";
import { navigate, toast, useApp } from "../lib/store";
import { isZh, lang, locale, tx } from "../lib/i18n";

// 09-24 目标：把 JASS 的各类能力做成导出接口，让使用者能从外部随时调用，对游戏做进一步的自定义。
// 这一页 = 对着正在跑的实例敲 JASS：直接粘 JASS 写法（call / set / local），变量一直记着；右边查 1291 个 native。
// 后端 console/server/jassview.py；同样的能力在命令行（python -m openwar3 jass）和 HTTP（本页底部）。

// zh / en 是这条 native 的中英文说明（sdk/python/openwar3/jass_docs.json，1291 条每条都有）；desc = 当前语言那一种
interface NativeRow { name: string; signature: string; params: string[]; paramNames: string[]; returns: string; category: string; categoryName: string; readonly: boolean; verified: boolean; zh: string; en: string; desc: string }
interface NativesResp { categories: { key: string; name: string; count: number }[]; natives: NativeRow[]; verified: number; total: number }
interface RunRow { line: number; code: string; value: unknown }
interface RunResp { ok: boolean; pid?: number; rows?: RunRow[]; printed?: string[]; vars?: Record<string, unknown>; error?: string; line?: number }
interface LogEntry { at: number; code: string; resp: RunResp }

// 示例脚本里有游戏内显示的中文（面板标题、对白、按钮）和注释：英文界面下按这张表换成英文
const EXAMPLE_EN: [string, string][] = [
  ["暴击！", "Crit!"], ["// 删掉：", "// remove: "], ["// 去掉：", "// remove: "], ["// 恢复：", "// restore: "],
  ["我的面板", "My panel"], ["心情", "Mood"], ["开心", "Happy"], ["Boss 刷新", "Boss respawn"], ["\"小圣\"", "\"Buddy\""],
  ["主人，前面那片好像有宝箱！", "Master, I think there's a chest up ahead!"], ["接下来做什么？", "What next?"],
  ["跟着我", "Follow me"], ["原地守着", "Hold here"], ["// 点完以后再跑：", "// after clicking, run: "],
  ["—— 哪个变成 1 就是点了哪个", "- whichever becomes 1 is the one clicked"],
  ["// 在游戏里打 -follow 回车，再跑：", "// type -follow + Enter in game, then run: "], ["造出来的句柄", "created handle"],
];
const localizeExample = (code: string) => (isZh ? code : EXAMPLE_EN.reduce((c, [zh, en]) => c.split(zh).join(en), code));

const EXAMPLES: { name: string; code: string }[] = [
  { name: tx("浮动文字"), code: 'set h = hero()\nset t = CreateTextTag()\ncall SetTextTagText(t, "|cffffcc00+128 暴击！|r", 0.024)\ncall SetTextTagPosUnit(t, h, 60)\ncall SetTextTagVelocity(t, 0, 0.03)\ncall SetTextTagPermanent(t, false)\ncall SetTextTagLifespan(t, 4)\ncall SetTextTagFadepoint(t, 3)\ncall SetTextTagVisibility(t, true)' },
  { name: tx("范围圈"), code: 'set h = hero()\nset img = CreateImage("ReplaceableTextures\\\\Selection\\\\SpellAreaOfEffect.blp", 1200, 1200, 0, h.x - 600, h.y - 600, 0, 0, 0, 0, 2)\ncall SetImageRenderAlways(img, true)\ncall SetImageColor(img, 255, 80, 80, 200)\ncall ShowImage(img, true)\n// 删掉：call DestroyImage(img)' },
  { name: tx("连线"), code: 'set h = hero()\nset l = AddLightningEx("SPLK", true, h.x, h.y, 60, h.x + 500, h.y + 300, 60)\n// 删掉：call DestroyLightning(l)' },
  { name: tx("头顶特效"), code: 'set h = hero()\nset e = AddSpecialEffectTarget("Abilities\\\\Spells\\\\Other\\\\TalkToMe\\\\TalkToMe.mdl", h, "overhead")\n// 删掉：call DestroyEffect(e)' },
  { name: tx("面板"), code: 'set mb = CreateMultiboard()\ncall MultiboardSetRowCount(mb, 2)\ncall MultiboardSetColumnCount(mb, 2)\ncall MultiboardSetTitleText(mb, "我的面板")\ncall MultiboardSetItemsStyle(mb, true, false)\ncall MultiboardSetItemsWidth(mb, 0.07)\nset it = MultiboardGetItem(mb, 0, 0)\ncall MultiboardSetItemValue(it, "心情")\ncall MultiboardReleaseItem(it)\nset it = MultiboardGetItem(mb, 0, 1)\ncall MultiboardSetItemValue(it, "开心")\ncall MultiboardReleaseItem(it)\ncall MultiboardDisplay(mb, true)' },
  { name: tx("倒计时"), code: 'set tm = CreateTimer()\ncall TimerStart(tm, 60, false, null)\nset td = CreateTimerDialog(tm)\ncall TimerDialogSetTitle(td, "Boss 刷新")\ncall TimerDialogDisplay(td, true)' },
  { name: tx("头像对白"), code: 'call ForceCinematicSubtitles(true)\ncall SetCinematicScene(\'Hpal\', ConvertPlayerColor(1), "小圣", "主人，前面那片好像有宝箱！", 6, 5)' },
  { name: tx("屏幕泛红"), code: 'call SetCineFilterTexture("ReplaceableTextures\\\\CameraMasks\\\\DreamFilter_Mask.blp")\ncall SetCineFilterBlendMode(ConvertBlendMode(2))\ncall SetCineFilterTexMapFlags(ConvertTexMapFlags(0))\ncall SetCineFilterStartUV(0, 0, 1, 1)\ncall SetCineFilterEndUV(0, 0, 1, 1)\ncall SetCineFilterStartColor(255, 0, 0, 0)\ncall SetCineFilterEndColor(255, 0, 0, 150)\ncall SetCineFilterDuration(0.8)\ncall DisplayCineFilter(true)\n// 去掉：call DisplayCineFilter(false)' },
  { name: tx("小地图闪点"), code: 'set h = hero()\ncall PingMinimapEx(h.x, h.y, 4, 255, 0, 0, false)' },
  { name: tx("单位变色放大"), code: 'set h = hero()\ncall SetUnitVertexColor(h, 255, 120, 120, 255)\ncall SetUnitScale(h, 1.5, 1.5, 1.5)\ncall SetUnitAnimation(h, "victory")\n// 恢复：call SetUnitVertexColor(h, 255, 255, 255, 255) / call SetUnitScale(h, 1, 1, 1)' },
  { name: tx("按钮菜单"), code: 'set d = DialogCreate()\ncall DialogSetMessage(d, "接下来做什么？")\nset b1 = DialogAddButton(d, "跟着我", 0)\nset b2 = DialogAddButton(d, "原地守着", 0)\nset t1 = CreateTrigger()\ncall TriggerRegisterDialogButtonEvent(t1, b1)\nset t2 = CreateTrigger()\ncall TriggerRegisterDialogButtonEvent(t2, b2)\ncall DialogDisplay(GetLocalPlayer(), d, true)\n// 点完以后再跑：GetTriggerExecCount(t1)  GetTriggerExecCount(t2)  —— 哪个变成 1 就是点了哪个' },
  { name: tx("聊天命令"), code: 'set tc = CreateTrigger()\ncall TriggerRegisterPlayerChatEvent(tc, GetLocalPlayer(), "-follow", true)\n// 在游戏里打 -follow 回车，再跑：GetTriggerExecCount(tc)' },
  { name: tx("造单位"), code: 'set h = hero()\nset u = CreateUnit(Player(0), \'hfoo\', h.x + 200, h.y, 270)\nprint("造出来的句柄", u)\n// 删掉：call RemoveUnit(u)' },
].map((e) => ({ ...e, code: localizeExample(e.code) }));

const TYPE: Record<string, string> = { I: "integer", R: "real", B: "boolean", S: "string", C: "code", V: "nothing" };
const typeName = (p: string) => (p.startsWith("H") ? p.slice(1) : TYPE[p] ?? p);

/** 带参数名的签名：(player id, integer unitid, real x, real y, real face) → unit */
function prettyArgs(n: NativeRow): string {
  const args = n.params.map((p, i) => `${typeName(p)}${n.paramNames[i] ? " " + n.paramNames[i] : ""}`).join(", ");
  return `(${args || "nothing"}) → ${typeName(n.returns)}`;
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function JassConsole() {
  const inst = useApp((s) => s.inst);
  const [code, setCode] = useState(EXAMPLES[0].code);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [vars, setVars] = useState<Record<string, unknown>>({});
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  // 搜索框停手 250 ms 再查（以前每敲一个字请求一次）
  const [qd, setQd] = useState("");
  useEffect(() => { const id = setTimeout(() => setQd(q), 250); return () => clearTimeout(id); }, [q]);
  const { data } = useFetch<NativesResp>(`/api/jass/natives?q=${encodeURIComponent(qd)}&cat=${cat}&lang=${lang}`, 0, [qd, cat]);

  const runCode = async (text: string) => {
    setBusy(true);
    try {
      const r = await api.post<RunResp>(`/api/instances/${inst}/jass`, { code: text });
      setLog((l) => [{ at: Date.now(), code: text, resp: r }, ...l].slice(0, 50));
      if (r.vars) setVars(r.vars);
      if (!r.ok) toast(r.error ?? tx("没跑成"), "error", 6000);
    } catch (e: any) {
      toast(String(e?.message ?? e), "error", 6000);
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    const r = await action(() => api.post(`/api/instances/${inst}/jass/reset`, {}), tx("变量清空了"));
    if (r) setVars({});
  };
  const insert = (n: NativeRow) => {
    const el = ref.current;
    const call = `call ${n.name}(${n.params.map((p) => (p.startsWith("H") ? p.slice(1) : { I: "0", R: "0.0", B: "false", S: '""', C: "null" }[p] ?? p)).join(", ")})`;
    if (!el) { setCode((c) => `${c}\n${call}`); return; }
    const pos = el.selectionStart ?? code.length;
    const next = code.slice(0, pos) + (pos && code[pos - 1] !== "\n" ? "\n" : "") + call + code.slice(pos);
    setCode(next);
  };
  const catCount = useMemo(() => (data?.categories ?? []).filter((c) => c.count > 0), [data]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("JASS 控制台 · 实例 {0}", inst)}</h1>
          <p className="page-desc">
            {tx("对着正在跑的游戏直接调 JASS：地图作者能用的 {0} 个 native 都能调（造单位、画特效、面板、对话框、镜头、声音……）， 直接粘 JASS 写法（call / set / local），变量一直记着。✔ 是实机调过、看过效果的（{1} 个）。 ⚠ 会改游戏世界：只在单人局用；多人局只放行只读的。", data?.total ?? 1291, data?.verified ?? "…")}
          </p>
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 460px), 1fr))", gap: 16 }}>
        <div className="col gap12">
          <Card title={tx("脚本")} icon={<Braces size={15} />} actions={
            <span className="row" style={{ gap: 6 }}>
              <select className="input sm" value="" onChange={(e) => { const ex = EXAMPLES.find((x) => x.name === e.target.value); if (ex) setCode(ex.code); }}>
                <option value="">{tx("示例…")}</option>
                {EXAMPLES.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
              </select>
              <button className="btn sm ghost" onClick={reset} title={tx("清掉记着的变量")}><Eraser size={13} />{tx("清变量")}</button>
              <button className="btn sm primary" disabled={busy} onClick={() => runCode(code)} title="Ctrl+Enter"><Play size={13} />{busy ? tx("运行中…") : tx("运行")}</button>
            </span>}>
            <textarea ref={ref} className="input mono" spellCheck={false} style={{ width: "100%", minHeight: 260, fontSize: 12.5, lineHeight: 1.55 }}
              value={code} onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runCode(code); } }} />
            <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>
              {tx("内置：hero() 我方主英雄 · me() 本机玩家 · unit('hfoo'[, 玩家号]) · unit_at(x, y) · wait(秒) · print(...)；单位能读 .x .y .hp .type .owner .level。 不支持 if / loop / function —— 要写逻辑用 Python 的 g.jass（docs/JASS_API_ZH.md）。")}
            </div>
          </Card>
          <Card title={tx("结果")} icon={<Play size={15} />}>
            {log.length === 0 ? <Empty title={tx("还没跑过")}>{tx("左边写好点「运行」（Ctrl+Enter）")}</Empty> : (
              <div className="col gap12" style={{ maxHeight: 360, overflow: "auto" }}>
                {log.map((e) => (
                  <div key={e.at} style={{ borderLeft: `3px solid var(--${e.resp.ok ? "success" : "danger"})`, paddingLeft: 10 }}>
                    <div className="faint" style={{ fontSize: 11 }}>{new Date(e.at).toLocaleTimeString(locale, { hour12: false })}{e.resp.pid ? ` · pid ${e.resp.pid}` : ""}</div>
                    {(e.resp.rows ?? []).filter((r) => r.value !== null && r.value !== undefined).map((r) => (
                      <div key={r.line} className="mono" style={{ fontSize: 12 }}><span className="faint">{r.line}</span> {r.code} <b>= {show(r.value)}</b></div>
                    ))}
                    {(e.resp.printed ?? []).map((p, i) => <div key={i} className="mono" style={{ fontSize: 12 }}>» {p}</div>)}
                    {e.resp.ok ? <div className="faint" style={{ fontSize: 12 }}>{tx("✔ {0} 句", (e.resp.rows ?? []).length)}</div>
                      : <div style={{ color: "var(--danger)", fontSize: 12.5 }}>✘ {e.resp.error}</div>}
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title={tx("变量")} icon={<Braces size={15} />}>
            {Object.keys(vars).length === 0 ? <div className="faint" style={{ fontSize: 12 }}>{tx("set / local 出来的变量会记在这个实例上（换局清空）")}</div> : (
              <table className="table"><tbody>
                {Object.entries(vars).map(([k, v]) => <tr key={k}><td className="mono">{k}</td><td className="mono faint" style={{ fontSize: 12 }}>{show(v)}</td></tr>)}
              </tbody></table>
            )}
          </Card>
        </div>
        <div className="col gap12">
          <Card title={tx("查 native")} icon={<BookOpen size={15} />}>
            <div className="search" style={{ marginBottom: 8 }}>
              <Search size={14} />
              <input className="input" placeholder={tx("名字或说明，比如 TextTag / 漂浮文字 / 镜头")} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="row wrap" style={{ gap: 4, marginBottom: 8 }}>
              <button className={`btn sm ${cat === "" ? "primary" : "ghost"}`} onClick={() => setCat("")}>{tx("全部")}</button>
              {catCount.map((c) => (
                <button key={c.key} className={`btn sm ${cat === c.key ? "primary" : "ghost"}`} onClick={() => setCat(c.key)} title={c.name}>
                  {c.name.split(/（| \(/)[0]} <span className="faint">{c.count}</span>
                </button>
              ))}
            </div>
            <div style={{ maxHeight: 620, overflow: "auto" }}>
              <table className="table">
                <tbody>
                  {(data?.natives ?? []).slice(0, 400).map((n) => (
                    <tr key={n.name} style={{ cursor: "pointer" }} onClick={() => insert(n)}
                        title={`${n.name}${prettyArgs(n)}\n${isZh ? n.zh : n.en}\n${isZh ? n.en : n.zh}\n\n${tx("点一下插进脚本")}`}>
                      <td style={{ fontSize: 12 }}>
                        <div className="mono">
                          {n.verified && <Badge tone="success">✔</Badge>} {n.readonly && <Badge>{tx("读")}</Badge>} <b style={{ fontWeight: 600 }}>{n.name}</b>
                          <span className="faint" style={{ fontSize: 11 }}>{prettyArgs(n)}</span>
                        </div>
                        <div className="muted" style={{ fontSize: 12, marginTop: 2, lineHeight: 1.45 }}>{n.desc || (isZh ? n.zh : n.en)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(data?.natives.length ?? 0) > 400 && <div className="faint" style={{ fontSize: 12 }}>{tx("只列前 400 个，搜索或选分类缩小范围")}</div>}
            </div>
          </Card>
          <Card title={tx("从外部程序调（HTTP，只听本机）")} icon={<TerminalSquare size={15} />}>
            <pre className="mono" style={{ fontSize: 11.5, whiteSpace: "pre-wrap", margin: 0 }}>{tx("# 跑一段脚本（直接粘 JASS）\nPOST /api/instances/{0}/jass\n{\"code\": \"set h = hero()\\ncall PingMinimapEx(h.x, h.y, 3, 255, 0, 0, false)\"}\n\n# 调一个 native（单位参数写 {\"unit\": 地址}）\nPOST /api/instances/{1}/jass/call\n{\"name\": \"SetUnitScale\", \"args\": [{\"unit\": 12345678}, 1.5, 1.5, 1.5]}\n\n# 查 native\nGET /api/jass/natives?q=TextTag&cat=visual\n\n# 命令行\npython -m openwar3 jass --inst {2}          (交互)\npython -m openwar3 jass --inst {3} -e \"...\"", inst, inst, inst, inst)}</pre>
            <div className="row wrap" style={{ gap: 8, marginTop: 10, fontSize: 12.5 }}>
              <span className="muted">{tx("别的语言走网关（方法名 jass.<函数名>），大模型用 MCP 的 war3_jass 工具。")}</span>
              <button className="btn sm" onClick={() => navigate("connect")}>{tx("接入与扩展")}</button>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
