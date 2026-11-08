# 进度

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

完成一项任务后更新这里；里程碑的范围有变化时，先更新 [DESIGN.md](DESIGN.md) 并让人确认。

最后更新：2026-11-08

## 总览

| 里程碑 | 状态 |
|---|---|
| M0 项目骨架 | 未开始 |
| M1 资源类型和样本 | 未开始 |
| M2 本地模拟环境 | 未开始 |
| M3 worker 核心 | 未开始 |
| M4 数据源 | 未开始 |
| M5 API | 未开始 |
| M6 Dashboard | 未开始 |
| M7 部署 | 未开始 |
| M8 上线确认 | 未开始 |

设计阶段已完成：[DESIGN.md](DESIGN.md)、[DECISIONS.md](DECISIONS.md)、[specs/](specs/)。

---

## M0 项目骨架
- [ ] `backend/`：pyproject（uv、Ruff、ty、`[tool.fastapi]` entrypoint）、`app/` 目录结构、Settings
- [ ] `frontend/`：Vite + React 19 + TanStack + shadcn/ui + Tailwind v4 + Biome，hey-api 客户端生成脚本
- [ ] `backend/Dockerfile`：多阶段构建，非 root，健康检查
- [ ] `compose.yaml`：postgres、redis、moto、fake-aws、api、worker
- [ ] 代码检查：Ruff、ty、Biome；在 CI 中运行

## M1 资源类型和样本
- [ ] `types/spec.py`：资源类型配置的 Pydantic 模型，格式见 [specs/resource-types.md](specs/resource-types.md)
- [ ] `types/registry.py`、`types/mapping.py`、`types/redact.py`、`types/hooks.py`
- [ ] 第一批类型的配置文件和样本：VPC、Subnet、ALB/NLB、ECS Cluster / Service / Task Definition、EC2、AMI、Lambda、Secret、SNS、SQS
- [ ] 第二批类型的配置文件和样本：Security Group、Target Group、Route 53 Hosted Zone
- [ ] 样本包括：Config 配置项、Cloud Control `GetResource` / `ListResources` 响应（AMI 是 EC2 `DescribeImages` 响应），以及 `SOURCE.md`（只用 AWS 官方资料）
- [ ] 核对每个类型的 Cloud Control Identifier 和 Config 支持情况（[specs/resource-types.md](specs/resource-types.md) 中标为「待核对」的项）
- [ ] 测试：每个类型的映射、脱敏、列取值

## M2 本地模拟环境
- [ ] `devtools/fake_aws`：Cloud Control `ListResources` / `GetResource`；账号清单服务 ①；凭证服务 ②（调用 moto STS `AssumeRole`）
- [ ] moto 的 Config 预设：记录器状态、`SelectResourceConfig` 结果
- [ ] `devtools/seed.py`：20 个账号 × 4 个区域的演示数据（只改账号、区域、资源 ID）
- [ ] `podman compose up` 后可以完成一轮完整扫描

## M3 worker 核心
- [ ] 7 张表的 SQLModel 模型和 Alembic 初始迁移（放在 `DATABASE_SCHEMA`）
- [ ] 任务队列：领取、心跳、完成、重新排队、取消，见 [specs/scanner.md](specs/scanner.md)
- [ ] 范围租约、合并、跳过刚扫过的范围（R1–R3）
- [ ] `ResourceWriter`：加锁 → 叠加 → 脱敏 → 内容哈希 → 有条件写入（R4）→ 关系表
- [ ] 删除判断：可能已删除、两条证据（`absent_in_scan_at` + 按 ID 读取明确不存在）、整批保护（R5–R6）
- [ ] `Discovery`：选数据源、产出 `Observation`、标记读取是否完整、区分「不存在」和「出错」；`TYPE_HOOKS` 的默认实现
- [ ] 调度：一轮扫描的创建、按账号错开、advisory lock 选主
- [ ] 优雅退出；清理任务
- [ ] 测试：DESIGN.md 7.1 的 A–H 每种情况

## M4 数据源
- [ ] 凭证提供器（`RefreshableCredentials` + 凭证服务 ②）
- [ ] 账号清单客户端 ①；账号快照更新、不活跃账号标记
- [ ] 区域发现（`ec2:DescribeRegions`）和 `REACHABLE_REGIONS`
- [ ] Config：记录器检测、`SelectResourceConfig` 分页读取
- [ ] Cloud Control：`ListResources`（含需要上级资源的类型）、`GetResource`
- [ ] 服务 API 数据源：AMI（`DescribeImages`，自有 + 共享）
- [ ] 权限状态更新（`access_status`）；错误分类
- [ ] 测试：扫描 → 写入 → 查询的完整流程

## M5 API
- [ ] 资源：列表、分类计数、导出（CSV / JSON Lines）、按 ARN 查询、详情、关系、刷新
- [ ] 列定义、资源类型、概览
- [ ] 账号：列表、详情、扫描状态格子
- [ ] 扫描：摘要、发起（含 `Idempotency-Key`）、记录、详情、任务、重试、取消、任务日志、异常范围
- [ ] 健康检查；统一错误格式；Redis 缓存
- [ ] 测试：主要接口的行为

## M6 Dashboard
- [ ] 三套配色方案（中国传统色，暖灰 + 一个主色）供选择
- [ ] 数据表格组件：根据列定义生成列和筛选控件，服务端筛选、排序、分页
- [ ] 页面：概览、资源列表、资源详情、账号、扫描，见 [specs/dashboard.md](specs/dashboard.md)
- [ ] 中文和英文；亮色和暗色
- [ ] Playwright 主要流程测试

## M7 部署
- [ ] `deploy/ecs/`：api、worker、migrate 的任务定义示例
- [ ] [deploy/CHECKLIST.md](../deploy/CHECKLIST.md) 按实现结果更新
- [ ] `doctor` 命令：检查数据库、Redis、两个内部服务和各区域 endpoint 的连通性

## M8 上线确认
- [ ] 账号清单服务 ① 的真实接口格式，调整客户端
- [ ] 凭证服务 ② 的真实接口格式，调整客户端
- [ ] 用一个真实账号抓取每个类型的数据，修正样本和映射
- [ ] 平台按部署清单创建资源，`doctor` 检查通过
- [ ] 必填标签列表（如果使用「缺少某个标签」的快捷筛选）
