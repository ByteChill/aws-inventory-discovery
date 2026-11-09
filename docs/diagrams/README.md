# 架构图

用 [Archify](https://archify.si) 生成的可交互 HTML，在浏览器里打开。

| 图 | 内容 |
|---|---|
| [architecture.html](architecture.html) | 部署架构：两个 ECS 服务、数据存储、网络和三种数据源 |
| [scan-flow.html](scan-flow.html) | 扫描流程：调度 → 租约 → Discovery 读取 → Writer 写入 → 删除判断和确认 |
| [resource-lifecycle.html](resource-lifecycle.html) | 资源状态变化：正常 → 可能已删除 → 已删除，以及两条证据的规则 |
| [database.html](database.html) | 数据库结构：7 张 `t_inv_aws_*` 表 |
| [worker-loops.html](worker-loops.html) | worker 的三个循环：领任务（每个线程）、心跳（每 30 秒）、调度（每 60 秒，只有抢到 advisory lock 的实例）；为什么不需要 cron |
| [sweep-timeline.html](sweep-timeline.html) | 一轮全量扫描的时间线（示意）：账号在 5.5 小时窗口里错开开始、手动扫描不重置节奏、新账号在下一轮加入、处理不过来时会怎样 |
| [concurrency-rules.html](concurrency-rules.html) | 并发规则（时序图）：worker 卡住被回收后执行令牌怎么挡住它；列表扫描的旧数据为什么覆盖不了刷新写入的新数据 |

## 修改

源文件在 [src/](src/)。修改后在仓库根目录重新生成（需要已安装 Archify）：

```bash
mkdir -p .archify/diagrams && cp docs/diagrams/src/architecture.json .archify/diagrams/
```

```bash
node ~/.claude/skills/archify/bin/archify.mjs finalize architecture .archify/diagrams/architecture.json .archify/diagrams/architecture.html --quality showcase --json
```

```bash
cp .archify/diagrams/architecture.html docs/diagrams/ && cp .archify/diagrams/architecture.json docs/diagrams/src/
```

其他图同理：`scan-flow`、`worker-loops` 是 `workflow`，`resource-lifecycle` 是 `lifecycle`，`sweep-timeline` 是 `timeline`，`concurrency-rules` 是 `sequence`，`database` 是 `erd`（用 `--quality standard`）。重新生成时如果提示已有的浏览器检查结果文件冲突，加 `--out-dir .archify/diagrams/review-<n>` 换一个目录。图的内容必须和 [DESIGN.md](../DESIGN.md) 保持一致，设计变化时同步更新。
