# AutoGamer — 玩游戏的软件

通用的游戏 AI：感知 → 决策 → 执行。先实现炉石传说（狂野，圣骑士机械套牌「号角」），后续加更多游戏时再加 `games/<游戏>/`。

## 结构

```
games/registry.json  游戏注册表（Tauri 按此发现游戏、解析引擎模块）
games/PROTOCOL.md    sidecar 协议：新游戏接入的契约
games/README.md      加一个新游戏的步骤
games/hearthstone/   炉石传说实现：引擎 + 套牌数据 + 测试
app/                 Tauri 通用控制台（游戏选择 / 对局 / 回放 / 配置 / 日志）
```

加新游戏不需要改 Tauri 代码：在 `games/<id>/` 实现引擎（sidecar 协议
见 `games/PROTOCOL.md`），在 `registry.json` 注册，控制台的游戏下拉框
自动出现，配置按游戏分别保存。

## 架构

```
Power.log ──tail──> LogParser ──> GameState ──> Decision ──> Action ──> Executor
                        (log_parser)  (state)    (decision)  (actions)   (dry-run / pyautogui / Pico HID)

Tauri 控制台 (app/) ── JSON-lines stdio ──> hsbot.sidecar ──> Bot
   对局 / 回放 / 配置 / 日志                (state/decision/action 事件)
```

炉石引擎模块（`games/hearthstone/hsbot/`）：

- `deckstring.py` — 套牌代码解码（已用本套牌代码验证：狂野，30 张主牌，副牌 4 条）。
- `log_parser.py` — 增量解析 Power.log（TAG_CHANGE / FULL_ENTITY / SHOW_ENTITY / HIDE_ENTITY），折叠成 GameState；换牌回执（离开 BEGIN_MULLIGAN 时记录换牌后手牌）+ 对局结束/胜负（FINAL_GAMEOVER / PLAYSTATE）。
- `state.py` — 实体、英雄、手牌、场面、费用、奥秘；对局结束 `game_over` / `game_result` / `mulligan_receipt`。
- `decision.py` — 换牌表 + 斩杀检测优先（要求全部步骤 exact）+ 回合内全序列 Beam Search（宽 8，深 14）+ 启发式评分；`decide_turn_traced` 输出 veto/prefer trace；支持 `exclude`（本回合被 ban 的动作）。
- `deck_config.py` — 套牌配置：`load_deck(deck_id)` 读 `decks/<deck-id>/`（`deck.json` + `mulligan.json` + `strategy.md`）；`deck_data.py` 是薄兼容层（deprecated）。
- `policy.py` — veto（硬删，END_TURN 永不删）+ prefer（软收窄，永不清空菜单）+ 释放机制（margin 1.0）。
- `verify.py` — 点击后核对：`verify_action(before, action, after) -> OK | MISFIRE | PENDING`，按动作类型判定预期变化。
- `masking.py` — 隐私遮罩：`public_view(state)` 只含公开信息（对手手牌/牌库/奥秘只给数量，永不含 BattleTag / 原始日志）。
- `engine_version.py` — 版本化引擎：`ENGINE_VERSION = "v2"`；`get_engine` 未知版本抛错，永不静默回退。
- `actions.py` — 动作 → 屏幕坐标（`Layout.auto_detect()` 窗口自适应，找不到回退 1920×1080）→ 执行器；`cancel_drag()` 右键清除卡住的拖拽。
- `bot.py` — 主循环：tail 日志 → 换牌 → 每回合逐动作决策 → 执行 → 日志核对（OK 继续 / MISFIRE 重试一次后 ban / 超时按 dead 处理；8 连 dead 或 3 连 misfire 停机）；dry-run 走整计划执行（无核对）。
- `runner.py` — 无人值守批次：局数上限、连败停机（默认 6）、stop 文件、执行器故障、解析器静默、结果不可检出时只跑单局；每局结果写 `runtime/runs/<ts>.jsonl`。
- `sidecar.py` — Tauri sidecar：JSON-lines 协议（start/stop/pause/resume/config），stdout 只走协议，`print` 已静默；可选 `runner` 配置；无新日志行时仍调 `tick()` 以触发核对超时。
- `replay.py` — 离线回放：喂 Power.log，输出快照数组（含每回合的 bot 计划）。
- `eval/` — 离线 A/B 评测：冻结局面 `positions.jsonl` + 人工标签 `labels.json` + `eval_score.py`（1/0.5/0 打分）+ `EVAL.md`。

阈值说明：8 连 dead、3 连 misfire、6 连败、~1.2s settle 是参考项目
经验的**起点，未在实机上验证**，实机部署前必须重测，不得当成已验证值。

## Tauri 控制台（app/）

```bash
cd app
npm install
npx tauri dev      # 开发运行
npx tauri build    # 打包
```

前置要求：Rust 工具链（`rustup`）、Python 3.10+、Node 18+。
Linux 构建还需要系统库：`libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev`。
macOS / Windows 不需要这些（分别用 WKWebView / WebView2）。

引擎以 `python3 -m <sidecar 模块>` 方式作为 sidecar 运行，工作目录为
打包后的资源目录下该游戏的 `dir`（炉石是 `games/hearthstone`，内含
`hsbot/`、`data/`、`samples/`）。
前端在浏览器里打开也能看演示状态（不连引擎）。

## 关键设计：卡牌数据是外部数据，不是代码

引擎不硬编码任何卡牌效果。卡牌数据分三层：

1. **HearthstoneJSON**（推荐）：下载 `cards.collectible.json`
   https://hearthstonejson.com/ ，用 `--cards-json` 传入，得到精确的
   费用/攻击/血量/关键词。
2. **games/hearthstone/data/deck.json**（兜底）：只有套牌导出给出的名字/费用/数量，
   `type`/`race` 带 `_inferred` 标记的是按卡名推断的，attack/health 未知。
3. **games/hearthstone/data/overrides.json**（待补充）：结构化效果，如
   `{"恶火": {"effect_damage": 3, "effect_unknown": false}}`。
   效果未知的法术引擎**不会**在搜索里打出（不赌）。

没有精确卡牌数据时，引擎退化为：换牌（按名字）+ 随从按费拍 + 斩杀
（随从伤害）+ 攻击，能跑，但打不出法术配合。

## 运行

```bash
cd games/hearthstone

# 干跑：用示例日志验证整条链路，不碰鼠标
python -m hsbot.bot --log samples/sample_power.log --dry-run --once

# 实机（Windows/macOS，游戏运行中）：
# 1. 写 log.config 开 Power 日志（见下）
# 2. python -m hsbot.bot --log <Power.log 路径> --cards-json <cards.collectible.json>

# 回放：
python -m hsbot.replay --log <Power.log 路径>
```

`log.config`（放到游戏目录，内容示例）：

```
[Power]
LogLevel=1
FilePrinting=true
ConsolePrinting=false
ScreenPrinting=false
Verbose=true
```

日志路径：Windows `<Hearthstone>/Logs/Power.log`；
macOS `~/Library/Logs/Blizzard/Hearthstone/Power.log`。

## 测试

```bash
cd games/hearthstone
python3 tests/test_bot.py       # 套牌解码 + 日志解析 + 换牌 + 斩杀 + 回合决策 + 干跑
python3 tests/test_sidecar.py   # sidecar 协议 + 离线回放
python3 tests/test_phase_a.py   # exact 标记 + 套牌配置 + 隐私遮罩 + 版本化引擎 + 评测 harness
python3 tests/test_phase_b.py   # veto/prefer 两层规则 + 释放机制 + trace
python3 tests/test_phase_c.py   # 点击后核对 + 逐动作循环 + Runner 停机条件
```

## 上线前必须做的事

1. **验证 Layout 自适应**：`Layout.auto_detect()` 会自动找游戏窗口
   （位置 + 尺寸，16:9 信箱式适配），找不到时回退 1920×1080 参考值。
   实机上先跑一局 dry-run，核对坐标是否落在牌/按钮上；窗口标题、
   DPI 或非 16:9 导致偏差时，传入显式 `Layout(...)` 覆盖。
2. **补卡牌数据**：下载 HearthstoneJSON + 写 `games/hearthstone/data/overrides.json`
  （本套牌法术：雷达探测 / 十字军光环 / 恶火 等的效果）。
3. **换牌确认点击**：`bot._do_mulligan` 目前只发事件（含期望保留牌名），
   未接真实点击（换牌界面卡牌坐标需实机校准）；`verify.py` 的 MULLIGAN
   判定 + 解析器的换牌回执已就绪，等坐标校准后接入。
4. **核对阈值实机重测**：逐动作核对闭环已落地（`verify.py` + `bot.py`
   逐动作循环 + `runner.py`），但 settle（~1.2s）、verify 超时（10s）、
   dead（8 连）、misfire（3 连）均为未验证起点；实机上需按动画时长、
   网络延迟重测。
5. **评估函数调参**：`decision.WEIGHTS` 是初版，自对弈 + 实战回放调。
   已知评测差距：`overdraw-10` 局面 v1/v2 均错选（权重校准问题，
   不是 bug；不得为迎合标签调权重）。

## 明确不做（风险）

- 暴雪对自动对战是封号的。建议先跑「辅助提示」模式（只算不点），
  验证决策质量；自动模式要加局数上限、人化延迟、非 24 小时运行。
- 本引擎只读 Power.log + 模拟点击，不注入、不读内存。
- 不接 Jev / Clef 等外部决策模型（需 API key + 花费批准，未获批准）。
- 未在真实客户端验证过：Layout 自适应、阈值、换牌回执、胜负解析
  均只通过单元测试 + 离线回放，不得声称已实机验证。

## 下一步

- 评测 harness 已落地（`eval/`）；下一步是喂真实回放扩充局面 + 权重校准。
- 新游戏（原神等）：新增 `games/<游戏>/`，复用 DecisionProvider / Executor 抽象。
