# 进度

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

完成一项任务后更新这里；里程碑的范围有变化时，先更新 [DESIGN.md](DESIGN.md) 并让人确认。

最后更新：2026-11-10

## 总览

| 里程碑 | 状态 |
|---|---|
| M0 项目骨架 | 完成 |
| M1 资源类型和样本 | 完成（两批共 15 个类型） |
| M2 本地模拟环境 | 完成（Config 由 fake_aws 提供） |
| M3 worker 核心 | 完成 |
| M4 数据源 | 完成（①②的接口格式是假设） |
| M5 API | 完成 |
| M6 Dashboard | 完成 |
| M7 部署 | 完成（待平台创建资源后实际验证） |
| M8 上线确认 | 未开始 |

**MVP 已实现**：两批共 15 个资源类型、完整的扫描和写入流程、API、dashboard、本地环境和部署文件。后端 315 个测试通过（`cd backend && uv run pytest`，需要 `podman compose up -d postgres redis`），Playwright 端到端测试 6 个通过（`cd frontend && npm run e2e`，需要本地栈），CI 工作流（`.github/workflows/ci.yml`）已在 GitHub 上通过。剩下的是真实环境的确认（见 M1、M8）。

设计阶段已完成：[DESIGN.md](DESIGN.md)、[DECISIONS.md](DECISIONS.md)、[specs/](specs/)。实现中发现的偏差已经同步到这些文档（DECISIONS D-21 起）。

---

## M0 项目骨架
- [x] `backend/`：pyproject（uv、Ruff、ty、`[tool.fastapi]` entrypoint）、`app/` 目录结构、Settings
- [x] `frontend/`：Vite + React 19 + TanStack + shadcn/ui + Tailwind v4 + Biome，hey-api 客户端生成脚本（`backend/scripts/export_openapi.py` + `npm run generate-client`）
- [x] `backend/Dockerfile`：多阶段构建，非 root，健康检查
- [x] `compose.yaml`：postgres、redis、moto、fake-aws、migrate、api、worker、seed
- [x] 代码检查：Ruff、ty、Biome 都已配置并可以在本地运行；`.github/workflows/ci.yml` 跑后端、devtools、前端检查和 e2e（用 actionlint 和 YAML 解析检查过语法，还没有在 GitHub 上实际运行）

## M1 资源类型和样本
- [x] `types/spec.py`：资源类型配置的 Pydantic 模型，格式见 [specs/resource-types.md](specs/resource-types.md)
- [x] `types/registry.py`、`types/mapping.py`、`types/redact.py`、`types/hooks.py`（另有 `types/columns.py`）
- [x] 第一批类型的配置文件和样本：VPC、Subnet、ALB/NLB、ECS Cluster / Service / Task Definition、EC2、AMI、Lambda、Secret、SNS、SQS（12 个）
- [x] 第二批类型的配置文件和样本：Security Group、Target Group（Config 查不到，只走 Cloud Control，没有 Config 样本）、Route 53 Hosted Zone（全局类型，区域记为 `global`，在 us-east-1 读取；端到端测试覆盖了定时扫描和按需扫描下的写入与删除判断）。核对结果见 `backend/tests/fixtures/aws/VERIFICATION.md` 第 14–16 条
- [x] 样本包括：Config 配置项、Cloud Control `GetResource` / `ListResources` 响应（AMI 是 EC2 `DescribeImages` 响应），以及 `SOURCE.md`（只用 AWS 官方资料）（第一批）
- [x] 核对每个类型的 Cloud Control Identifier 和 Config 支持情况：15 个类型都核对过（结果和差异见 `VERIFICATION.md`，已同步到 [specs/resource-types.md](specs/resource-types.md) 第 5 节）
- [x] 测试：每个类型的映射、脱敏、列取值（第一批）

## M2 本地模拟环境
- [x] `devtools/fake_aws`：Cloud Control `ListResources` / `GetResource`；账号清单服务 ①；凭证服务 ②（调用 moto STS `AssumeRole`）；**还有 AWS Config**（moto 的服务器模式预设不了 `SelectResourceConfig`，见 D-21）和 `/_admin` 管理接口
- [x] Config 预设：记录器状态、`SelectResourceConfig` 结果（由 fake_aws 提供，不是 moto）
- [x] `devtools/seed.py`：20 个账号 × 4 个区域的演示数据（只改账号、区域、资源 ID），并在 moto 里注册 AMI
- [x] `podman compose up` 后可以完成一轮完整扫描：2026-11-09 在 compose 的 api / worker 容器上实测，`POST /scans {all: true}` 90 秒完成（20 个账号、740 个任务全部成功、入库 1,082 个资源）；在 fake-aws 里删掉一个 SQS 队列后按需扫描，资源先变为「可能已删除」再确认为「已删除」；`doctor` 16/16 通过，worker `healthcheck` 通过。端到端测试也覆盖了同样的流程。已知限制：moto 里共享 AMI 跨账号不可见，任务日志本地查不到

## M3 worker 核心
- [x] 7 张表的 SQLModel 模型和 Alembic 迁移（放在 `DATABASE_SCHEMA`）：0001 初始表，0002 围栏令牌和运行中任务的索引，0003 `candidate_count`
- [x] 任务队列：领取、心跳、完成、重新排队、取消，见 [specs/scanner.md](specs/scanner.md)（含围栏令牌、共用任务的取消语义）
- [x] 范围租约、合并、跳过刚扫过的范围（R1–R3）
- [x] `ResourceWriter`：加锁 → 叠加 → 脱敏 → 内容哈希 → 有条件写入（R4）→ 关系表
- [x] 删除判断：可能已删除、两条证据（`absent_in_scan_at` + 按 ID 读取明确不存在）、整批保护（R5–R6）
- [x] `Discovery`：选数据源、产出 `Observation`、标记读取是否完整、区分「不存在」和「出错」；`TYPE_HOOKS` 的默认实现
- [x] 调度：一轮扫描的创建、按账号错开、advisory lock 选主、全量扫描创建串行化
- [x] 优雅退出；清理任务
- [x] 测试：DESIGN.md 7.1 的 A–H 每种情况（另有围栏令牌、取消、优雅退出、访问状态并发）

## M4 数据源
- [x] 凭证提供器（`RefreshableCredentials` + 凭证服务 ②）
- [x] 账号清单客户端 ①；账号快照更新、不活跃账号标记
- [x] 区域发现（`ec2:DescribeRegions`，在第一个默认开启的可达区域调用）和 `REACHABLE_REGIONS`
- [x] Config：记录器检测、`SelectResourceConfig` 分页读取
- [x] Cloud Control：`ListResources`、`GetResource`（目前没有类型需要「上级资源」的特殊处理，见 D-24）
- [x] 服务 API 数据源：AMI（`DescribeImages`，自有 + 共享）
- [x] 权限状态更新（`access_status`）；错误分类
- [x] 测试：扫描 → 写入 → 查询的完整流程

## M5 API
- [x] 资源：列表、分类计数、导出（CSV / JSON Lines）、按 ARN 查询、详情、关系、刷新
- [x] 列定义、资源类型、概览
- [x] 账号：列表、详情、扫描状态格子
- [x] 扫描：摘要、发起（含 `Idempotency-Key`）、记录、详情、任务、重试、取消、任务日志、异常范围
- [x] 健康检查；统一错误格式；Redis 缓存
- [x] 测试：主要接口的行为

## M6 Dashboard
- [x] 三套配色方案（中国传统色，暖灰 + 一个主色）：黛绿、黛蓝、胭脂，`?palette=` 切换；最终保留哪套还没有定
- [x] 数据表格组件：根据列定义生成列和筛选控件，服务端筛选、排序、分页
- [x] 页面：概览、资源列表、资源详情、账号、扫描（含扫描详情、任务详情），见 [specs/dashboard.md](specs/dashboard.md)
- [x] 中文和英文；亮色和暗色
- [x] Playwright 主要流程测试：`frontend/e2e/`，6 个流程（概览、资源筛选和导出、资源详情和刷新、发起扫描、账号覆盖表、语言和主题），`npm run e2e`；本地数据没有关联资源，关联资源链接只在有数据时才点

## M7 部署
- [x] `deploy/ecs/`：api、worker、migrate 的任务定义示例
- [x] [deploy/CHECKLIST.md](../deploy/CHECKLIST.md) 按实现结果更新（密钥是 JSON 对象、worker 健康检查、`/tmp` 可写、`DescribeRegions` 的区域）
- [x] `doctor` 命令：检查数据库（含迁移版本）、Redis、两个内部服务和各区域 endpoint 的连通性；另有 `healthcheck` 命令给 worker 容器用

## M8 上线确认
- [ ] 账号清单服务 ① 的真实接口格式，调整客户端（现在是假设的 JSON 数组，见 `aws/account_registry.py` 的 `RegistryAccount`）
- [ ] 凭证服务 ② 的真实接口格式，调整客户端（现在假设返回 STS 格式的 JSON，见 `aws/credentials.py`）
- [ ] 用一个真实账号抓取每个类型的数据，修正样本和映射（现在的样本都来自 AWS 官方文档）
- [ ] 平台按部署清单创建资源，`doctor` 检查通过
- [ ] 必填标签列表（如果使用「缺少某个标签」的快捷筛选）
