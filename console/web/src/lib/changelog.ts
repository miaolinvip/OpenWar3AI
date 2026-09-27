// 「最近更新」：远见和底座最近加了什么。新的一条加在最前面，id 递增 ——
// 侧栏的小红点按浏览器里记的「看过的最大 id」判断（localStorage farsight.seenChange）。
import { tx } from "./i18n";

// 和仓库根的 VERSION 文件一致（后端按那个文件和 war3ai.com/version.json 比新旧；console/tests/test_version.py 守着）
export const VERSION = "0.4.0";

export interface ChangeItem { text: string; page?: string }
export interface ChangeEntry { id: number; date: string; title: string; items: ChangeItem[] }

export const CHANGELOG: ChangeEntry[] = [
  {
    id: 10, date: "2026-09-26", title: tx("部署只要 Python：不再装 PowerShell 7 和 Node.js"), items: [
      { text: tx("新电脑上 start.bat 只装一个私有的 Python（约 14 MB）；PowerShell 用 Windows 自带的，以前下的 PowerShell 7（约 105 MB）自动删掉") },
      { text: tx("起游戏实例的 farm 改成了 Python（runtime/farm.py），用法不变：多开、打完按下一局设置重开、认停止牌"), page: "instances" },
      { text: tx("编好的网页随仓库发：不用装 Node.js、不用下 90 MB 的依赖；只有改了网页源码，start.bat 才会下 Node.js 重编") },
      { text: tx("环境自检：「PowerShell 7」换成系统自带的「Windows PowerShell」；Node.js 标成「不用装」"), page: "home" },
      { text: tx("官网预览要 Node.js 时，命令行运行 start.bat node 装一份"), page: "home" },
    ],
  },
  {
    id: 9, date: "2026-09-26", title: tx("游戏目录自己设、新版本提醒、反馈与建议"), items: [
      { text: tx("魔兽争霸 III 目录由你自己设置：控制中心最上面的设置卡片（也在「设置」页），能自动查找、浏览选择，保存后检查游戏版本、自动提取游戏数据"), page: "home" },
      { text: tx("游戏版本不是 1.27a（1.27.0.52240）时给温馨提示；开局前也会检查，不再硬开导致游戏崩溃。更多版本即将支持，请关注 War3AI.com"), page: "settings" },
      { text: tx("选地图：游戏 Maps 目录下所有文件夹的地图都列出来（以前文件名没有 (2) 这种前缀的不显示），能按文件夹筛；下一局的地图存相对 Maps 的路径"), page: "instances" },
      { text: tx("新版本提醒：远见启动时向 War3AI.com 问一次（之后每 6 小时），有新版本在顶上和侧栏底部提示"), page: "home" },
      { text: tx("新页面「反馈与建议」：遇到问题、有建议直接提交给 War3AI.com，可以附带诊断信息、发之前能预览；运行时签名，防伪造、防刷"), page: "feedback" },
      { text: tx("远见只接受本机的请求：别的网站不能借你的浏览器操作远见") },
    ],
  },
  {
    id: 8, date: "2026-09-26", title: tx("头顶气泡并进远见；多开标签页更顺手"), items: [
      { text: tx("新页面「头顶气泡」：让单位说话、调样式、和 AI 对话，茶话会、镜头对白、战况触发的开关和设置都在这里（原来 8872 上那个单独的调试页不用了）"), page: "speech" },
      { text: tx("每个标签页各看各的实例：实例号在地址里，刷新、收藏都不会跳；顶栏实例菜单里每个实例都能「在新标签页打开」") },
      { text: tx("侧栏的页面都是真链接：Ctrl + 点击或中键点击就在新标签页打开同一页、同一个实例；标签页标题写着页面名和实例号") },
      { text: tx("切到后台的标签页不再收实时画面，非战场页面每秒只刷一次顶栏：开再多标签页也不卡") },
      { text: tx("主题在一个标签页换了，别的标签页马上跟着换；「全部停止」后所有标签页都显示已停止") },
      { text: tx("选地图：卡片上写文件名；同名的几份（原版、带 AMAI 的、固定种族的、改过的）自动比对，标出哪里不一样"), page: "instances" },
    ],
  },
  {
    id: 7, date: "2026-09-26", title: tx("新首页「控制中心」，一个 start.bat 从零部署到打开远见"), items: [
      { text: tx("首页改成「控制中心」：在跑的服务和可以启动的服务分开摆，网关、头顶气泡、本地大模型、官网预览都在这里起停、看日志"), page: "home" },
      { text: tx("环境自检：Python、依赖包、PowerShell 7、注入运行时、游戏版本、游戏数据、网页、磁盘，哪项没就绪、怎么补都写着"), page: "home" },
      { text: tx("start.bat 一个脚本包办：新电脑上自己装 Python、PowerShell 7、Node.js（装在仓库的 bin\\env 里，不动系统），找游戏、提取数据、编网页，最后打开远见") },
      { text: tx("start-gateway.bat、start-speech.bat、start-website.bat 不用了，功能都并进了控制中心") },
      { text: tx("彻底停止：控制中心右上角「全部停止」，或双击 stop.bat —— 游戏实例、AI、各服务、本地模型、远见后台一起停"), page: "home" },
      { text: tx("原来的「总览」改名「对局总览」，内容不变"), page: "overview" },
    ],
  },
  {
    id: 6, date: "2026-09-26", title: tx("远见：数据多了也不卡、新页面「数据与磁盘」"), items: [
      { text: tx("对局记录：统计和分组改在后台算，表格一页 50 局；「所有实例」不再把几百局一次塞进网页（以前会把浏览器拖到卡死）；可以选最近 200 / 600 / 2000 局或全部"), page: "matches" },
      { text: tx("对局摘要存盘：远见重启后打开对局记录不用再等几千个文件重新解析"), page: "matches" },
      { text: tx("单局详情：曲线最多画 600 个点，动作时间线只画看得见的几十条") , page: "matches" },
      { text: tx("所有定时刷新：上一次没回来不发下一次，页面切走就暂停，内容没变不重画"), page: "decisions" },
      { text: tx("新页面「数据与磁盘」：战斗录制、对局记录、决策明细、日志各占多少，按实例看，最近 24 小时涨了多少，磁盘还能撑几天"), page: "storage" },
    ],
  },
  {
    id: 5, date: "2026-09-26", title: tx("底座：好几个程序同时用、网关更安全"), items: [
      { text: tx("模组、远见、MCP、网关可以同时往一局里放按钮和热键，互不覆盖；谁退出只收掉自己的"), page: "events" },
      { text: tx("程序被强行结束时，运行时 2 秒内清掉它留下的热键和地面点击拦截，它的按钮也不再拦点击") },
      { text: tx("网关：浏览器里别的网站连不上；选手角色不能结束游戏、改速度，也看不到别人的底牌"), page: "connect" },
      { text: tx("MCP：游戏重开后自动重连；观战角色只读；结果太长自动截断"), page: "connect" },
      { text: tx("不在对局里（菜单、结算页）时不报地面点击，放在世界上的画板元素也不画") },
      { text: tx("玩法模组：一个定时器出错不影响别的；刷完怪马上看得到新单位"), page: "schemes" },
    ],
  },
  {
    id: 4, date: "2026-09-25", title: tx("远见：事件流、接入与扩展、交互优化"), items: [
      { text: tx("新页面「事件与输入」：实时看这一局发生了什么（放技能、聊天、点按钮、热键、选中、玩家离开……），按类别筛选；旁边是鼠标位置、悬停项和本机选中"), page: "events" },
      { text: tx("新页面「接入与扩展」：网关状态和一键启动，MCP 挂接命令一键复制，各种接法的入口"), page: "connect" },
      { text: tx("跑方案或模组的实例不再显示「没有 AI」；模组在方案页有自己的图标；日志页能看方案运行器的输出"), page: "schemes" },
      { text: tx("有新版本时顶部出现一条横幅，自己点刷新，不再强制刷新丢掉没保存的输入") },
      { text: tx("读取失败会显示原因和「重试」；开始测试、保存这类按钮执行时转圈、防连点；「全部恢复默认」要先确认") },
      { text: tx("窄屏：侧栏自动收起，两栏布局改成上下排") },
    ],
  },
  {
    id: 3, date: "2026-09-25", title: tx("底座：界面与输入、事件补齐、网关、MCP、玩法模组"), items: [
      { text: tx("画板：往游戏画面上画文字框、面板、进度条、地上的圈和路线；按钮、选项卡能点，画在鼠标指针下面") },
      { text: tx("界面与输入：热键、点地面选位置、鼠标指着的地面点、本机选中了谁"), page: "events" },
      { text: tx("新事件：放技能、玩家离开、选择变化、屏幕消息和聊天全文、离开对局"), page: "events" },
      { text: tx("网关（WebSocket / JSON）：任何语言、浏览器页面都能调同样的接口"), page: "connect" },
      { text: tx("MCP 服务器：Claude Code 等客户端一行命令挂上，大模型直接调 10 个工具"), page: "connect" },
      { text: tx("玩法模组：方案类型 mod，内置英雄肉鸽、无尽守城两个示例"), page: "schemes" },
    ],
  },
  {
    id: 2, date: "2026-09-25", title: tx("远见英文界面"), items: [
      { text: tx("右上角切换中文 / English；方案可以带英文名字和简介") },
    ],
  },
  {
    id: 1, date: "2026-09-24", title: tx("AI 方案与 JASS 控制台"), items: [
      { text: tx("AI 方案：一键切换、导出 zip 分享、导入别人的方案（先确认信任）、战绩自动统计"), page: "schemes" },
      { text: tx("JASS 控制台：写脚本点运行，按分类查 1291 个函数"), page: "jass" },
    ],
  },
];

const SEEN_KEY = "farsight.seenChange";

/** 浏览器里记的「看过的最大 id」；从没打开过是 null */
export function seenChange(): number | null {
  try {
    const v = localStorage.getItem(SEEN_KEY);
    return v == null ? null : Number(v) || 0;
  } catch {
    return 0;
  }
}

export function markChangesSeen() {
  try { localStorage.setItem(SEEN_KEY, String(CHANGELOG[0].id)); } catch { /* 存不了就下次再提示 */ }
}

export const hasUnseenChanges = () => (seenChange() ?? 0) < CHANGELOG[0].id;
