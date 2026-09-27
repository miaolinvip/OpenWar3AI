// 直播日志页（/log）：8777 旧页 streamlog.html 的新大脑版。不用 React —— 滚动动画是逐元素的 FLIP，
// 直接操作 DOM 最稳，而且 OBS 浏览器源加载得越轻越好。数据：/api/i/{n}/stream（1 秒）+ /stream/meta（10 秒），
// 翻译在服务端（farsight/server/stream.py），这里只管过滤、合并相邻重复、增量追加。
import "./live.css";
import { tx } from "../lib/i18n";

// ⚠ 模块级状态一律放在最顶上：旧页因为 let 的暂时性死区白屏过两次（初始化代码在声明之前就去写它们）
interface Item { id: string; g: number | null; tick: number; text: string; cat: string; key: string; count: boolean }
interface Group { gid: string; key: string; text: string; cat: string; n: number; count: boolean; g: number | null; el?: HTMLDivElement }

const RACE_CN: Record<string, string> = { human: tx("人族"), orc: tx("兽族"), undead: tx("亡灵"), nightelf: tx("暗夜") };
const TIER_CN: Record<number, string> = { 1: tx("一本"), 2: tx("二本"), 3: tx("三本") };
const DIFF_CN: Record<number, string> = { 2: tx("简单"), 3: tx("普通"), 4: tx("疯狂") };
const WIN_OUTCOMES = new Set(["victory"]);
const LOSE_OUTCOMES = new Set(["defeated", "defeated-or-ended", "conceded-weak", "conceded", "timeout"]);
const OUTCOME_CN: Record<string, string> = {
  victory: tx("拿下了"), defeated: tx("被推平"), "defeated-or-ended": tx("没守住"), "conceded-weak": tx("打不动认输"), conceded: tx("认输"),
  timeout: tx("时间到"), "ended-by-operator": tx("手动结束"), crash: tx("中断"), "ended-stuck": tx("卡住结束"),
};
// 分类：键、名字、颜色、默认开关（经济最啰嗦，默认关 —— 和旧页一样）
const CATS: [string, string, string, boolean][] = [
  ["ai", tx("智能判断"), "var(--ai)", true],
  ["fight", tx("战斗"), "var(--fight)", true],
  ["army", tx("兵力"), "var(--army)", true],
  ["build", tx("建造"), "var(--build)", true],
  ["tech", tx("科技"), "var(--tech)", true],
  ["econ", tx("经济"), "var(--econ)", false],
  ["big", tx("里程碑"), "var(--big)", true],
  ["sep", tx("场记"), "var(--dim)", true],
];
const LS = "farsight.live";
const SLIDE = 320;                       // 滑动时长

const Q = new URLSearchParams(location.search);
if (Q.has("id") && !Q.has("inst")) Q.set("inst", Q.get("id")!);   // ?id=5 = War3-5.exe 那个实例（旧页的叫法）
const INST = Math.max(1, parseInt(Q.get("inst") || "5", 10) || 5);
const REVIEW = Q.has("match");
const ROWS = Math.max(1, Math.min(30, parseInt(Q.get("rows") || "3", 10) || 3));

const saved: { on?: Record<string, boolean>; size?: number } = (() => {
  try { return JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch { return {}; }
})();
const on: Record<string, boolean> = {};
CATS.forEach(([k, , , def]) => { on[k] = saved.on && k in saved.on ? !!saved.on[k] : def; });
// 地址栏定分类：?cats=ai,fight,army 只开这几类；?off=econ,sep 关掉这几类。
// OBS 浏览器源的 localStorage 和平时用的浏览器不是一份，在 Chrome 里点好的勾选到 OBS 里不算数 —— 写在地址里才带得过去
if (Q.get("cats")) { const want = new Set(Q.get("cats")!.split(",")); CATS.forEach(([k]) => { on[k] = want.has(k); }); }
if (Q.get("off")) Q.get("off")!.split(",").forEach((k) => { if (k in on) on[k] = false; });
let size = Number(Q.get("size")) || saved.size || 2.5;   // 和 live.css 的 --size 保持一致

let shown: Group[] = [];                 // 当前 DOM 里的条目
let queue: Group[] = [];                 // 等着错开追加的条目
let pumping = false;
let pageVer: number | null = null;
let lastItems: Item[] = [];
// 资源行：游戏时间取自 stream（1 秒一拉），采金速度按最近 60 游戏秒的累计采金差算
interface EcoRow { owner: number; me: boolean; gold: number | null; lumber: number | null; foodUsed: number | null;
  foodCap: number | null; upkeep: number | null; goldGathered: number | null }
let gameT: number | null = null;
let ecoHist: { t: number; me: number; foe: number }[] = [];

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const feed = $("feed");
const deadEl = $("dead");

const mmss = (g: number | null | undefined) =>
  g == null || !Number.isFinite(g) ? "" : `${Math.floor(g / 60)}:${String(Math.floor(g % 60)).padStart(2, "0")}`;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const race = (k: string | null | undefined) => (k ? RACE_CN[k] ?? k : "?");

function qs(extra: Record<string, string | number | null | undefined> = {}) {
  const p = new URLSearchParams();
  if (Q.get("match")) p.set("match", Q.get("match")!);
  if (Q.get("tick")) p.set("tick", Q.get("tick")!);
  for (const [k, v] of Object.entries(extra)) if (v != null) p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

// ── 外观：字号 / 行数 / 透明度 / 分类勾选 ──
function save() { try { localStorage.setItem(LS, JSON.stringify({ on, size })); } catch { /* 隐私模式 */ } }
function applySize() {
  document.documentElement.style.setProperty("--size", String(size));
  $("sizeVal").textContent = size.toFixed(2).replace(/0$/, "");
  forceRebuild();
}
document.documentElement.style.setProperty("--rows", String(ROWS));
if (Q.get("alpha") && Number.isFinite(Number(Q.get("alpha")))) document.documentElement.style.setProperty("--alpha", Q.get("alpha")!);
if (Q.has("clean")) document.body.classList.add("clean");                   // 隐藏选择框
if (Q.get("controls") === "0") document.body.classList.add("nocontrols");   // 连分类勾选和字号按钮也藏起来
$("plus").onclick = () => { size = Math.min(3.2, size + 0.15); applySize(); save(); };
$("minus").onclick = () => { size = Math.max(0.7, size - 0.15); applySize(); save(); };
CATS.forEach(([k, label, color]) => {
  const b = document.createElement("span");
  b.className = "chip" + (on[k] ? " on" : "");
  b.style.setProperty("--c", color);
  b.innerHTML = '<i class="dot"></i>';
  b.append(label);
  b.onclick = () => { on[k] = !on[k]; b.classList.toggle("on", on[k]); forceRebuild(); save(); render(groupsOf(lastItems)); };
  $("filters").append(b);
});
applySize();

// ── 条目 -> 分组：先按分类过滤，再把相邻同 key 的合并（带次数的显示 ×N）──
// 对齐靠 id（服务端每条一个稳定 id），不靠文本 —— 旧页按文本对齐，同一句话隔几条又出现就对错位置、整屏重建
function groupsOf(items: Item[]): Group[] {
  const out: Group[] = [];
  for (const it of items) {
    if (!on[it.cat]) continue;
    const last = out[out.length - 1];
    if (last && last.key === it.key && last.cat === it.cat) { last.n++; last.g = it.g; continue; }
    out.push({ gid: it.id, key: it.key, text: it.text, cat: it.cat, n: 1, count: it.count, g: it.g });
  }
  if (!out.length) out.push({ gid: "_wait", key: "_wait", text: tx("· 等待对局开始 …"), cat: "", n: 1, count: false, g: null });
  return out;
}

function makeRow(o: Group): HTMLDivElement {
  const row = document.createElement("div");
  row.className = "row " + o.cat;
  const t = document.createElement("span"); t.className = "t"; t.textContent = mmss(o.g);
  const m = document.createElement("span"); m.className = "m"; m.textContent = o.text;
  row.append(t, m);
  if (o.n > 1 && o.count) {
    const x = document.createElement("span"); x.className = "x"; x.textContent = "×" + o.n;
    row.append(x);
  }
  return row;
}
function refresh(o: Group) {
  if (!o.el) return;
  (o.el.querySelector(".t") as HTMLElement).textContent = mmss(o.g);
  let x = o.el.querySelector(".x") as HTMLElement | null;
  if (o.n > 1 && o.count) {
    if (!x) { x = document.createElement("span"); x.className = "x"; o.el.append(x); }
    x.textContent = "×" + o.n;
  } else if (x) x.remove();
}

// ★ 追加一行 —— FLIP 让已经在屏幕上的那些行真的滑上去（09-16：已有的文字要平滑上移，原来是直接跳到新位置）
//   1 记下现有每行的布局位置（offsetTop，不能用 getBoundingClientRect —— 它含 transform，会被入场动画污染）
//   2 改 DOM   3 transform 拉回原位（transition: none）   4 两次强制重排后放开 transform，让它们平滑滑到新位置
function appendOne(o: Group) {
  const rows = [...feed.children] as HTMLElement[];
  const firsts = rows.map((r) => r.offsetTop);
  o.el = makeRow(o);
  feed.append(o.el);
  shown.push(o);
  while (shown.length > ROWS) shown.shift()!.el?.remove();
  rows.forEach((r) => { if (r.isConnected && r.style.transform) r.style.transform = ""; });   // 残留位移自愈
  const movers: HTMLElement[] = [];
  rows.forEach((r, i) => {
    if (!r.isConnected) return;
    const dy = firsts[i] - r.offsetTop;
    if (!dy) return;
    r.style.transition = "none";
    r.style.transform = `translateY(${dy}px)`;
    movers.push(r);
  });
  if (movers.length) {
    void feed.offsetHeight;                                  // 让 inverted 落定
    movers.forEach((r) => { r.style.transition = `transform ${SLIDE}ms cubic-bezier(.22,.61,.36,1)`; });
    void feed.offsetHeight;                                  // transition 进入「变更前样式」，否则不触发、transform 永久卡住
    movers.forEach((r) => { r.style.transform = ""; });
    setTimeout(() => movers.forEach((r) => { r.style.transition = ""; r.style.transform = ""; }), SLIDE + 40);
  }
}
function pump() {
  if (pumping) return;
  const step = () => {
    if (!queue.length) { pumping = false; return; }
    pumping = true;
    appendOne(queue.shift()!);
    // 间隔不短于滑动时长，否则上一条没滑完就被打断，看起来又是「跳」；积压多了缩短间隔
    setTimeout(step, queue.length > 6 ? 120 : queue.length > 2 ? 200 : 340);
  };
  step();
}
// ★ 09-22 滚动字幕反复刷屏，来路不明的旧内容一直在滚 —— 三处一起：
//   1 「上次滚到哪」要取**排队里的最后一条**（原来先取屏上的：一次来 10 条、1 秒没滚完，下一轮把队里没滚的又排一遍，越积越多）
//   2 从后往前找（服务端拼了上一局的尾巴，id 曾经两局撞号 —— 从前往前找会找到上一局那条，把九十多条旧内容重滚一遍）
//   3 积压封顶：一下来太多就只留最后几条滚，不追着把几十条全滚完
const MAXQ = Math.max(ROWS, 8);
function render(want: Group[]) {
  const tail = queue[queue.length - 1] ?? shown[shown.length - 1];
  let p = -1;
  if (tail) for (let i = want.length - 1; i >= 0; i--) if (want[i].gid === tail.gid) { p = i; break; }
  if (p < 0) {
    feed.replaceChildren();
    shown = []; queue = [];
    for (const o of want.slice(-ROWS)) { o.el = makeRow(o); feed.append(o.el); shown.push(o); }
    return;
  }
  // 最后一条的次数涨了（×2 -> ×3）就地改，不新增行
  if (tail.n !== want[p].n || tail.g !== want[p].g) { tail.n = want[p].n; tail.g = want[p].g; refresh(tail); }
  const fresh = want.slice(p + 1);
  if (fresh.length) {
    queue.push(...fresh);
    if (queue.length > MAXQ) queue.splice(0, queue.length - MAXQ);
    pump();
  }
}
function forceRebuild() { shown = []; queue = []; feed.replaceChildren(); }

// ── 顶栏：这页在看谁 ──
interface Source { mode: string; inst: number; match: string | null; game: number | null; maxTick: number; tick: number | null;
  finalized: boolean; outcome: string | null; idleSec: number | null }
interface Now { t: number | null; own: number; foe: number; army: number; heroes: number; workers: number; tier: number; foeTier: number;
  halls: number; food: string; doing: string; state: string; defense: string | null;
  opening: { name: string; step: number; steps: number; current: string; active: boolean } | null }
interface StreamResp { source: Source; items: Item[]; clock: { t: number | null; race: string | null; foeRace: string | null; tick: number } | null;
  now: Now | null; at: string }

const hhmm = (name: string | null) => {
  const m = name?.match(/match-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/);
  return m ? `${m[4]}:${m[5]}` : "";
};
function renderSource(s: Source) {
  const src = $("src");
  const who = `War3-${s.inst}`;
  if (s.mode === "none") {
    src.className = "badge stale";
    src.textContent = REVIEW ? tx("找不到 {0} 这一局", Q.get("match")) : tx("{0} 还没有对局记录", who);
    return;
  }
  const no = s.game ? tx("第 {0} 局", s.game) : hhmm(s.match);
  if (s.mode === "match") {
    src.className = "badge review";
    src.textContent = tx("回看 {0} · {1} {2}", who, no, hhmm(s.match)) + (s.outcome ? ` · ${OUTCOME_CN[s.outcome] ?? s.outcome}` : "");
    const box = $("scrubBox"), scrub = $<HTMLInputElement>("scrub");
    if (s.maxTick) {
      box.classList.add("on");
      scrub.max = String(s.maxTick);
      const at = s.tick == null ? s.maxTick : Math.min(s.tick, s.maxTick);
      if (document.activeElement !== scrub) scrub.value = String(at);
      $("scrubVal").textContent = `${at}/${s.maxTick}`;
    }
    return;
  }
  const idle = s.idleSec ?? 0;
  const stale = idle > 90;
  src.className = "badge" + (stale ? " stale" : "");
  src.textContent = `${who} · ${stale ? tx("记录停了 {0} 分钟", Math.floor(idle / 60)) : s.finalized ? tx("局间") : tx("直播中")} · ${no}`;
}

// 局势：新大脑此刻的双方强度（拔河条）、在干嘛、家底 —— 替掉旧页的「底座」行（底座账本 foundation.json 是老 AI 那条线，09-17 起没再更新）
function renderNow(n: Now | null) {
  const row = $("nowRow"), line = $("nowLine");
  if (!n) { row.classList.add("off"); return; }
  row.classList.remove("off");
  const tot = (n.own || 0) + (n.foe || 0);
  const pct = tot > 0 ? Math.round(((n.own || 0) / tot) * 100) : 50;
  line.innerHTML =
    tx("<span class=\"own\">我 {0}</span><span class=\"tug\"><i style=\"width:{1}%\"></i></span>", esc((n.own ?? 0).toFixed(1)), pct) +
    tx("<span class=\"foe\">{0} 敌</span>", esc((n.foe ?? 0).toFixed(1))) +
    `<span class="doing">${esc(n.doing)}${n.defense ? ` · 🛡 ${esc(n.defense)}` : ""}</span>` +
    // 开局谱（照高手录像的开局顺序）走到第几步：开局那几分钟最想让观众看见的就是「它在照谁的套路走」
    (n.opening?.active ? `<span class="doing" title="${esc(n.opening.current)}">📜 ${esc(n.opening.name)} ${n.opening.step}/${n.opening.steps}</span>` : "") +
    tx("<span class=\"sub\">兵 <b>{0}</b> · 英雄 <b>{1}</b> · 农民 <b>{2}</b> · 人口 {3}", n.army ?? 0, n.heroes ?? 0, n.workers ?? 0, esc(n.food)) +
    ` · ${TIER_CN[n.tier] ?? ""}${n.foeTier && n.foeTier !== n.tier ? tx("（对面{0}）", TIER_CN[n.foeTier] ?? "") : ""}` +
    `${n.halls > 1 ? tx(" · {0} 矿", n.halls) : ""}</span>`;
}

async function pull() {
  try {
    const d = await getJSON<StreamResp>(`/api/i/${INST}/stream${qs({ limit: 120 })}`);
    $("at").textContent = d.at || "";
    renderSource(d.source);
    const t = d.now?.t ?? d.clock?.t;
    if (t != null) gameT = t;
    const c = d.clock;
    $("clock").textContent = c ? `${mmss(t)} · ${race(c.race)} vs ${race(c.foeRace)}` : "";
    if (!REVIEW) renderNow(d.now);
    lastItems = d.items || [];
    render(groupsOf(lastItems));
    // 停多久也要写出来：「停了」和「停了 87 分钟」是两条不同的信息
    const idle = d.source.idleSec ?? 0;
    if (!REVIEW && (d.source.mode === "none" || idle > 90)) {
      const m = Math.floor(idle / 60);
      deadEl.textContent = d.source.mode === "none" ? tx("⛔ War3-{0} 还没有对局记录", INST)
        : m >= 60 ? tx("⛔ AI 已经停了 {0} 小时 {1} 分 —— 没人在开车了", Math.floor(m / 60), m % 60) : tx("⛔ AI 已经停了 {0} 分钟 —— 没人在开车了", m);
      deadEl.style.display = "block";
    } else deadEl.style.display = "none";
  } catch { /* 服务没起来时安静重试 */ }
}

// ── 顶栏徽章 + 简报（10 秒一次）──
interface Meta {
  record: { games: number; wins: number; todayW: number; todayL: number; streak: number };
  kernel: { n: number; rev: string; dirty: boolean; w: number; l: number; subject: string; prev: { n: number; w: number; l: number } | null } | null;
  speed: number | null;
  setup: { difficulty: number | null; map: string; race: string | null; foeRace: string | null };
  tuner: { study: string; games: number; wins: number; asked: number; insts: number[]; difficulty: number | null; running: boolean } | null;
  brief: { match: string; game: number | null; race: string | null; foeRace: string | null; outcome: string; outcomeText: string;
    minutes: number | null; army: number | null; foeArmy: number | null; tier: number | null; halls: number | null } | null;
  battles: { g: number | null; where: string; dur: number; us: number; them: number; lostUs: number; lostThem: number; heroesDead: string[] }[];
  instances: { inst: number; live: boolean; recent: string[] }[];
  pageVer: number;
}

function renderMeta(m: Meta) {
  const pct = Number(m.speed) || 100;
  $("spd").textContent = pct > 100 ? tx("⏩ {0}× 加速推进", String(pct / 100).replace(/\.0$/, "")) : tx("▶ 正常速度");
  // 战绩：只算新大脑、进胜率的结局（手动结束 / 中断不算）。今日战绩放悬停提示 —— 顶栏只有两行，留给连胜
  const r = m.record;
  $("games").innerHTML = r.games
    ? tx("累计 <b>{0}</b> 局 <i>{1} 胜</i>", r.games, r.wins) + (r.streak >= 2 ? tx(" · 🔥 {0} 连胜", r.streak) : "")
    : "";
  $("games").title = tx("War3-{0} 上新大脑的战绩（手动结束 / 中断不算）；今日 {1} 胜 {2} 负", INST, r.todayW, r.todayL);
  // 大脑版本：节目的主线是「它在不在变强」—— 这一版和上一版的战绩摆在一起；不到 8 局变灰并写明局数
  const k = m.kernel, kel = $("kern");
  if (k) {
    const n = k.w + k.l;
    const rate = (w: number, l: number) => (w + l ? w / (w + l) : 0);
    const arrow = k.prev && n ? (rate(k.w, k.l) > rate(k.prev.w, k.prev.l) ? '<span class="up">↑</span>' : rate(k.w, k.l) < rate(k.prev.w, k.prev.l) ? '<span class="down">↓</span>' : "") : "";
    kel.classList.toggle("thin", n < 8);
    kel.innerHTML = tx("大脑 #{0} <b>{1}胜{2}负</b>{3}", k.n, k.w, k.l, arrow) + (k.prev ? tx(" · 上版 {0}胜{1}负", k.prev.w, k.prev.l) : "")
      + (n < 8 ? tx("（才 {0} 局）", n) : "");
    kel.title = tx("这一版：内核 #{0}（{1}{2}）改了什么：{3}", k.n, k.rev, k.dirty ? tx("，有未提交改动") : "", k.subject)
      + (k.prev ? tx("\n上一版：内核 #{0}", k.prev.n) : "");
  } else kel.textContent = "";
  // 后台调参：「它正在自己试新参数」
  const tu = m.tuner, tel = $("tune");
  if (tu && tu.running && tu.insts.length) {
    tel.innerHTML = tx("后台 <b>{0}</b> 台调参 · {1} 局 {2} 胜", tu.insts.length, tu.games, tu.wins);
    tel.title = tx("自动调参实验 {0}：实例 {1}", tu.study, tu.insts.join("、")) + (tu.difficulty ? tx("，对{0}电脑", DIFF_CN[tu.difficulty] ?? "") : "")
      + tx("，派过 {0} 组参数", tu.asked);
  } else tel.textContent = "";

  const b = m.brief, prow = $("prevRow");
  if (b) {
    prow.classList.remove("off");
    const cls = WIN_OUTCOMES.has(b.outcome) ? "win" : LOSE_OUTCOMES.has(b.outcome) ? "lose" : "";
    const parts = [
      tx("{0}<b>{1}</b> 打 <b>{2}</b>", b.game ? tx("第 {0} 局 ", b.game) : "", esc(race(b.race)), esc(race(b.foeRace))),
      `<span class="${cls}">${esc(b.outcomeText)}</span>`,
      b.minutes != null ? tx("{0} 分钟", Math.round(b.minutes)) : "",
      b.army != null ? tx("兵力峰值 {0} 对 {1}", b.army, b.foeArmy ?? "?") : "",
      b.tier ? `${TIER_CN[b.tier] ?? ""}${b.halls && b.halls > 1 ? tx(" {0} 矿", b.halls) : ""}` : "",
    ].filter(Boolean);
    $("briefLine").innerHTML = parts.join('<span class="dot">·</span>');
  } else prow.classList.add("off");

  const frow = $("fightRow"), fl = $("fightLine");
  if (m.battles?.length) {
    frow.classList.remove("off");
    fl.replaceChildren(...m.battles.slice(-2).map((x) => {
      const el = document.createElement("span");
      const dead = x.heroesDead?.length ? tx("｜英雄阵亡 {0}", x.heroesDead.join("、")) : "";
      el.innerHTML = tx("{0} {1} {2}秒｜参战 我 {3} : 敌 {4}｜阵亡 ", mmss(x.g), esc(x.where), x.dur, x.us, x.them)
        + tx("<span class=\"{0}\">我 {1} : 敌 {2}</span>{3}", x.lostUs <= x.lostThem ? "w" : "l", x.lostUs, x.lostThem, esc(dead));
      return el;
    }));
  } else { frow.classList.add("off"); fl.replaceChildren(); }

  fillPick(m.instances || []);
  // 页面换代：live.html 重新构建了就自己刷新（OBS 里不用手点）
  if (m.pageVer) {
    if (pageVer !== null && m.pageVer !== pageVer) { location.reload(); return; }
    pageVer = m.pageVer;
  }
}

let pickKey = "";
function fillPick(list: Meta["instances"]) {
  const key = JSON.stringify(list);
  if (key === pickKey) return;
  pickKey = key;
  const pick = $<HTMLSelectElement>("pick");
  const opts: HTMLOptionElement[] = [];
  for (const s of list) {
    opts.push(new Option(`War3-${s.inst} · ${s.live ? tx("直播中") : tx("已停")}`, `inst=${s.inst}`));
    if (s.inst !== INST) continue;
    for (const f of s.recent) {
      const m = f.match(/^match-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}(?:-(\d+))?/);
      if (!m) continue;
      const id = f.replace(/\.jsonl$/, "");
      opts.push(new Option(tx("　回看 {0}-{1} {2}:{3}{4}", m[2], m[3], m[4], m[5], m[6] ? tx(" 第 {0} 局", +m[6]) : ""), `inst=${s.inst}&match=${id}`));
    }
  }
  pick.replaceChildren(...opts);
  const want = REVIEW ? `inst=${INST}&match=${Q.get("match")}` : `inst=${INST}`;
  for (const o of pick.options) if (o.value === want || (REVIEW && o.value.startsWith(`inst=${INST}&match=${Q.get("match")}`))) { o.selected = true; break; }
}
$<HTMLSelectElement>("pick").onchange = (e) => {
  const v = (e.target as HTMLSelectElement).value;
  const keep = new URLSearchParams(v);
  for (const k of ["clean", "rows", "alpha", "size"]) if (Q.get(k)) keep.set(k, Q.get(k)!);
  location.search = "?" + keep.toString();
};
const scrub = $<HTMLInputElement>("scrub");
scrub.oninput = () => { $("scrubVal").textContent = `${scrub.value}/${scrub.max}`; };
scrub.onchange = () => {
  Q.set("tick", scrub.value);
  history.replaceState(null, "", "?" + Q.toString());
  forceRebuild(); void pull(); void meta();
};

async function meta() {
  try { renderMeta(await getJSON<Meta>(`/api/i/${INST}/stream/meta${qs()}`)); } catch { /* 下一轮再说 */ }
}

// ── 资源：双方金木 / 累计采金（09-22 新增：实况日志里显示敌军的资源情况）──
// 数据是 /api/i/{n}/players（worker 直接调引擎的 GetPlayerState，控制台总览页「双方经济」同一份）。全知信息：人类玩家看不到对手的钱。
// 「累计采金」比手里的存量更能说明谁经济好（存量 = 收入 - 支出）；速度是每游戏分钟，不是墙钟（3 倍速下墙钟会差三倍）
function ecoRate(key: "me" | "foe"): number | null {
  if (ecoHist.length < 2) return null;
  const a = ecoHist[0], b = ecoHist[ecoHist.length - 1];
  return b.t - a.t >= 20 ? ((b[key] - a[key]) / (b.t - a.t)) * 60 : null;
}
function renderEco(rows: EcoRow[]) {
  const row = $("ecoRow"), line = $("ecoLine");
  const me = rows.find((r) => r.me);
  const foes = rows.filter((r) => !r.me);
  if (!me || !foes.length) { row.classList.add("off"); return; }
  const sum = (k: keyof EcoRow) => foes.reduce((s, r) => s + (Number(r[k]) || 0), 0);
  const foe: EcoRow = { owner: -1, me: false, gold: sum("gold"), lumber: sum("lumber"), foodUsed: sum("foodUsed"), foodCap: sum("foodCap"),
    upkeep: Math.max(...foes.map((r) => r.upkeep ?? 0)), goldGathered: sum("goldGathered") };
  if (gameT != null && me.goldGathered != null) {
    const last = ecoHist[ecoHist.length - 1];
    if (last && (gameT < last.t - 1 || me.goldGathered < last.me)) ecoHist = [];   // 换了一局：游戏时间或累计采金往回走
    if (!last || gameT > last.t) ecoHist.push({ t: gameT, me: me.goldGathered, foe: foe.goldGathered ?? 0 });
    while (ecoHist.length > 2 && ecoHist[0].t < gameT - 60) ecoHist.shift();
  }
  // 一方 7 格（谁 / 金 / 木 / 人口 / 总采金 / 速度 / 维护），两方共用 #ecoLine 的 grid 列宽，所以上下对齐；
  // 格子数必须固定 —— 没数据也占一格（空串），少一格后面整行就错位
  const side = (label: string, cls: string, r: EcoRow, rate: number | null) => [
    `<span class="${cls}">${label}</span>`,
    tx("<span>金 <b class=\"{0}\">{1}</b></span>", (r.gold ?? 0) >= 1000 ? "rich" : "", r.gold ?? "?"),
    tx("<span>木 <b>{0}</b></span>", r.lumber ?? "?"),
    tx("<span>人口 <b>{0}/{1}</b></span>", r.foodUsed ?? "?", r.foodCap ?? "?"),
    tx("<span>总采金 <b>{0}</b></span>", r.goldGathered ?? "?"),
    `<span class="rate">${rate != null ? tx("+{0}/分", Math.max(0, Math.round(rate))) : ""}</span>`,
    tx("<span class=\"{0}\">维护 {1}%</span>", r.upkeep ? "" : "none", r.upkeep ?? 0),
  ].join("");
  line.innerHTML = side(tx("我"), "own", me, ecoRate("me")) + side(tx("敌"), "foe", foe, ecoRate("foe"));
  row.classList.remove("off");
}
async function eco() {
  try {
    const d = await getJSON<{ me: number; players: EcoRow[] }>(`/api/i/${INST}/players`);
    renderEco((d.players || []).filter((r) => r.owner < 12));
  } catch { $("ecoRow").classList.add("off"); }
}

void pull(); void meta();
setInterval(pull, 1000);
setInterval(meta, 10000);
// 回看没有实时资源（对局记录里没存对手的钱），那一行占位空着
if (Q.get("eco") === "0") document.body.classList.add("noeco");
else if (REVIEW) $("ecoRow").classList.add("off");
else { void eco(); setInterval(eco, 3000); }
addEventListener("resize", () => { forceRebuild(); render(groupsOf(lastItems)); });
// 回看模式没有「AI 停了」这回事；不直播时局势行空着但占位（整页高度固定）
if (REVIEW) $("nowRow").classList.add("off");
