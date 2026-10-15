# Sidecar 协议（Tauri ↔ 游戏引擎）

每个游戏引擎都是一个 sidecar 子进程：Tauri 以
`python3 -m <sidecar 模块>` 启动它（工作目录 = 该游戏的引擎目录），
通过 stdin/stdout 传 JSON-lines（一行一个 JSON 对象）。

## 命令（Tauri → 引擎，stdin）

| 命令 | 说明 |
|---|---|
| `{"type": "start", "config": {...}}` | 开始 tail 日志 + 决策。`config` 含 `game` + 该游戏的配置字段 |
| `{"type": "stop"}` | 停止 |
| `{"type": "pause"}` / `{"type": "resume"}` | 暂停/继续决策（日志解析继续） |
| `{"type": "config", "config": {...}}` | 实时更新配置 |
| `{"type": "ping"}` | → `{"type": "pong"}` |
| `{"type": "status"}` | → `{"type": "status", "running": bool, "config": {...}}` |

## 事件（引擎 → Tauri，stdout）

| 事件 | 说明 |
|---|---|
| `{"type": "started", "config": {...}}` | 启动成功 |
| `{"type": "stopped"}` | 已停止 |
| `{"type": "state", "state": {...}}` | 游戏状态快照（只在有意义变化时发） |
| `{"type": "mulligan", ...}` | 换牌 / 开局决策 |
| `{"type": "decision", "turn": N, "actions": [...]}` | 本回合计划 |
| `{"type": "action", "turn": N, "action": "..."}` | 正在执行的操作 |
| `{"type": "paused"}` / `{"type": "resumed"}` | 暂停状态变化 |
| `{"type": "config", "config": {...}}` | 配置回显 |
| `{"type": "error", "message": "..."}` | 错误 |

规则：

- **stdout 只走协议**。`print` 调试必须关掉或写 stderr，否则会污染协议流。
- `state` 快照结构由各游戏自定，但必须包含 `turn` / `step` /
  `current_player` / `me` / `opp`（前端按这个渲染）；`me` / `opp` 含
  `hero_health` / `mana` / `max_mana` / `deck_count` / `hand[]` / `board[]`，
  卡片含 `id` / `name` / `cost` / `attack` / `health`。

## 回放（`python3 -m <replay 模块> --log <路径> [--beam-width N]`）

一次性进程，stdout 输出一个 JSON 数组，每个元素是一次状态快照
（同 `state` 事件的 `state` 结构），可在回合开始处附加 `plan`
（该回合 bot 会执行的动作列表）。Tauri 回放页用滑块逐步展示。
