import { useMemo, useState, type ReactNode } from "react";
import { RotateCcw } from "lucide-react";
import { Badge, Card, Confirm, Field, RangeInput, Seg, Switch, useFetch } from "./ui";
import { api, action } from "../lib/api";
import type { ConfigItem } from "../lib/types";
import { tx } from "../lib/i18n";

/** 按后端 schema 渲染一组开关/滑块（AI 配置、对局运行开关共用）。只存和默认不同的项，改完立刻 PUT。
 *  schema 的 group/label/desc/选项名是后端给的中文，显示时 tx() —— 译文在 en.json，键由 tools/i18n_check.py 从后端 schema 收集 */
export default function ConfigCard({ title, icon, schemaUrl, configUrl, okText, footer }: {
  title: ReactNode; icon?: ReactNode; schemaUrl: string; configUrl: string; okText: string; footer?: ReactNode;
}) {
  const { data: schema } = useFetch<ConfigItem[]>(schemaUrl);
  const { data: cfg, reload } = useFetch<{ overrides: Record<string, unknown>; values: Record<string, any> }>(configUrl, 0, [configUrl]);
  const groups = useMemo(() => {
    const m = new Map<string, ConfigItem[]>();
    (schema ?? []).forEach((c) => m.set(c.group, [...(m.get(c.group) ?? []), c]));
    return m;
  }, [schema]);
  const put = async (patch: Record<string, unknown>) => { await action(() => api.put(configUrl, patch), okText); reload(); };
  const [resetAll, setResetAll] = useState(false);
  const nChanged = cfg ? Object.keys(cfg.overrides).length : 0;

  return (
    <Card title={title} icon={icon} style={{ marginBottom: 14 }} actions={nChanged > 0 && (
      <button className="btn sm" onClick={() => setResetAll(true)}><RotateCcw size={13} />{tx("全部恢复默认")}</button>
    )}>
      <Confirm open={resetAll} title={tx("全部恢复默认？")} danger confirm={tx("全部恢复默认")} onClose={() => setResetAll(false)}
        body={tx("改过的 {0} 项都会回到默认值，马上生效。", nChanged)}
        onConfirm={() => cfg && put(Object.fromEntries(Object.keys(cfg.overrides).map((k) => [k, null])))} />
      {[...groups.entries()].map(([g, items]) => (
        <div key={g}>
          <div className="section-label">{tx(g)}</div>
          {items.map((c) => {
            const v = cfg?.values?.[c.key] ?? c.default;
            const changed = cfg != null && c.key in cfg.overrides;
            return (
              <Field inline key={c.key} label={<span className="row">{tx(c.label)}{changed && <Badge tone="accent">{tx("已改")}</Badge>}</span>} desc={c.desc ? tx(c.desc) : undefined}>
                <div className="row" style={{ gap: 10 }}>
                  {c.type === "choice" ? (
                    <Seg value={String(v)} options={(c.options ?? []).map((o) => ({ value: o, label: tx(c.labels?.[o] ?? o) }))} onChange={(x) => put({ [c.key]: x })} />
                  ) : c.type === "bool" ? <Switch on={Boolean(v)} onChange={(x) => put({ [c.key]: x })} /> : (
                    <RangeInput value={Number(v)} min={c.min ?? 0} max={c.max ?? 100} step={c.step ?? 1} onCommit={(x) => put({ [c.key]: x })} />
                  )}
                  <button className="btn ghost icon sm" title={tx("恢复默认（{0}）", String(c.default))} disabled={!changed} onClick={() => put({ [c.key]: null })}><RotateCcw size={13} /></button>
                </div>
              </Field>
            );
          })}
        </div>
      ))}
      {footer}
    </Card>
  );
}
