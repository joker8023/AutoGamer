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
- `log_parser.py` — 增量解析 Power.log（TAG_CHANGE / FULL_ENTITY / SHOW_ENTITY / HIDE_ENTITY），折叠成 GameState。
- `state.py` — 实体、英雄、手牌、场面、费用、奥秘。
- `decision.py` — 换牌表 + 斩杀检测优先 + 回合内全序列 Beam Search（宽 8，深 14）+ 启发式评分。
- `actions.py` — 动作 → 屏幕坐标（1920×1080 参考布局，**上线前必须校准**）→ 执行器。
- `bot.py` — 主循环：tail 日志 → 换牌 → 每回合决策 → 执行（带人化随机延迟）。
- `sidecar.py` — Tauri sidecar：JSON-lines 协议（start/stop/pause/resume/config），stdout 只走协议，`print` 已静默。
- `replay.py` — 离线回放：喂 Power.log，输出快照数组（含每回合的 bot 计划）。

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
```

## 上线前必须做的事

1. **校准 Layout**：`actions.Layout` 的坐标是 1920×1080 参考值，实机上
   窗口位置/分辨率/DPI 不同，必须先跑一局只看 dry-run 坐标，逐个核对。
2. **补卡牌数据**：下载 HearthstoneJSON + 写 `games/hearthstone/data/overrides.json`
  （本套牌法术：雷达探测 / 十字军光环 / 恶火 等的效果）。
3. **换牌确认点击**：`bot._do_mulligan` 目前只打印，确认坐标后接上执行器。
4. **动画等待**：实机上每次操作后要等动画播完、读日志确认状态变化
   再下一步（目前 dry-run 无此等待）。
5. **评估函数调参**：`decision.WEIGHTS` 是初版，自对弈 + 实战回放调。

## 明确不做（风险）

- 暴雪对自动对战是封号的。建议先跑「辅助提示」模式（只算不点），
  验证决策质量；自动模式要加局数上限、人化延迟、非 24 小时运行。
- 本引擎只读 Power.log + 模拟点击，不注入、不读内存。

## 下一步

- 评测 harness：回放实战日志，离线重算最优解，找系统性错误。
- 新游戏（原神等）：新增 `games/<游戏>/`，复用 DecisionProvider / Executor 抽象。
