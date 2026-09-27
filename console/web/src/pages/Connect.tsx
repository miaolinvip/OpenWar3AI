// 接入与扩展（09-25）：把游戏接到别的程序和大模型上 —— 网关（任何语言 / 浏览器）、MCP（Claude Code 等）、Python、HTTP。
// 后端 GET /api/connect、POST /api/connect/gateway/start（console/server/connectview.py）。
import { useMemo, useState } from "react";
import { Bot, Copy, ExternalLink, Globe, Plug, Puzzle, RefreshCw, TerminalSquare } from "lucide-react";
import { Badge, BusyButton, Card, FetchState, Seg, useFetch } from "../components/ui";
import { action, api } from "../lib/api";
import { navigate, toast, useApp } from "../lib/store";
import { tx } from "../lib/i18n";

interface Info {
  gateway: { port: number; running: boolean; ws: string; demo: string; http: string; script: string; log: string; exists: boolean };
  mcp: { python: string; script: string; exists: boolean; tools: { name: string; desc: string; who: "all" | "draw" | "dev" }[] };
  root: string;
}
type Role = "dev" | "player" | "observer";

function copy(text: string) {
  navigator.clipboard.writeText(text).then(() => toast(tx("已复制"), "success", 1500), () => toast(tx("复制失败：浏览器不让写剪贴板"), "error"));
}

function CodeBox({ code }: { code: string }) {
  return (
    <div className="codebox">
      <pre className="mono">{code}</pre>
      <button className="btn ghost icon sm" title={tx("复制")} onClick={() => copy(code)}><Copy size={14} /></button>
    </div>
  );
}

const ROLE_DESC: Record<Role, string> = {
  dev: tx("开发：全部能力，包括 JASS 改世界（本机工具、模组、玩伴）"),
  player: tx("选手：只能指挥一个玩家的单位，只看得见它的视野（公平模式）"),
  observer: tx("观众：只读（观战、解说、数据采集）"),
};

export default function Connect() {
  const inst = useApp((s) => s.inst);
  const { data, error, reload } = useFetch<Info>("/api/connect", 5000);
  const [role, setRole] = useState<Role>("dev");
  const [player, setPlayer] = useState(0);

  const q = (x: string) => (/\s/.test(x) ? `"${x}"` : x);
  const roleArgs = role === "dev" ? "" : role === "player" ? ` --role player --player ${player}` : " --role observer";
  const wsUrl = data ? `${data.gateway.ws}?inst=${inst}&role=${role}${role === "player" ? `&player=${player}` : ""}` : "";
  const mcpCmd = data ? `claude mcp add war3 -- ${q(data.mcp.python)} ${q(data.mcp.script)} --inst ${inst}${roleArgs}` : "";
  const mcpJson = useMemo(() => data ? JSON.stringify({
    mcpServers: { war3: { command: data.mcp.python, args: [data.mcp.script, "--inst", String(inst), ...roleArgs.trim().split(/\s+/).filter(Boolean)] } },
  }, null, 2) : "", [data, inst, roleArgs]);
  const jsCode = `import { OpenWar3, unit } from "./openwar3.mjs";   // gateway/clients/js/openwar3.mjs

const ow = new OpenWar3("${data?.gateway.ws ?? "ws://127.0.0.1:8870/ws"}", { inst: ${inst}, role: "${role}"${role === "player" ? `, player: ${player}` : ""} });
await ow.connect();
const mine = await ow.api.units("me");
await ow.api.ui.button("hi", "${tx("点我")}", { screen: [40, 300] });
ow.on("event:ui.click", (e) => console.log("${tx("点了")}", e.key));
await ow.subscribe({ events: true });`;
  const pyCode = `import time
from openwar3 import Game

g = Game(inst=${inst})
print(len(g.my_units()))
g.ui.button("hi", "${tx("点我")}", screen=(40, 300), on_click=lambda g, ev: g.ui.toast("${tx("收到！")}"))
while True:
    g.events()          # ${tx("回调在这里触发")}
    time.sleep(0.1)`;

  if (!data) return <div className="page"><FetchState error={error} onRetry={reload} /></div>;
  const gw = data.gateway;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">{tx("接入与扩展")}</h1>
          <p className="page-desc">{tx("把游戏接到别的程序和大模型上：网关让任何语言、浏览器页面调同样的接口；MCP 让 Claude Code 这类 Agent 直接调工具；Python SDK 最省事。")}</p>
        </div>
        <div className="right row">
          <button className="btn sm" onClick={reload}><RefreshCw size={13} />{tx("刷新")}</button>
        </div>
      </div>

      <div className="row wrap" style={{ gap: 10, marginBottom: 14 }}>
        <span className="faint" style={{ fontSize: 12.5 }}>{tx("下面的地址和命令按这个角色生成：")}</span>
        <Seg value={role} onChange={setRole} options={[{ value: "dev", label: tx("开发") }, { value: "player", label: tx("选手") }, { value: "observer", label: tx("观众") }]} />
        {role === "player" && (
          <label className="row" style={{ gap: 6, fontSize: 12.5 }}>{tx("玩家号")}
            <select className="select sm" value={player} onChange={(e) => setPlayer(Number(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
        )}
        <span className="faint" style={{ fontSize: 12 }}>{ROLE_DESC[role]}</span>
      </div>

      <div className="grid g2" style={{ alignItems: "start" }}>
        <Card title={tx("网关（WebSocket / JSON）")} icon={<Plug size={15} />}
          actions={gw.running ? <Badge tone="success">{tx("在跑 · 端口 {0}", gw.port)}</Badge> : <Badge>{tx("没在跑")}</Badge>}>
          <div className="col gap12">
            <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
              {tx("Python SDK 能调的公开接口，JS、C#、Go、浏览器页面、另一台机器上的程序都能调，方法名和参数一样；延迟是快车道再加约 1 ms。")}
            </div>
            <div className="row wrap" style={{ gap: 6 }}>
              <BusyButton className="btn primary sm" disabled={gw.running || !gw.exists}
                onClick={async () => { await action(() => api.post("/api/connect/gateway/start"), tx("网关已启动")); reload(); }}>
                <Plug size={13} />{gw.running ? tx("已经在跑") : tx("启动网关")}
              </BusyButton>
              <a className={`btn sm ${gw.running ? "" : "disabled"}`} href={gw.running ? gw.demo : undefined} target="_blank" rel="noreferrer"
                title={gw.running ? gw.demo : tx("先启动网关")}><ExternalLink size={13} />{tx("打开演示页")}</a>
            </div>
            <div>
              <div className="field-label">{tx("连接地址（实例 {0}）", inst)}</div>
              <CodeBox code={wsUrl} />
            </div>
            <div>
              <div className="field-label">{tx("JS 客户端（浏览器或 Node 22+）")}</div>
              <CodeBox code={jsCode} />
            </div>
            <div className="faint" style={{ fontSize: 12 }}>{tx("日志：{0} · 文档：docs/GATEWAY_ZH.md", gw.log)}</div>
          </div>
        </Card>

        <Card title={tx("MCP 服务器（给大模型的工具）")} icon={<Bot size={15} />}
          actions={<Badge tone="accent">{tx("{0} 个工具", data.mcp.tools.length)}</Badge>}>
          <div className="col gap12">
            <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>
              {tx("Claude Code、Claude Desktop 或任何支持 MCP 的客户端挂上它，大模型就能直接看局面、下命令、在屏幕上跟玩家说话、弹卡片问玩家、截图。第一次调工具时才连游戏。")}
            </div>
            <div>
              <div className="field-label">{tx("Claude Code 一行挂上")}</div>
              <CodeBox code={mcpCmd} />
            </div>
            <div>
              <div className="field-label">{tx("别的客户端（配置文件里的 mcpServers）")}</div>
              <CodeBox code={mcpJson} />
            </div>
            <table className="table">
              <thead><tr><th>{tx("工具")}</th><th>{tx("做什么")}</th></tr></thead>
              <tbody>
                {data.mcp.tools.map((t) => {
                  const off = (t.who === "dev" && role !== "dev") || (t.who === "draw" && role === "observer");
                  return (
                    <tr key={t.name} style={off ? { opacity: 0.45 } : undefined} title={off ? tx("这个角色用不了") : undefined}>
                      <td className="mono">{t.name}</td><td>{tx(t.desc)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title={tx("Python SDK")} icon={<TerminalSquare size={15} />}>
          <div className="col gap12">
            <div className="muted" style={{ fontSize: 13, lineHeight: 1.7 }}>{tx("最省事的接法：写 Bot、玩法模组、自己的小工具都用它。")}</div>
            <CodeBox code={pyCode} />
          </div>
        </Card>

        <Card title={tx("远见里的其它入口")} icon={<Globe size={15} />}>
          <div className="col gap8">
            <button className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => navigate("events")}>{tx("事件与输入：看这一局的事件、鼠标和选中")}</button>
            <button className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => navigate("jass")}>{tx("JASS 控制台：写脚本点运行，查 1291 个函数")}</button>
            <button className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => navigate("schemes")}><Puzzle size={13} />{tx("AI 方案：切换 Bot 和玩法模组、导入导出")}</button>
            <div className="faint" style={{ fontSize: 12, lineHeight: 1.7, marginTop: 4 }}>
              {tx("HTTP（本机）：POST /api/instances/{0}/canvas 画东西，POST /api/instances/{0}/jass 跑 JASS，GET /api/i/{0}/events 读事件。", inst)}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
