# OpenWar3 Python 接口目录

由 `python -m openwar3.catalog` 从代码生成，别手改。每个接口都标了**实测状态**和**底层机制**。

⚠ 「实机验证过」指**底层那条路**（运行时动作号 / 语义命令 / 参数形状）在真实对局里验证过（实测记录见参考大脑 act.py 等处的注释）；SDK 门面和快车道是 OpenWar3 新写的：09-23 在演练实例上跑过快车道实机对拍（30/30 一致）和两个示例 bot（造农民、续建、伐木、兵营、英雄学技能），但还没覆盖每一个接口（施法、复活、物品、商店只验证过底层路）。

延迟说明：`快照` = 读共享内存，不等游戏（约 0.05 ms）；`快车道` = 约 1 帧（游戏线程成批执行）；`控制通道` = 20~40 ms（旧路，快车道接不上时自动退回）。

## 观察：读状态（不改变游戏）

| 接口 | 状态 | 延迟 | 底层 | 说明 |
|---|---|---|---|---|
| `snapshot(max_age: 'float' = 0.05)` | 实机验证过 | 推送快照 | W3P 世界块 Local\War3World_<pid>（运行时每 50 ms 推送，seqlock） | 整张地图的完整状态（WorldState）：.units .players .items .clock .me，max_age 秒内重复调用返回同一份。 |
| `last_seen(owner: 'str | int' = 'enemy', max_age: 'float | None' = None) -> 'list'` | 实机验证过 | 推送快照 | 推送快照的 visibleTo（每次刷新快照时记下看得见的敌方/野怪单位） | 最后一次看见的敌方（或 'creep' 野怪、或某个玩家号）单位：[(单位当时的样子, 当时的游戏时钟, 过去了几秒)]，新的在前。 |
| `map()` | 实机验证过 | 推送快照 | W3P 地图块 Local\War3Map_<pid>（开局后运行时分批算，IsTerrainPathable 行走/建造） | 这一局的地形表 MapInfo：.walkable(x,y) .buildable(x,y) .at(x,y) .bounds（可玩区）.starts（出生点）.cells（bit0 不能走、bit1 不能盖）。 |
| `me() -> 'int | None'` | 实机验证过 | 推送快照 | 世界块头 | 我是几号玩家（0~11）。 |
| `resources(player: 'int | None' = None) -> 'dict | None'` | 实机验证过 | 推送快照 | 世界块 players[16] | {'gold','lumber','food_used','food_cap','gold_gathered','lumber_gathered'}；player 默认我方，任何玩家都读得到。 |
| `players() -> 'list'` | 实机验证过 | 推送快照 | 世界块 players[16] | 全部 16 个玩家槽：Player(id, gold, lumber, food_used, food_cap, gold_gathered, lumber_gathered, race, known)。 |
| `units(owner: 'str | int' = 'all', types=None, alive: 'bool' = True) -> 'list'` | 实机验证过 | 推送快照 | 世界块 units[] | 按主人/类型筛单位。owner：'me' / 'enemy' / 'creep' / 'all' / 玩家号。types：四字码集合。 |
| `unit(handle) -> 'object | None'` | 实机验证过 | 推送快照 | 世界块 by_handle | 按句柄对 (lo, hi) 找单位（订单目标、任务目标、事件给的都是句柄对）。 |
| `is_building(u) -> 'bool'` | 实机验证过 | 推送快照 | 快照 + units.json（spd==0 = 建筑） | 是不是建筑（含塔）。按单位表移动速度 0 判；亡灵大厅的占地是 0，别用占地判。 |
| `my_workers() -> 'list'` | 实机验证过 | 推送快照 | 推送快照 | 我方工人（农民/苦工/侍僧/小精灵）。 |
| `idle_workers() -> 'list'` | 实机验证过 | 推送快照 | 推送快照（订单槽 + 任务槽） | 没活干的工人：没有订单、也没有任务（这一拍刚被你派了活的不算）。 |
| `my_heroes() -> 'list'` | 实机验证过 | 推送快照 | 推送快照 | 我方活着的英雄（死了的在祭坛复活表里，见 revive）。 |
| `my_army() -> 'list'` | 实机验证过 | 推送快照 | 推送快照 + units.json | 我方作战单位：不是工人、不是建筑。 |
| `my_buildings(types=None) -> 'list'` | 实机验证过 | 推送快照 | 推送快照 | 我方建筑（含塔、在建的地基）；types 可以只要某几种，如 {'hbar'}。 |
| `is_constructing(worker) -> 'bool'` | 实机验证过 | 推送快照 | 推送快照（订单 = 建筑四字码，或施工令/修理令） | 这个工人是不是正在盖房子（或正走去盖 / 在帮着修；含这一拍刚派的）。挑建造工时要跳过它，否则上一座地基会停工。 |
| `under_construction(building) -> 'bool'` | 实机验证过 | 推送快照 | 推送快照（地基的血从很低一路涨到满） | 这座建筑还没盖完（血没满）。⚠ 被打残的建筑也不满血 —— 开局判断够用，打起来之后要结合时间看。 |
| `gold_mines() -> 'list'` | 实机验证过 | 推送快照 | 推送快照（ngol/egol/ugol） | 地图上的金矿。⚠ 暗夜缠绕金矿和中立金矿同一坐标各有一个单位，采集要派到自己那一个。 |
| `enemies(fighters_only: 'bool' = False) -> 'list'` | 实机验证过 | 推送快照 | 推送快照 | 敌方玩家的单位（不含野怪）。fighters_only：去掉工人和建筑。 |
| `creeps() -> 'list'` | 实机验证过 | 推送快照 | 推送快照（owner 12 = 中立敌对） | 野怪（中立敌对）。⚠ 夜里视野变短，远处营地进迷雾后对它的目标命令会被拒（原因码 1001）。 |
| `life_mana(u) -> 'dict | None'` | 实机验证过 | 推送快照 | 世界块单位 hp/hpMax/mana/manaMax | {'hp','hp_max','mana','mana_max'}（浮点，引擎原值）。u 用快照里拿到的单位即可（会换成最新一份）。 |
| `hero_info(hero) -> 'dict | None'` | 实机验证过 | 推送快照 | 世界块单位 level/xp/skillPoints | {'level','xp','skill_points'}。 |
| `abilities(u) -> 'list'` | 实机验证过 | 推送快照 | 世界块细节：技能（代码/等级/标志/剩余冷却） | [{code, level, cooldown, flags}]；buff 在 buffs(u)。只有"有细节"的单位才有（英雄 > 玩家单位 > 野怪，最多 256 个）。 |
| `buffs(u) -> 'list'` | 实机验证过 | 推送快照 | 世界块细节：B 开头的技能对象 | 单位身上的 buff 码（如 'BHds' 神圣护盾、'Bslo' 减速）。码对应什么效果见 data/game/buffs.json。 |
| `cooldown(u, ability: 'str') -> 'float | None'` | 实机验证过 | 推送快照 | 世界块细节：技能剩余冷却（技能计时器） | 这个技能还要冷却几秒（游戏秒）；0 = 能放；没有这个技能（或这个单位没细节）返回 None。 |
| `inventory(hero) -> 'list | None'` | 实机验证过 | 推送快照 | 世界块细节：背包 6 格 | 6 格物品四字码（空格为 None）；没有背包返回 None。 |
| `current_order(u) -> 'dict | None'` | 实机验证过 | 推送快照 | 世界块单位 order / 订单目标 / 订单目标点 | {'order','target','x','y'}：单位手上这条订单（order 是 0x000D00xx 或建筑四字码，0 = 空闲）。 |
| `current_target(u)` | 实机验证过 | 推送快照 | 世界块单位任务目标 | 单位**实际在打/在追**的那个单位（没有返回 None）。 |
| `clock() -> 'float | None'` | 实机验证过 | 推送快照 | 世界块头 clockMs（引擎游戏时钟） | 引擎游戏时钟（游戏秒，载入中为 0）。倍速下它比墙钟走得快。 |
| `production(building)` | 实机验证过 | 推送快照 | 世界块生产表（Aque/ABnP/AUnP 技能对象 + 运行时跟踪的已进行时间；实测误差 < 0.2 游戏秒） | 这座建筑正在做什么：Production(kind, queue, duration, elapsed, blocked, progress, remaining…)，没在做返回 None。 |
| `queue(building) -> 'list'` | 实机验证过 | 推送快照 | 世界块生产表 | 训练/研究队列里的四字码（[0] 正在做）；空闲或不是生产建筑 = []。 |
| `all_production(owner: 'str | int' = 'me') -> 'list'` | 实机验证过 | 推送快照 | 世界块生产表 | 全部正在进行的生产 [(建筑, Production)]。owner 同 units()：'me' / 'enemy' / 玩家号 / 'all'。 |
| `path_distance(a, b) -> 'float | None'` | 实机验证过 | 推送快照 + 本地计算 | 地图块（引擎 IsTerrainPathable）+ 树块 + 建筑占地，SDK 侧 A*（128 一格） | 地面单位从 a 走到 b 的路程（a、b 给单位或 (x,y)）；走不到 None。岛图上判断"这个野点/分矿地面过不过得去"用它， |
| `reachable(a, b) -> 'bool | None'` | 实机验证过 | 推送快照 + 本地计算 | 同上 | 地面能不能走到（地图块没算好 = None）。 |
| `walk_path(a, b) -> 'list | None'` | 实机验证过 | 推送快照 + 本地计算 | 同上 | 路径拐点 [(x,y)...]（最后一点是 b）；配合 path(units, 点列表) 让部队按这条路走（绕开塔、走小路）。 |
| `upkeep(player: 'int | None' = None) -> 'dict | None'` | 推断/未完整实测 | 推送快照 | 1.27 固定规则：0~50 人口不收、51~80 收入 ×0.7、81~100 ×0.4 | 维护费档位：{'level': 'none'/'low'/'high', 'income': 1.0/0.7/0.4, 'next_at': 下一档的人口（没有 = None）}。 |
| `xp_to_next(hero) -> 'int | None'` | 实机验证过 | 推送快照 | 世界块 level/xp + MiscGame NeedHeroXP 公式 | 英雄离下一级还差多少经验（10 级 = 0）。 |
| `creep_camps(link: 'float' = 600.0) -> 'list'` | 实机验证过 | 推送快照 | 推送快照（野怪按 600 连成一群）+ units.json 等级 | 把场上（看得见的）野怪聚成营地：[{'x','y','units','level','hp','max_level'}]，按离我方主基地由近到远。 |
| `buff_info(code: 'str') -> 'dict | None'` | 实机验证过 | 本地数据 | data/game/buffs.json（AbilityData.slk 的 BuffID -> 技能/效果/时长） | buff 码是什么：{'ability','effect','dur','hero_dur','targets'}（例 'Bslo' -> 减速）。一码多行时给第一行。 |
| `stats(u, player: 'int | None' = None)` | 实机验证过 | 推送快照 + 快车道（5 秒一批） | 数据表（UnitBalance/UnitWeapons/UpgradeData/MiscGame）+ 实时科技等级 + 英雄等级 | 单位的战斗属性 combat.UnitStats：血/蓝上限、护甲（含攻防升级、英雄敏捷）、护甲类型、移速、白天/夜里视野、 |
| `time_to_kill(attackers, target) -> 'float | None'` | 实机验证过 | 推送快照 | stats() + 实时血量 | 这群单位一起打 target 要几游戏秒（用 target 现在的血；算克制、护甲、攻防升级；不算走位、溅射、治疗）。 |
| `time_of_day() -> 'float | None'` | 实机验证过 | 推送快照 | 世界块扩展区：GetFloatGameState(GAME_STATE_TIME_OF_DAY) | 游戏内时间（小时，0~24）。开局是早上 8 点；一整天 = 480 游戏秒（白天、黑夜各 240 秒，按昼夜流速缩放）。 |
| `is_night() -> 'bool | None'` | 实机验证过 | 推送快照 | 世界块扩展区（6~18 点白天） | 现在是不是夜里（18:00~6:00）。职业打法：夜里野怪睡着（先手打野不被围）、所有单位视野变短（偷袭好时机）， |
| `seconds_until(hour: 'float') -> 'float | None'` | 实机验证过 | 推送快照 | 世界块扩展区 + 480 秒一天（实测 20 游戏秒/小时） | 离游戏内时间 hour 点还有几游戏秒（比如 seconds_until(18) = 离天黑还有多久，计划夜里打野用）。 |
| `items_on_ground() -> 'list'` | 实机验证过 | 推送快照 | 世界块 items[]（只含地上的：持有者句柄全 FF） | 地上的物品 [Item(addr, handle_lo, handle_hi, type, x, y, life)]。捡走/用掉会发 item.removed 事件。 |
| `trees(x: 'float | None' = None, y: 'float | None' = None, limit: 'int' = 60) -> 'list'` | 实机验证过 | 推送快照 | 树块 Local\War3Trees_<pid>（每 2 秒刷新） | 活着的树（DestructableData 里 targType 含 tree 的），给了 (x,y) 就按距离由近到远，最多 limit 棵。 |
| `events() -> 'list'` | 实机验证过 | 推送快照 | 事件环 Local\War3Events_<pid>（发布对比 + 运行时捕获的伤害事件） | 上次调用之后发生的事：unit.appeared / unit.died / unit.removed / unit.damaged / order.changed / |
| `selection() -> 'list'` | 实机验证过 | 推送快照 | W3P 世界块扩展区 selAddrs（运行时每次发布时带上本机玩家的选择） | 本机玩家现在选中的单位（主单位排第一；最多 12 个）。选择一变会发 selection.changed 事件。 |
| `messages() -> 'list'` | 实机验证过 | 推送快照 | 共享内存 Local\War3Msgs_<pid>（运行时捕获的屏幕消息） | 上次调用之后屏幕消息框里新出的消息：[{'text', 'frame', 'repeat', 'seq', 'game_ms'}]。 |
| `tech(code: 'str', player: 'int | None' = None) -> 'int | None'` | 实机验证过 | 快车道 | W3P 查询 q_tech（引擎的玩家科技计数） | 研究等级 / 建完的建筑数（升级链算在内：城堡也算 htow）。player 默认我方，任何玩家都能查。 |
| `can_do(u, code: 'str') -> 'int | None'` | 实机验证过 | 快车道 | W3P 查询 q_feasible（引擎可行性检查） | 引擎的可行性裁决：0/220 能下；3 人口 8 缺金 9 缺木 32 队列满 183 缺前置 185 祭坛在复活 221 没有这一项/建造中。 |
| `can_do_many(pairs) -> 'list'` | 实机验证过 | 快车道 × 1 | W3P 查询 q_feasible × N，一批提交 | 一次问很多个 can_do：pairs = [(单位, 四字码), ...]，返回同样顺序的裁决码列表（问不到的是 None）。 |
| `tech_many(codes, player: 'int | None' = None) -> 'dict'` | 实机验证过 | 快车道 × 1 | W3P 查询 q_tech × N，一批提交 | 一次查很多个科技/建筑计数：{四字码: 数量或 None}。 |
| `visible(x: 'float', y: 'float') -> 'bool | None'` | 实机验证过 | 快车道 | W3P 查询 q_visible（看得见 / 迷雾 / 黑区） | 这一点我方现在看不看得见（不在迷雾/黑区里）。公平模式的 bot 应该只用看得见的敌人。 |
| `gold_left(mine) -> 'int | None'` | 推断/未完整实测 | 快车道 | W3P 查询 q_mine_gold（引擎的金矿余量） | 金矿还剩多少金。 |
| `enemy_ai_plan(enemy_unit) -> 'dict | None'` | 实机验证过 | 快车道 | W3P 查询 q_captain（敌兵跟着的电脑队长） | 电脑 AI 的队长：它带兵要去哪（出门前就知道要打你家哪里）。只对电脑对手有效；没跟队长走返回 None。 |
| `order_of(u) -> 'int | None'` | 实机验证过 | 推送快照 | 快照订单 + 本进程刚被接下的命令（回执） | 单位现在的订单，**含你这一拍刚下的**（快照还没追上时用回执里的新订单）。 |
| `can_afford(code: 'str') -> 'bool'` | 实机验证过 | 推送快照 | 推送快照的我方资源 + units.json 的价格 | 现在的金/木够不够买 code（单位、建筑；按 units.json 的价格）。价格表里没有的一律当买得起。 |
| `map_data()` | 实机验证过 | 读文件（第一次约 0.1 秒） | 地图文件（启动器 --map 的路径）：w3u/w3t/w3a + wts，保护过的图读图里的 TXT | 正在打的这张地图的数据（openwar3.mapdata.MapData）：name_of('HC07') 自定义单位/物品/技能的名字、hero_names、tooltip。 |

## 命令：让单位做事

| 接口 | 状态 | 延迟 | 底层 | 说明 |
|---|---|---|---|---|
| `batch() -> 'Batch'` | 实机验证过 | 快车道 × 1 | 块里的命令攒成一批，块结束时一次提交（同一帧执行、只等一次游戏线程） | 把一拍里的命令合成一批： |
| `move(units, x: 'float', y: 'float', queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P point：move（extra 位 = 排队方式） | 走到 (x,y)，路上不打人（撤退用这个）。可以传一个单位或一个列表（同一帧一起下）。 |
| `attack_move(units, x: 'float', y: 'float', queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P point：attack 对点 | 攻击移动（A 地面）：路上遇敌就打。queue 同 move。 |
| `attack(units, target, force: 'bool' = False, queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P target：目标命令（右键 smart） | 打 target。默认用右键（对敌人 = 攻击这一个，09-23 实测订单目标/任务目标都是它）。 |
| `stop(units)` | 实机验证过 | 快车道 | W3P immediate：stop | 停下手上的一切（订单号 0x000D0004），排着的订单也清掉。 |
| `hold(units, queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P immediate：holdposition | 原地待命（不追出去，只打射程里的）。 |
| `patrol(units, x: 'float', y: 'float', queue: 'str | None' = None)` | 推断/未完整实测 | 快车道 | W3P point：patrol | 在当前位置和 (x,y) 之间巡逻。 |
| `attack_ground(units, x: 'float', y: 'float', queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P point：attackground（攻城单位 / 迫击炮 / 投石车） | 攻击地面：炮对一块地方开火（打隐形单位、打树林后面的人、封路口）。只有能攻击地面的单位接受。 |
| `cancel(building)` | 实机验证过 | 快车道 | W3P immediate：cancel | 取消：训练/研究队列的最后一格（退钱）、正在盖的建筑（退 75%）、正在升级的大厅。 |
| `path(units, points, attack: 'bool' = False)` | 实机验证过 | 快车道 × 1 | 一批：第一段立刻执行，其余倒序用 queue='after' 插（引擎只有插在当前后面） | 按顺序走过一串点（Shift 连点：路径点、绕开塔、侦察路线）。attack=True 每段都是攻击移动。 |
| `gather(workers, target, queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P target：harvest（金矿或树） | 采金/伐木（target 是金矿或 trees() 里的树）。⚠ 只给空闲工人派（idle_workers）：给有任务的工人重下会打断采集周期。 |
| `repair(workers, building, queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P target：repair | 修理 / 帮着盖（人族兽族的工地没人盖会停工）。 |
| `build(worker, code: 'str', x: 'float', y: 'float', queue: 'str | None' = None)` | 实机验证过 | 快车道 | W3P build：建造订单，同一帧读回工人订单确认 | 让工人在 (x,y) 造 code（坐标对齐 32 格）。回执接下 = 工人订单已经是这座建筑（或开工令）； |
| `build_queue(worker, plan)` | 实机验证过 | 快车道 × 1 | 一批：第一座立刻，其余倒序 queue='after' | 一个工人按顺序连造几座（Shift 连造）：plan = [(四字码, x, y), ...]。一次提交；回执按 plan 顺序。 |
| `build_near(worker, code: 'str', x: 'float', y: 'float', min_r: 'float' = 450, max_r: 'float' = 1500, max_tries: 'int' = 24)` | 实机验证过 | 快车道 × 试的点数 | 逐点 build + 跟踪（地基出现 = 成；工人放弃订单又没地基 = 这个点拉黑） | 在 (x,y) 周围由近到远找个放得下的点盖 code。**不阻塞**，每拍调都行： |
| `train(building, code: 'str')` | 实机验证过 | 快车道 | W3P immediate：四字码，被拒时带可行性原因码 | 训练单位 / 研究科技 / 升级大厅（升本 = 对大厅本身下目标大厅的四字码，如 'hkee'）。 |
| `learn(hero, ability: 'str')` | 实机验证过 | 快车道 | W3P learn：技能点减少才算学会 | 英雄学技能（四字码，如 'AHbz' 暴风雪）。 |
| `cast(u, spell, target=None, x: 'float | None' = None, y: 'float | None' = None)` | 实机验证过 | 快车道 | W3P target / point / immediate（按参数选） | 放技能。spell 是订单名（'thunderbolt' 风暴之锤、'blizzard'、'holybolt' 圣光术…，见 data/order-ids.txt）或订单号。 |
| `rally(building, x: 'float | None' = None, y: 'float | None' = None, target=None)` | 实机验证过 | 快车道 | W3P rally | 设集结点（对点，或者对一个单位/金矿）。 |
| `revive(altar, hero=None)` | 实机验证过 | 快车道 | W3P revive：死亡英雄表 -> 祭坛对死英雄施放复活 | 在祭坛复活死掉的英雄（hero 不给就复活表里第一个）。 |
| `pick_up(hero, item)` | 实机验证过 | 快车道 | W3P target：右键物品 | 英雄去捡地上的物品（item 来自 items_on_ground）。捡到后背包里出现、地上发 item.removed 事件。 |
| `use_item(hero, slot: 'int', target=None, x: 'float | None' = None, y: 'float | None' = None)` | 实机验证过 | 快车道 | W3P use_item（按格号） | 用背包第 slot 格（0~5）的物品；可带目标单位或目标点。 |
| `drop_item(hero, slot: 'int', x: 'float', y: 'float')` | 实机验证过 | 快车道 | W3P item_drop（照抄 JASS UnitDropItemPoint：dropitem 0xD0021 对点 + 物品即时目标） | 把背包第 slot 格的东西丢在 (x,y)（英雄走过去放下）。 |
| `give_item(hero, slot: 'int', to)` | 实机验证过 | 快车道 | W3P item_drop（照抄 JASS UnitDropItemTarget：dropitem 对单位） | 把背包第 slot 格的东西给 to（别的英雄 / 单位，走过去递过去）。给商店 = 卖掉（见 sell_item）。 |
| `sell_item(hero, slot: 'int', shop)` | 实机验证过 | 快车道 | 同 give_item，目标是商店（实测避难权杖卖 125 金） | 把背包第 slot 格的东西卖给商店（英雄要走到商店旁边；可卖的物品才收，回一半价钱）。 |
| `move_item(hero, slot: 'int', to_slot: 'int')` | 实机验证过 | 快车道 | W3P target：订单 0xD0022+格号，目标 = 物品（照抄 JASS UnitDropItemSlot） | 背包里换格子（第 slot 格挪到第 to_slot 格；两格都有东西就对调）。整理快捷键位用。 |
| `buy(shop, item_code: 'str')` | 推断/未完整实测 | 快车道 | W3P buy：商店卖给旁边的英雄 | 在商店买物品（给站在商店旁边的英雄）。缺科技前置时引擎回 0、不扣钱。 |
| `call_to_arms(hall, on: 'bool' = True)` | 实机验证过 | 快车道 | W3P immediate：townbellon/off | 人族战斗号召：农民变民兵（一本城镇大厅没有这个技能，只对主城/城堡有效）。 |

## 游戏控制：速度、暂停、界面、气泡

| 接口 | 状态 | 延迟 | 底层 | 说明 |
|---|---|---|---|---|
| `ui()` | 实机验证过 | 共享内存 | W3P 74 input_enable + 共享内存 Local\War3Input_<pid>（运行时接收窗口输入） | 界面与输入（openwar3.ui.UI）：可点的按钮和选项卡、热键、点地面选位置、鼠标指着哪儿。 |
| `set_speed(percent: 'int') -> 'bool'` | 实机验证过 | 控制通道 | 动作 47（25~800%） | 游戏倍速（100 = 原速）。 |
| `pause(on: 'bool' = True)` | 实机验证过 | 快车道 | W3P pause | 暂停 / 继续游戏。暂停时引擎时钟停住，快车道照常能下令（事件分发还在跑）。 |
| `set_publish_period(ms: 'int') -> 'None'` | 实机验证过 | 推送快照 | 世界块 requestedPeriodMs | 世界状态的发布周期（16~1000 毫秒，默认 50）。一次采集约 0.5 ms，33 ms 也没问题；全机共用一个值，最后写的算。 |
| `say(u, text: 'str', seconds: 'float' = 4.0) -> 'bool'` | 实机验证过 | 控制通道 | 动作 56 | 单位头顶冒一个聊天气泡（直播/调试用，不影响游戏）。没冒出来返回 False，原因在 g.last_say_error。 |
| `message(text: 'str') -> 'bool'` | 推断/未完整实测 | 控制通道 | 动作 45 | 在游戏左下角消息区打一行字（只有本机看得见）。要等游戏自己先弹过一条提示（DLL 从那次抓消息框）。 |
| `end_game() -> 'bool'` | 实机验证过 | 控制通道 | 动作 22 | 结束这个游戏进程（farm.py --keep 会按 next_game.json 自动开下一局）。 |
| `canvas()` | 实机验证过 | 共享内存 | W3P 73 canvas_enable + 共享内存 Local\War3Canvas_<pid>（运行时每帧在游戏画指针前画，指针盖在上面） | 画板：往游戏画面上画文字框、面板、进度条、图片、地上的圈和路线（openwar3.canvas.Canvas）。 |
| `press_to_continue() -> 'bool'` | 实机验证过 | 窗口消息 | PostMessage WM_KEYDOWN/UP 空格到游戏窗口（不抢焦点） | 在「按下任意键以继续」的载入画面上按一下空格。很多 RPG / 剧情地图载入完要按键才开始（09-24 WarChasers 实测： |

## 沙盒：JASS 通道（造单位、设盟友、给物品…… RPG 辅助 / 玩伴用；只在单人局、本机工具里能改世界）

| 接口 | 状态 | 延迟 | 底层 | 说明 |
|---|---|---|---|---|
| `jass()` | 实机验证过 | 快车道 | W3P 70 jass（运行时按名字查 native 表，1291 个） | 按名字调任意 JASS native：g.jass.CreateUnit(g.jass.Player(1), "Hpal", x, y, 270.0)。 |
| `player_slots() -> 'list[dict]'` | 实机验证过 | 快车道 | JASS GetPlayerController / GetPlayerSlotState / IsPlayerAlly | 16 个玩家槽：controller（user 真人 / computer / neutral…）、state（empty / playing / left）、human、me、ally（和我是不是盟友）。 |
| `spawn(code: 'str', x: 'float', y: 'float', player: 'int | None' = None, facing: 'float' = 270.0)` | 实机验证过 | 快车道 | JASS CreateUnit + W3P 72 句柄 -> 单位 | 在 (x,y) 造一个单位（player 默认本机玩家），返回快照里的单位（等下一次世界发布，约 50 ms）；造不出来返回 None。 |
| `set_alliance(a: 'int', b: 'int', allied: 'bool' = True, vision: 'bool' = True, control: 'bool' = False, xp: 'bool' = False, both: 'bool' = True) -> 'None'` | 实机验证过 | 快车道 | JASS SetPlayerAlliance | 设玩家 a 对 b 的同盟关系：allied = 不互相攻击 + 互相求援；vision 共享视野；control 共享单位控制（b 能指挥 a 的单位）； |
| `set_player_name(player: 'int', name: 'str') -> 'None'` | 实机验证过 | 快车道 | JASS SetPlayerName | 改玩家名字（计分板、聊天、盟友面板里显示的那个）。给玩伴起名字用。 |
| `show_text(text: 'str', seconds: 'float' = 6.0, player: 'int | None' = None) -> 'None'` | 实机验证过 | 快车道 | JASS DisplayTimedTextToPlayer | 屏幕左下角显示一行字（地图触发器用的那种文字），默认给本机玩家看。支持 \|cffRRGGBB 颜色码。 |

## 连接与工具

| 接口 | 状态 | 延迟 | 底层 | 说明 |
|---|---|---|---|---|
| `status() -> 'dict'` | 实机验证过 |  | 世界块 + 快车道 + 仲裁表 | 连接状态：pid、世界发布（周期、采集耗时）、快车道计数。 |
| `nearest(candidates, to)` | 实机验证过 |  | 纯计算 | 离 to（单位或 (x,y)）最近的一个；没有候选返回 None。 |

