# 进度

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

完成一项任务后更新这里；里程碑的范围有变化时，先更新 [DESIGN.md](DESIGN.md) 并让人确认。

最后更新：2026-11-14

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

**MVP1 完成，已合并到 main**：两批共 15 个资源类型、完整的扫描和写入流程、API、dashboard、本地环境和部署文件。后端 363 个测试通过（`cd backend && uv run pytest`，需要 `podman compose up -d postgres redis`），Playwright 端到端测试 11 个通过（`cd frontend && npm run e2e`，需要本地栈），CI 工作流（`.github/workflows/ci.yml`）已在 GitHub 上通过。剩下的是真实环境的确认（见 M1、M8）。

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
- [x] 7 张表的 SQLModel 模型和 Alembic 迁移（放在 `DATABASE_SCHEMA`）：0001 初始表，0002 围栏令牌和运行中任务的索引，0003 `candidate_count`，0004 按账号查排队任务的索引，0005 范围的 `id` 和 `candidate_ids`
- [x] 任务队列：领取、心跳、完成、重新排队、取消，见 [specs/scanner.md](specs/scanner.md)（含围栏令牌、共用任务的取消语义）
- [x] 范围租约、合并、跳过刚扫过的范围（R1–R3）
- [x] `ResourceWriter`：加锁 → 叠加 → 脱敏 → 内容哈希 → 有条件写入（R4）→ 关系表
- [x] 删除判断：可能已删除、两条证据（`absent_in_scan_at` + 按 ID 读取明确不存在）、整批保护（R5–R6）
- [x] `Discovery`：选数据源、产出 `Observation`、标记读取是否完整、区分「不存在」和「出错」；`TYPE_HOOKS` 的默认实现
- [x] 调度：一轮扫描的创建、按账号错开、advisory lock 选主、全量扫描创建串行化
- [x] 优雅退出；清理任务
- [x] 测试：DESIGN.md 7.1 的 A–H 每种情况（另有围栏令牌、取消、优雅退出、访问状态并发）
- [x] 上线前并发修复（2026-11-14）：资源写入、删除判断、统计、租约取得/续约/释放、区域与 recorder 保存、子任务和确认任务创建都在事务内核对 claim_token（先锁 shard 再锁 task）；清理锁定候选后复查保留期，重试改为 task → request 锁顺序并在取消胜出时回滚；Config 删除证据使用 configurationItemCaptureTime；用真实 handler 的「暂停 → 回收 → 重新领取」回归测试覆盖

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
- [x] 新 dashboard 需要的三个接口：`GET /applications`（按 EIM 汇总活跃账号、环境数和资源数）；资源列表、分类和导出的 `scanRequestId`（按请求里任务的已解析范围，取当前清单）；`GET /scan-scopes/{id}/candidates`（候选 ID 和 ARN 存在 `scan_shard.candidate_ids`，迁移 0005，范围恢复正常时清空）
- [x] 这三个接口的独立评审（Claude 一轮、DeepSeek V4.1 Flash 一轮，2026-11-13）发现的问题已修复：`config_group` 只覆盖 Config 能提供的类型；超过任务保留期的扫描改按请求记录的范围计算（D-33）；应用列表先分页再统计资源数，`total` 和列表按同一组计数；候选接口不再把整个候选列表读进进程；「已清理」候选的测试数据改为来自样本。迁移 0005 之前已经异常的范围没有候选明细，已写进 [specs/api.md](specs/api.md)
- [x] 健康检查；统一错误格式；Redis 缓存
- [x] 缓存层（2026-11-14）：专用 ElastiCache for Valkey（D-34，见 deploy/CHECKLIST.md 第 2 节）；api 进程内共享一个 Redis 客户端；分类计数按字段缓存，筛选后的资源计数由列表 `total` 和 `/resources/summary` 共用，账号、应用列表、概览统计和扫描页摘要进缓存；TTL 由 `CACHE_TTL_SECONDS` 控制（默认 60 秒，扫描摘要 10 秒）；缓存值形状不符或 Redis 不可用都当作未命中，`/health` 用同一个客户端探测
- [x] 测试：主要接口的行为

## M6 Dashboard
- [x] 静态 Dashboard 原型完成本轮交互验收与独立只读评审；修复搜索／登录菜单焦点冲突、扫描重绘丢失焦点及资源刷新结果串页问题。原型定稿为 `.oil-ui/dashboard-redesign/inventory.html`，已提交到仓库（说明见该目录的 `README.md`）；2026-11-13 确认按原型迁移正式前端。
- [x] 规格同步：重写 [specs/dashboard.md](specs/dashboard.md)；补充应用列表、扫描范围过滤、异常范围候选 ID、网关身份、胭脂配色和响应式检查（DESIGN、DECISIONS、api.md、scanner.md）。
- [x] 配色：胭脂（中国传统色，暖灰 + 一个主色），满足 WCAG AA；黛绿、黛蓝保留在设计记录里，界面不再切换
- [x] 数据表格组件：根据列定义生成列和筛选控件，服务端筛选、排序、分页
- [x] 页面：概览、资源列表、资源详情、账号、扫描（含扫描详情、任务详情），见 [specs/dashboard.md](specs/dashboard.md)
- [x] 中文和英文；亮色和暗色
- [x] Playwright 主要流程测试：`frontend/e2e/`，`npm run e2e`；本地数据没有关联资源，关联资源链接只在有数据时才点（第 6 阶段按新界面重写，见下）
- [x] 第 1 阶段·视觉基础和布局：只用胭脂配色、统一字号、侧栏（范围切换、服务树、扫描管理、只读说明）、顶栏（全局搜索、折叠胶囊）、范围参数 `s`、窄屏抽屉和路由骨架
- [x] 第 2 阶段·数据表格和筛选：资源表格的单行省略、首列固定、右缘渐隐、复制、异常标记；搜索、环境、区域、类型、清单状态筛选，「更多筛选」，CSV / JSON Lines 导出，空结果只说一句话
- [x] 第 3 阶段·概览、应用列表、资源列表和资源详情：概览按范围显示统计和按服务 / 应用 / 账号 / 环境 / 区域的分布（默认收起），下面是最近 5 条资源和完整列表入口；应用列表用统一表格组件（搜索、环境筛选、排序、整行进入应用范围）；服务页和分组页显示类型页签和数量，以及环境和区域胶囊；资源详情保留来源的返回链接，账号和应用都显示名称加编号
- [x] 第 4 阶段·账号、扫描、扫描详情、任务详情和待核对的范围：账号列表的筛选行（搜索、EIM、环境、访问状态、已移除的账号和清理倒计时）和整行进入；账号详情的扫描操作和概要；扫描管理的待核对范围表格（重新扫描、核对资源）和扫描记录筛选，结果数字只做统计，失败数链接到失败任务；扫描详情的任务筛选行；核对页的行预览、详情返回核对页和「确认是真实变化」
- [x] 第 5 阶段·文案和多语言：删去 29 个无引用的文案键，中英文键完全一致；用词对齐规格第 5 节；提示改成一句短话；英文改写成英文用户的说法，并在 1440 和 390 宽检查英文布局（标题按词换行，环境列放得下「Environment」）
- [x] 第 6 阶段·Playwright 端到端测试和各宽度检查：11 个测试（概览、资源筛选和导出、资源预览和详情、发起扫描、账号、待核对的范围、语言和主题，以及 1440 / 1920 / 1024 / 390 四个宽度：页面不横向溢出、表格在自己的框里滚动、800 px 以下菜单是抽屉、扫描表单不出屏）；待核对的范围在没有触发范围保护的栈里跳过
- [x] 按原型严格复核（2026-11-13）：概览去掉最近更新资源，全局范围常显按服务 / 应用 / 环境 / 区域的分布，应用和账号范围收进「资源分布」；服务页有类型页签、状态 / 环境 / 区域胶囊和上下文数量，分组页有统计和分布；应用、账号和账号扫描情况页补面包屑并统一摘要、扫描情况和区域表格；资源详情改为摘要加配置 / 关联资源 / 标识与记录 / 标签卡片，并保留「原始配置」和「原始实时数据」；资源列表保留「更多筛选 / 导出 / 列」工具栏；扫描管理、扫描详情、任务详情和待核对范围按原型重写（摘要、进度、任务筛选、日志搜索）；扫描页返回走面包屑和侧栏的范围入口；环境值统一显示为 dev / preprod / prod；端到端测试同步
- [x] 异常范围超过 200 个（2026-11-14）：扫描页的「待核对的范围」每页 50 个、可翻页；核对页直接打开时通过分页接口查找范围，不再只读列表的第一页；新增 Playwright 回归测试，用本地扫描出的 201 个范围验证翻页、直接链接、后续页失败重试和不存在状态（`frontend/e2e/scan-scopes.spec.ts`）。

## M7 部署
- [x] `deploy/ecs/`：api、worker、migrate 的任务定义示例
- [x] [deploy/CHECKLIST.md](../deploy/CHECKLIST.md) 按实现结果更新（密钥是 JSON 对象、worker 健康检查、`/tmp` 可写、`DescribeRegions` 的区域）
- [x] `doctor` 命令：检查数据库（含迁移版本）、Redis、两个内部服务和各区域 endpoint 的连通性；另有 `healthcheck` 命令给 worker 容器用
- [x] 挂在已有 ALB 的 `/aws-inventory` 下：路径由 Dockerfile 的构建参数 `BASE_PATH` 决定，前后端共用

## M8 上线确认
- [ ] 账号清单服务 ① 的真实接口格式，调整客户端（现在是假设的 JSON 数组，见 `aws/account_registry.py` 的 `RegistryAccount`）
- [ ] 凭证服务 ② 的真实接口格式，调整客户端（现在假设返回 STS 格式的 JSON，见 `aws/credentials.py`）
- [ ] 用一个真实账号抓取每个类型的数据，修正样本和映射（现在的样本都来自 AWS 官方文档）
- [ ] 平台按部署清单创建资源，`doctor` 检查通过
- [ ] 必填标签列表（如果使用「缺少某个标签」的快捷筛选）
