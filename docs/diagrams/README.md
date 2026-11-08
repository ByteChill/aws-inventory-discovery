# 架构图

用 [Archify](https://archify.si) 生成的可交互 HTML，在浏览器里打开。

| 图 | 内容 |
|---|---|
| [architecture.html](architecture.html) | 部署架构：两个 ECS 服务、数据存储、网络和三种数据源 |
| [scan-flow.html](scan-flow.html) | 扫描流程：调度 → 租约 → Discovery 读取 → Writer 写入 → 删除判断和确认 |
| [resource-lifecycle.html](resource-lifecycle.html) | 资源状态变化：正常 → 可能已删除 → 已删除，以及两条证据的规则 |
| [database.html](database.html) | 数据库结构：7 张 `t_inv_aws_*` 表 |

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

其他图同理：`scan-flow` 是 `workflow`，`resource-lifecycle` 是 `lifecycle`，`database` 是 `erd`（用 `--quality standard`）。图的内容必须和 [DESIGN.md](../DESIGN.md) 保持一致，设计变化时同步更新。
