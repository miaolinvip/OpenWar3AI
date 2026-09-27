import { useState } from "react";
import { Bot, Gamepad2, Languages, Monitor, Moon, Palette, SlidersHorizontal, Sun } from "lucide-react";
import { Card, Field, RangeInput, Seg, useFetch } from "../components/ui";
import ConfigCard from "../components/ConfigCard";
import GameDirSetup, { type GameDirStatus } from "../components/GameDirSetup";
import { ACCENTS, setTheme, useTheme } from "../lib/theme";
import { api, action } from "../lib/api";
import { setRate } from "../lib/live";
import { useApp } from "../lib/store";
import { LANGS, lang, setLang, tx } from "../lib/i18n";

export default function SettingsPage() {
  const theme = useTheme();
  const inst = useApp((s) => s.inst);
  const { data: console_, reload: reloadConsole } = useFetch<{ manualHoldSeconds: number; snapshotHz: number }>("/api/settings");
  const [hz, setHz] = useState(Number(localStorage.getItem("farsight.hz") || 4));
  const gd = useFetch<GameDirStatus>("/api/gamedir", 5000);

  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head"><div><h1 className="page-title">{tx("设置")}</h1><p className="page-desc">{tx("外观只保存在这台浏览器；AI 配置写到实例目录的 brain_config.json（只存改过的项）。")}</p></div></div>

      {/* 游戏目录：玩家自己设，所有游戏相关的相对路径（Maps、地图）都相对它 */}
      <div style={{ marginBottom: 14 }}>
        <GameDirSetup status={gd.data} onChanged={gd.reload} />
      </div>

      <Card title={tx("外观")} icon={<Palette size={15} />} style={{ marginBottom: 14 }}>
        <div className="grid g2" style={{ gap: 24 }}>
          <div className="col gap16">
            <Field label="语言 / Language">
              <Seg value={lang} onChange={(v) => v !== lang && setLang(v)} options={LANGS.map((l) => ({ value: l.code, label: l.name, icon: <Languages size={13} /> }))} />
            </Field>
            <Field label={tx("明暗")}>
              <Seg value={theme.mode} onChange={(v) => setTheme({ mode: v })} options={[{ value: "light", label: tx("浅色"), icon: <Sun size={13} /> }, { value: "dark", label: tx("深色"), icon: <Moon size={13} /> }, { value: "system", label: tx("跟随系统"), icon: <Monitor size={13} /> }]} />
            </Field>
            <Field label={tx("主题色")}>
              <div className="row wrap" style={{ gap: 10 }}>
                {ACCENTS.map((a) => <button key={a.hex} className={`swatch ${theme.accent.toLowerCase() === a.hex ? "on" : ""}`} style={{ background: a.hex }} title={a.name} onClick={() => setTheme({ accent: a.hex })} />)}
                <label className="row" style={{ gap: 6, marginLeft: 6 }}>
                  <input type="color" value={theme.accent} onChange={(e) => setTheme({ accent: e.target.value })} style={{ width: 34, height: 28, border: 0, background: "transparent", padding: 0 }} />
                  <span className="mono faint">{theme.accent}</span>
                </label>
              </div>
            </Field>
          </div>
          <Field label={tx("界面风格")}>
            <div className="grid g2">
              {([["modern", tx("现代"), tx("干净的中性色、圆角、细线分隔")], ["war3", tx("魔兽"), tx("石料底色、金色细线、衬线标题，克制不花哨")]] as const).map(([k, l, d]) => (
                <div key={k} className={`theme-card ${theme.style === k ? "on" : ""}`} onClick={() => setTheme({ style: k, accent: k === "war3" && theme.style !== "war3" ? "#d4a93a" : theme.accent })}>
                  <div className="theme-preview" style={k === "war3" ? { background: "#17140f", borderColor: "#56472a" } : { background: "var(--bg-sunken)" }}>
                    <div style={{ background: k === "war3" ? "#0e0c09" : "var(--bg-elev)", borderRight: `1px solid ${k === "war3" ? "#56472a" : "var(--border)"}` }} />
                    <div style={{ padding: 8 }}>
                      <div style={{ height: 8, width: "60%", borderRadius: 2, background: k === "war3" ? "#d4a93a" : "var(--accent)", marginBottom: 6 }} />
                      <div style={{ height: 6, width: "90%", borderRadius: 2, background: k === "war3" ? "#56472a" : "var(--border-strong)", marginBottom: 4 }} />
                      <div style={{ height: 6, width: "75%", borderRadius: 2, background: k === "war3" ? "#3a3223" : "var(--border)" }} />
                    </div>
                  </div>
                  <div style={{ fontWeight: 600 }}>{l}</div>
                  <div className="faint" style={{ fontSize: 12 }}>{d}</div>
                </div>
              ))}
            </div>
          </Field>
        </div>
      </Card>

      <Card title={tx("控制台")} icon={<SlidersHorizontal size={15} />} style={{ marginBottom: 14 }}>
        <Field inline label={tx("手动命令占住单位的时长")} desc={tx("手动下令后这么多秒内 AI 各层不覆盖（租约优先级 95）")}>
          <div className="row">
            <RangeInput value={console_?.manualHoldSeconds ?? 8} min={2} max={60} width={180} format={(v) => `${v}s`}
              onCommit={(v) => action(() => api.put("/api/settings", { manualHoldSeconds: v }), tx("已保存")).then(reloadConsole)} />
          </div>
        </Field>
        <Field inline label={tx("快照刷新率")} desc={tx("战场和单位表每秒更新几次（服务端最多 4 次）")}>
          <Seg value={hz} onChange={(v) => { setHz(v); setRate(v); }} options={[1, 2, 4].map((x) => ({ value: x, label: tx("{0} 次/秒", x) }))} />
        </Field>
      </Card>

      <ConfigCard title={tx("对局运行 · 实例 {0}", inst)} icon={<Gamepad2 size={15} />} schemaUrl="/api/ops/schema" configUrl={`/api/i/${inst}/ops`}
        okText={tx("已保存，监督器 2 秒内、导播 3 秒内读到")}
        footer={<p className="faint" style={{ fontSize: 12, marginTop: 10, lineHeight: 1.6 }}>
          {tx("写到实例目录的 ops.json。模块开关要下一局开局才生效，其它立刻生效。想马上结束这一局，用右上角的「结束本局」。")}
        </p>} />

      <ConfigCard title={tx("AI 配置 · 实例 {0}", inst)} icon={<Bot size={15} />} schemaUrl="/api/brain/schema" configUrl={`/api/i/${inst}/brain/config`}
        okText={tx("已保存，大脑 2 秒内生效")} />
    </div>
  );
}
