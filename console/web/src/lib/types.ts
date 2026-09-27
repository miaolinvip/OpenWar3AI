// 服务端数据的形状（和 farsight/server 对齐）

export type UnitTuple = [number, string, number, number, number, number, number, number, number];
// [addr, type, owner, x, y, hp, hpMax, order(-1 未知), hasTask(-1/0/1)]

export interface Snapshot {
  pid: number;
  t: number | null;
  me: number;
  inGame: boolean;
  gold: number | null;
  lumber: number | null;
  foodUsed: number | null;
  foodCap: number | null;
  epoch: number;
  ageMs: number;
  units: UnitTuple[];
}

export interface Unit {
  addr: number;
  type: string;
  owner: number;
  x: number;
  y: number;
  hp: number;
  hpMax: number;
  order: number;
  hasTask: number;
}

export const toUnit = (u: UnitTuple): Unit => ({
  addr: u[0], type: u[1], owner: u[2], x: u[3], y: u[4], hp: u[5], hpMax: u[6], order: u[7], hasTask: u[8],
});

export interface Meta {
  names: Record<string, string>;
  orders: Record<string, string>;
  units: Record<string, { hp?: number; armor?: number; food?: number; gold?: number; lumber?: number; level?: number; spd?: number; range?: number; dmgAvg?: number; cool?: number; atk?: string; defT?: string }>;
  abilities: Record<string, string>;
  casts: Record<string, { order: number; orderName: string; target: "point" | "unit" | "instant" }>;
  races: Record<string, string>;
  difficulty: Record<string, string>;
}

export interface InstanceStatus {
  inst: number;
  game: { pid: number; alive: boolean; exe: string | null; started: number | null; channel: boolean; openingDone: boolean; race: number | null; enemyRace: number | null };
  farm: { pid: number; cmd: string; withAi: boolean; keep: boolean; range: [number, number]; started: number | null }[];
  launcher: { pid: number; started: number | null } | null;
  held: { why?: string; at?: number } | null;   // 停止牌 inst<N>/hold.json：停了，不会自己再开（开始测试时摘掉）
  brain: { pid: number; started: number }[];
  oldAi: { pid: number; started: number | null }[];
  director: { pid: number; started: number | null }[];
  modules: { name: string; pid: number; parent: number }[];
  speed: number | null;
  match: { name: string; finalized: boolean; outcome: string | null; mtime: number } | null;
  nextGame: { map?: string; race?: number; enemyRace?: number; difficulty?: number; speed?: number; director?: boolean; scheme?: string };
  brainLive: boolean;
  bot?: { pid: number; started: number | null; scheme: string | null }[];   // 方案运行器（玩家方案）
  scheme?: string;                  // 这个实例选的 AI 方案（来源/id）
  schemeRunning?: string | null;    // 现在实际在跑的方案
  schemeKind?: "brain" | "bot" | "mod" | null;   // 在跑的（没在跑就是选的）方案是参考大脑 / bot / 玩法模组
}

export interface Scheme {
  ref: string; source: "builtin" | "mine" | "installed"; id: string; name: string; version?: string; author?: string;
  description?: string; kind: "brain" | "bot" | "mod"; entry?: string; class?: string; fair?: boolean; hz?: number; judge?: boolean; maps?: string;
  races?: string[]; license?: string; homepage?: string; forked_from?: string; trusted: boolean; dir: string; error?: string;
  i18n?: Record<string, Record<string, string> | undefined>;   // 别的语言的名字/简介（scheme.json 可选字段 {"en": {"name", "description"}}）
}
export interface SchemeStats { games: number; wins: number; losses: number; unknown: number; midGame?: number; avgMinutes: number; last: number }
export interface SchemesOverview {
  schemes: Scheme[];
  stats: Record<string, SchemeStats>;
  instances: { inst: number; scheme: string | null; running: string | null; alive: boolean; farm: boolean; held: boolean }[];
  dirs: { mine: string; installed: string };
}
export interface SchemeResult {
  scheme: string; name: string; version: string; inst: number; pid: number; start: number; end: number;
  result: "win" | "loss" | "unknown"; reason: string; gameMinutes?: number; ticks?: number; errors?: number; midGame?: boolean;
}

export interface WorkerStatus { inst: number; pid: number; connected: boolean; lastSnapAgo: number | null; error: string | null; snaps: number }

export interface BrainWant { kind: string; code: string; qty: number; prio: number; note: string; have: number | null; status: string }
export interface BrainLive {
  v: number;
  wall: number;
  t: number;
  configVersion: number;
  config: Record<string, unknown>;
  world: {
    gold: number; lumber: number; foodUsed: number; foodCap: number; tier: number; foeTier: number; foeRace: string | null;
    ownStrength: number; foeStrength: number; aggression: number; army: number; heroes: number; workers: number; halls: number;
    enemyFighters: number; enemyBuildings: number; creeps: number;
    myArmies: { n: number; x: number; y: number; strength: number }[];
    enemyArmies: { owner: number; n: number; x: number; y: number; strength: number; dx: number; dy: number }[];
    towns: { owner: number; hall: string | null; x: number; y: number; threat: number }[];
    acceptedThreat: number;
  };
  attack: {
    state: string; target: { addr: number; type: string; x: number; y: number } | null; targetKind: string; startStrength: number;
    searchLeft: number; camp: { x: number; y: number; strength: number; alive: number; committed: boolean } | null;
    gatherAt: number | null; retreatAt: number | null; safe: boolean; counts: Record<string, number>;
  };
  defense: { mode: string; counts: Record<string, number>; preserveSince: number };
  harass: { counts: Record<string, number>; busy: number };
  plan: { t?: number; gold?: number; lumber?: number; committed?: number[]; stoppedAt?: string | null; wants?: BrainWant[] };
  pending: Record<string, unknown>[];
  economy: { goldCrew: number; lumberCrew: number; trees: number; sites: number };
  intents: { kind: string; n: number; x: number; y: number; why: string; target: string | null }[];
  notes: [number, string][];
  counts: Record<string, number>;
  act: Record<string, number>;
  jobs: Record<string, { runs: number; errors: number; ms: number; last_error: string | null }>;
  peak: Record<string, number>;
}

export interface UnitDetail {
  addr: number;
  type: string;
  order: { order: number; target: number[] | null; x: number; y: number } | null;
  task: { cls: string | null; id: number; target: number[] | null; x: number | null; y: number | null } | null;
  mana: number | null;
  manaMax: number | null;
  level?: number | null;
  xp?: number | null;
  skillPoints?: number | null;
  inventory?: (string | null)[] | null;
  abilities: { code: string; level: number; cooldown: number | null; cooling: boolean; name: string | null; cast: { order: number; orderName: string; target: string } | null; buff: boolean }[];
  errors?: Record<string, string>;
}

export interface MapEntry {
  path: string; rel?: string; file: string; folder: string; players: number | null; size: number; mtime?: number; tft: boolean;
  name?: string; author?: string; suggested?: string; preview?: string | null; tileset?: string; description?: string; slots?: number;
  // 区分同名地图（console/server/mapinfo.py fingerprint）：整份文件哈希、各部分哈希、自带 AI 脚本、固定玩家设置、每个位置
  sha1?: string; parts?: Record<string, string | null>; aiScripts?: string[]; aiKind?: "AMAI" | "custom" | null;
  fixedSlots?: boolean; seats?: { type: number; race: number }[];   // type 1 玩家 2 电脑 3 中立 4 可营救；race 0 可选 1~4 人兽亡暗
}

export interface MapInfo {
  path: string; file: string; name?: string; author?: string; description?: string; suggested?: string;
  players?: { id: number; type: number; race: number; raceName: string; name: string; x: number; y: number }[];
  icons?: { kind: string; x: number; y: number }[];
  terrain?: { width: number; height: number; minX: number; minY: number; maxX: number; maxY: number } | null;
  cameraBounds?: number[];
  preview?: string | null;
  previewSize?: number[];
}

export interface CamStatus {
  inMatch: boolean;
  cam: null | { x: number; y: number; distance: number; pitch: number; yaw: number; fov: number; zOffset: number; boundMinX: number; boundMaxX: number; boundMinY: number; boundMaxY: number };
  ui: { hidden: boolean; auto: boolean; threshold: number; clean: boolean };
  bars: { width: boolean; colour: boolean };
  director: null | {
    running: boolean; mode: string; note: string; shot: Record<string, unknown> | null; heldFor: number; orbiting: boolean;
    stats: Record<string, number>; config: Record<string, number | boolean>;
    // 09-18 导演台改造：人话原因、人调过的参数、计数器（0 = 那条路没走过）、景别、账本进度
    why?: string; pinned?: string[]; counts?: Record<string, number>;
    size?: "wide" | "mid" | "close"; sizeWhy?: string; curDistance?: number;
    ledger?: { stamp: string | null; shots: number; ticks: number; error: string | null };
    lastTickS?: number;
  };
  directorHost?: string;
  directorHolderPid?: number | null;
  busy: boolean;
  shot: string | null;
  presets: { key: string; label: string }[];
  sequences: { key: string; label: string }[];
  error: string | null;
}

/** 导播账本里的一次切镜（war3-analytics/dirledger.py 写的 shots-*.jsonl 一行） */
export interface DirectorShot {
  t: number; gt: number | null; ev: "cut" | "follow" | "release";
  kind?: string; x?: number; y?: number; score?: number; how?: string; dist?: number | null;
  heldPrev?: number | null; reason?: string; why?: string; bk?: string | null; src?: string;
  size?: string; sizeWhy?: string; mode?: string;
  rivals?: { kind: string; score: number; x: number; y: number; why: string }[];
}

export interface ConfigItem { key: string; group: string; label: string; type: "bool" | "int" | "float" | "choice"; default: number | boolean | string; min: number | null; max: number | null; step: number | null; desc: string; options?: string[]; labels?: Record<string, string> }

// farsight/server/matches.py _summary
export interface MatchSummary {
  inst: number;   // 对局文件所在的实例目录（inst<N>）
  name: string; size: number; mtime: number; finalized: boolean; live: boolean;
  result: { outcome: string; minutes: number; peakArmy: number; peakGold?: number; peakWorkers?: number; peakHeroes?: number } | null;
  planVer: number | null; brain: boolean; date: string | null; time: string | null; startTs: number | null;
  opening?: { id: string; name: string } | null;     // 开局谱（war3-brain/brain/openings；none = 按建造表）
  game: number | null; episode: string | null; runId: string | null;
  instance: string | null; pid: number | null; exe: string | null; supervisorPid: number | null;
  kernel: { kernel: number; kernelRev: string | null; dirty?: boolean; approx: boolean } | null;
  tuned: boolean; config: Record<string, unknown>;
  /** 这一局属于哪批自动调参实验（study，如 s12）的第几组（trial）；没调参或对不上是 null */
  tuning?: { study?: string; trial?: number; tag?: string } | null;
  race: string | null; foeRace: string | null; map: string | null; difficulty: number | null; speed: number | null;
  endnote: { gap: number; advanced: number; suspect: boolean } | null;
  peakFoeArmy: number | null; maxHalls: number | null; tier: number | null; maxOwnStr: number | null;
  endOwnStr: number | null; endFoeStr: number | null; wallMinutes: number | null; actions: number; states: number;
}

export interface Kernel { n: number; rev: string; at: number; subject: string }

/** 一组对局的统计（console/server/matches.py _stats）：局数、打完、结局可信、胜、平均时长 / 峰值兵力我敌、误判结束 */
export interface GroupStats {
  n: number; done: number; reliable: number; wins: number;
  avgMinutes: number | null; avgPeakArmy: number | null; avgPeakFoe: number | null; suspects: number;
}
/** GET /api/matches/overview：统计、分组、筛选、分页都在后端算，网页只拿一页 */
export interface MatchOverview {
  scope: "inst" | "all"; inst: number | null;
  scanned: number; total: number; offset: number; size: number;
  stats: GroupStats;
  outcomes: [string, number][];
  byInst: (GroupStats & { inst: number; kernels: number[]; moreKernels: boolean; last: { date: string | null; time: string | null; live: boolean; outcome: string | null } })[];
  byKernel: (GroupStats & { kernel: number })[];
  byOpening: (GroupStats & { id: string; name: string })[];
  rows: MatchSummary[];
}

/** GET /api/disk（console/server/disk.py） */
export interface DiskUsage {
  scannedAt: number; tookMs: number; root: string; cached: boolean;
  drive: { path: string; total: number; used: number; free: number } | null;
  total: { bytes: number; files: number; growth24h: number };
  categories: { key: string; bytes: number; files: number; growth24h: number }[];
  instances: { inst: number; bytes: number; files: number; growth24h: number; newest: number; cats: Record<string, number> }[];
  dirs: { path: string; bytes: number; files: number; newest: number }[];
  largest: { path: string; bytes: number; mtime: number; key: string }[];
}

// farsight/server/issues.py
export type IssueStatus = "draft" | "open" | "fixed" | "wontfix";
export interface Issue {
  id: string; status: IssueStatus; title: string; note: string; category: string;
  inst: number | null; pid: number | null; exe: string | null; match: string | null; game: number | null;
  gameTime: number | null; window: number; source: "hotkey" | "web"; markId: string | null;
  kernel: number | null; kernelRev: string | null; at: number; created: number; updated: number;
}
export interface IssueContext {
  t: number; window: number; lastT: number; match: string; inst: number; wall?: [number, number];
  actions: { t: number; text: string }[];
  states: { t: number; gold: number; lumber: number; food: string; army: number; heroes: number; workers: number; ownStr: number | null; foeStr: number | null; attack: string | null; defense: string | null; why?: Record<string, number> }[];
  intel: { t: number; army: number; composition: string; race: string | null }[];
  logs: Record<string, { label: string; lines: { wall: number; text: string }[] }>;
}

export interface MatchDetail {
  name: string;
  meta: Record<string, any>;
  summary: MatchSummary;
  states: { tick: number; t: number; gold: number; lumber: number; foodUsed: number; foodCap: number; units: string; ownStr: number | null; foeStr: number | null; attack: string | null; defense: string | null }[];
  actions: { tick: number; t: number; text: string }[];
  intel: { tick: number; army: number; workers: number; composition: string }[];
  statesTotal?: number;       // 曲线抽样前一共多少拍（后端最多给 600 个点）
  actionsCapped?: boolean;    // 动作太多，只给了前 4000 条
}

/** 首页「控制中心」：GET /api/services（console/server/services.py） */
export type ServiceKey = "console" | "gateway" | "speech" | "llm" | "website" | "mcp";
export type ServiceState = "running" | "starting" | "partial" | "stopped" | "conflict" | "unavailable" | "on-demand" | "error";
export interface ServiceRow {
  key: ServiceKey;
  state: ServiceState;
  pids: number[];
  started: number | null;
  port?: number | null;
  url?: string | null;
  log?: string | null;
  reason?: string | null;          // unavailable 的原因：missing-websockets / missing-lmstudio / missing-node / missing-website
  actions: ("start" | "stop" | "restart")[];
  extra?: Record<string, any>;
  error?: string;
}
export interface ServicesResp { services: ServiceRow[]; at: number; root: string }

/** GET /api/health：环境自检。文字按 key / code 在网页上写（要翻译），后端只给状态 */
export interface HealthCheck {
  key: string;
  status: "ok" | "warn" | "error" | "info";
  code: string;
  value: string | null;
  path: string | null;
  extra: Record<string, any> | null;
}
export interface HealthResp {
  checks: HealthCheck[];
  summary: { ok: number; warn: number; error: number; info: number };
  at: number;
  root: string;
  deploy: { at?: string; python?: string; node?: string; mode?: string } | null;
  config: string | null;
}
