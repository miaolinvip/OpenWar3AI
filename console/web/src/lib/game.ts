// 游戏知识：玩家颜色、单位分类、训练/建造菜单、格式化
import type { InstanceStatus, Meta, Unit } from "./types";
import { isZh, tx } from "./i18n";

/** 这个实例现在谁在指挥：参考大脑 / 玩家方案（bot）/ 玩法模组（mod）/ 老 AI / 没有。
 *  ★ 09-25 以前总览只看 brain 和 oldAi，跑方案 / 模组的实例一律显示「没有 AI」。 */
export type AiKind = "brain" | "bot" | "mod" | "old" | "none";
export function aiState(i: InstanceStatus | null | undefined): { kind: AiKind; name: string | null } {
  if (!i) return { kind: "none", name: null };
  if (i.brain.length) return { kind: "brain", name: null };
  if (i.bot?.length) {
    const ref = i.schemeRunning ?? i.bot[0].scheme ?? null;
    return { kind: i.schemeKind === "mod" ? "mod" : "bot", name: ref ? ref.split("/").pop() ?? ref : null };
  }
  if (i.oldAi.length) return { kind: "old", name: null };
  return { kind: "none", name: null };
}

/** 一句话：「新大脑在指挥」「方案 rush 在指挥」「模组 hero-roguelike 在跑」「没有 AI」 */
export function aiSentence(i: InstanceStatus | null | undefined): string {
  const a = aiState(i);
  if (a.kind === "brain") return tx("新大脑在指挥");
  if (a.kind === "bot") return tx("方案 {0} 在指挥", a.name ?? "?");
  if (a.kind === "mod") return tx("模组 {0} 在跑", a.name ?? "?");
  if (a.kind === "old") return tx("老 AI 在指挥");
  return tx("没有 AI");
}

export const PLAYER_COLORS = [
  "#ff0303", "#0042ff", "#1ce6b9", "#540081", "#fffc01", "#fe8a0e", "#20c000", "#e55bb0",
  "#959697", "#7ebff1", "#106246", "#4e2a04",
];
export const NEUTRAL_HOSTILE = 12;
export const PASSIVE = 15;

export function ownerColor(owner: number, me: number): string {
  if (owner === NEUTRAL_HOSTILE) return "#6b2b2b";
  if (owner >= 13) return "#9a8a5a";
  return PLAYER_COLORS[owner % 12];
}

export function ownerLabel(owner: number, me: number): string {
  if (owner === me) return tx("我方（P{0}）", owner + 1);
  if (owner === NEUTRAL_HOSTILE) return tx("野怪");
  if (owner >= 13) return tx("中立");
  return tx("对手（P{0}）", owner + 1);
}

const HALLS = new Set(["htow", "hkee", "hcas", "ogre", "ostr", "ofrt", "unpl", "unp1", "unp2", "etol", "etoa", "etoe"]);
const WORKERS = new Set(["hpea", "opeo", "uaco", "ewsp"]);
const TOWERS = new Set(["hgtw", "hctw", "hatw", "hwtw", "owtw", "uzg1", "uzg2", "etrp"]);
const MINES = new Set(["ngol", "ugol", "egol"]);

export type Category = "hero" | "worker" | "hall" | "tower" | "building" | "mine" | "unit";

export function category(code: string, meta: Meta | null): Category {
  if (MINES.has(code)) return "mine";
  if (code && code[0] >= "A" && code[0] <= "Z") return "hero";
  if (WORKERS.has(code)) return "worker";
  if (HALLS.has(code)) return "hall";
  if (TOWERS.has(code)) return "tower";
  const u = meta?.units[code];
  if (u && (u.spd ?? 0) <= 0) return "building";
  return "unit";
}

export const CATEGORY_LABEL: Record<Category, string> = {
  hero: tx("英雄"), worker: tx("工人"), hall: tx("大厅"), tower: tx("塔"), building: tx("建筑"), mine: tx("金矿"), unit: tx("部队"),
};

export function unitName(code: string, meta: Meta | null) {
  return meta?.names[code] ?? code;
}

export function orderName(order: number, meta: Meta | null) {
  if (order < 0) return "—";
  if (order === 0) return tx("空闲");
  const hex = order.toString(16).toUpperCase().padStart(8, "0");
  const n = meta?.orders[hex];
  if (n) return n;
  if (order > 0x41000000) {
    // 训练/建造订单是四字码
    const s = String.fromCharCode((order >> 24) & 255, (order >> 16) & 255, (order >> 8) & 255, order & 255);
    return /^[A-Za-z0-9]{4}$/.test(s) ? tx("造 {0}", meta?.names[s] ?? s) : `0x${hex}`;
  }
  return `0x${hex}`;
}

const ORDER_CN: Record<string, string> = {
  smart: tx("右键"), stop: tx("停止"), attack: tx("攻击"), move: tx("移动"), patrol: tx("巡逻"), holdposition: tx("保持"), harvest: tx("采集"),
  resumeharvesting: tx("继续采集"), returnresources: tx("送资源"), repair: tx("修理/建造"), autoharvestgold: tx("采金"), autoharvestlumber: tx("伐木"),
};
export function orderLabel(order: number, meta: Meta | null) {
  const n = orderName(order, meta);
  return ORDER_CN[n] ?? n;
}

export const RACE_NAMES: Record<number, string> = { 1: tx("人族"), 2: tx("兽族"), 3: tx("亡灵"), 4: tx("暗夜") };
export const RACE_COLORS: Record<number, string> = { 1: "#3b82f6", 2: "#dc2626", 3: "#7c3aed", 4: "#059669" };
// 单字种族标记：不走 tx —— 「人」在别处是「几人」的量词，一个键两种意思
export const RACE_GLYPH: Record<number, string> = isZh ? { 1: "人", 2: "兽", 3: "亡", 4: "夜" } : { 1: "H", 2: "O", 3: "U", 4: "N" };
export const DIFFICULTY: Record<number, string> = { 2: tx("简单"), 3: tx("普通"), 4: tx("疯狂") };

// 建筑能训练/研究什么（按四字码）
export const TRAIN: Record<string, string[]> = {
  htow: ["hpea", "hkee"], hkee: ["hpea", "hcas"], hcas: ["hpea"],
  hbar: ["hfoo", "hrif", "hkni", "Rhde", "Rhri", "Rhan"], halt: ["Hamg", "Hmkg", "Hpal", "Hblm"],
  hars: ["hmpr", "hsor", "hspt", "Rhpt", "Rhst", "Rhss"], harm: ["hgyr", "hmtm", "hrtt", "Rhfc", "Rhfl", "Rhfs", "Rhrt"],
  hgra: ["hgry", "hdhw", "Rhhb", "Rhcd"], hbla: ["Rhme", "Rhar", "Rhra", "Rhla"], hlum: ["Rhlh", "Rhac"],
  unpl: ["uaco", "unp1"], unp1: ["uaco", "unp2"], unp2: ["uaco"], uaod: ["Udea", "Ulic", "Udre", "Ucrl"],
  usep: ["ugho", "ucry", "ugar", "Ruac", "Rugf", "Ruwb", "Rubu", "Rusf"], utod: ["unec", "uban", "Rune", "Ruba", "Rusl", "Rusm"],
  uslh: ["uabo", "umtw", "uobs", "Rupc", "Ruex"], ubon: ["ufro", "Rufb"], ugrv: ["Rume", "Ruar", "Rucr", "Rura"],
  ogre: ["opeo", "ostr"], ostr: ["opeo", "ofrt"], ofrt: ["opeo"], oalt: ["Obla", "Ofar", "Otch", "Oshd"],
  obar: ["ogru", "ohun", "ocat"], ofor: ["Ropg", "Robs", "Rora", "Rotr"], osld: ["oshm", "odoc", "ospw"], obea: ["orai", "okod", "owyv", "otbr"], otto: ["otau"],
  etol: ["ewsp", "etoa"], etoa: ["ewsp", "etoe"], etoe: ["ewsp"], eate: ["Ekee", "Emoo", "Edem", "Ewar"],
  eaom: ["earc", "esen", "ebal"], eaoe: ["edry", "edoc", "emtg"], eaow: ["efdr", "echm"], edos: ["ehip", "edcm"],
};

export const BUILD: Record<string, string[]> = {
  hpea: ["htow", "hhou", "hbar", "halt", "hlum", "hbla", "hars", "harm", "hgra", "hvlt", "hwtw"],
  uaco: ["unpl", "uzig", "usep", "uaod", "ugrv", "utod", "uslh", "ubon", "usap", "utom", "ugol"],
  opeo: ["ogre", "otrb", "obar", "oalt", "ofor", "osld", "obea", "otto", "ovln", "owtw"],
  ewsp: ["etol", "emow", "eaom", "eate", "eaoe", "eaow", "edos", "eden", "etrp"],
};

export function fmtTime(sec: number | null | undefined) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

export function hex(n: number) {
  return "0x" + n.toString(16).toUpperCase().padStart(8, "0");
}

export function isMine(u: Unit, me: number) {
  return u.owner === me;
}

// 结局怎么判的：新大脑在 war3-brain/run_brain.py（play_match 的局末那段 + classify_end），老 AI 在 war3ai.ps1
export const OUTCOME: Record<string, { label: string; tone: "success" | "danger" | "warning" | "info" | ""; desc: string }> = {
  victory: { label: tx("胜利"), tone: "success", desc: tx("对面建筑全部拆光满 20 游戏秒；或游戏进程退出 / 时钟停住时对面已经一座建筑都没有。") },
  defeated: { label: tx("失败"), tone: "danger", desc: tx("我方一座建筑都不剩满 30 游戏秒；或游戏进程没了时我方已无建筑、或大厅没了且强度不到对面一半。") },
  "defeated-or-ended": { label: tx("失败"), tone: "danger", desc: tx("老 AI 的判法：分不开被打败和游戏被结束。") },
  "conceded-weak": { label: tx("认输"), tone: "danger", desc: tx("自动认输（设置页「对局运行」）：开局满若干分钟后，我方强度不到对面的一定比例、能打的单位不超过上限，并持续一段游戏时间。") },
  conceded: { label: tx("认输"), tone: "danger", desc: tx("老 AI 的自动认输。") },
  timeout: { label: tx("超时"), tone: "warning", desc: tx("游戏时间超过设置里的最长对局分钟数。") },
  "ended-by-operator": { label: tx("手动结束"), tone: "", desc: tx("控制台点了「结束本局」（写 end_request.json 后发结束令）。不进胜率。") },
  crash: { label: tx("中断"), tone: "", desc: tx("游戏进程没了、连不上或监督器出错，按最后一份正常快照也判不出胜负 —— 结局不可信，不进胜率。") },
  "ended-stuck": { label: tx("卡住结束"), tone: "warning", desc: tx("游戏时钟好几秒不走（本意是抓「胜利/失败」结算对话框）或一直读不到对局，按最后一份正常快照判不出胜负，就记这个并发结束令。结局不可信，不进胜率。") },
};

// 「卡住结束」里哪些其实是误判：09-18 15:01~15:30 那批，监督器把大脑一轮慢 step（5 秒以上）当成了时钟停，游戏其实在跑 —— 0819719 修（run_brain.EndWatch）
export const STUCK_SUSPECT_DESC = tx("误判：结局写在最后一份快照之后不到 1 秒，而那一拍游戏时钟还在往前走 —— 游戏在正常跑，是监督器把大脑一轮慢 step 当成了「时钟停了」，把这局在半路结束了（0819719 已修）。");

export const RACE_KEY: Record<string, number> = { human: 1, orc: 2, undead: 3, nightelf: 4 };
export const TIER_LABEL: Record<number, string> = { 1: tx("一本"), 2: tx("二本"), 3: tx("三本") };

export function mapLabel(file: string | null | undefined) {
  return file ? file.replace(/^\(\d+\)/, "").replace(/\.w3[xm]$/i, "") : "";
}

export const ATTACK_STATE: Record<string, string> = { home: tx("在家"), creep: tx("打野"), gather: tx("集结"), attack: tx("出击"), retreat: tx("撤退"), loot: tx("捡装备") };
export const DEFENSE_MODE: Record<string, string> = { none: tx("安全"), minor: tx("小股骚扰"), defend: tx("回防"), toostrong: tx("保存实力"), last_stand: tx("死守"), off: tx("已关闭") };
