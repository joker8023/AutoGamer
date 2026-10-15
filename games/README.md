# Games

每个游戏一个目录，`games/<id>/`，通过 `registry.json` 注册到 AutoGamer。

## 加一个新游戏

1. 新建 `games/<id>/`，里面放该游戏的引擎实现。
2. 在 `games/registry.json` 里加一条：

```json
{
  "id": "genshin",
  "name": "原神",
  "dir": "games/genshin",
  "sidecar": "genshin.sidecar",
  "replay": "genshin.replay",
  "description": "一句话描述"
}
```

3. 引擎必须实现 `PROTOCOL.md` 里的 sidecar 协议（JSON-lines stdin/stdout）
   和 replay 模块（输出快照 JSON 数组）。Tauri 端不需要改代码：
   游戏选择下拉框自动出现新游戏，启动/回放按注册表解析。

## 字段说明

- `id`：`[a-z0-9_-]+`，唯一。Tauri 只认注册表里的 id，前端传来的
  未知 id 会被拒绝（防 `..` 目录穿越）。
- `dir`：相对于仓库根目录的引擎目录，不能含 `..`。
- `sidecar` / `replay`：Python 模块名，Tauri 以 `python3 -m <模块>`
  运行，工作目录 = `dir`。

## 约定

- 引擎 stdout 只走 JSON-lines 协议，`print` 调试信息必须关掉或走 stderr
  （炉石引擎用 `quiet=True`）。
- 每个游戏的配置字段可以不同；Tauri 配置表单目前展示通用字段
  （日志路径、干跑、延迟、搜索宽度），引擎忽略自己用不上的键。
