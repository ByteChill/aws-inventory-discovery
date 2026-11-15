# 进度

状态标记：`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

完成一项任务后更新这里；里程碑的范围有变化时，先更新 [DESIGN.md](DESIGN.md) 并让人确认。

最后更新：2026-11-16

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
| M9 报表（v2） | 进行中（功能完成，等待合并前的独立评审） |

**MVP1 完成，已合并到 main**：两批共 15 个资源类型、完整的扫描和写入流程、API、dashboard、本地环境和部署文件。后端 495 个测试通过（`cd backend && uv run pytest`，需要 `podman compose up -d postgres redis`），Playwright 端到端测试 15 个（14 个通过，1 个按设计跳过）（`cd frontend && npm run e2e`，需要本地栈），CI 工作流（`.github/workflows/ci.yml`）已在 GitHub 上通过。剩下的是真实环境的确认（见 M1、M8）。

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
- [x] 10 张表的 SQLModel 模型和 Alembic 迁移（放在 `DATABASE_SCHEMA`）：0001 初始表（含关系表），0002 围栏令牌和运行中任务的索引，0003 `candidate_count`，0004 按账号查排队任务的索引，0005 范围的 `id` 和 `candidate_ids`，0006 报表，0007 findings 汇总，账号扫描策略（`87b6f3ba8251`）
- [x] 任务队列：领取、心跳、完成、重新排队、取消，见 [specs/scanner.md](specs/scanner.md)（含围栏令牌、共用任务的取消语义）
- [x] 范围租约、合并、跳过刚扫过的范围（R1–R3）
- [x] `ResourceWriter`：加锁 → 叠加 → 脱敏 → 内容哈希 → 有条件写入（R4）→ 关系表
- [x] 删除判断：可能已删除、两条证据（`absent_in_scan_at` + 按 ID 读取明确不存在）、整批保护（R5–R6）
- [x] `Discovery`：选数据源、产出 `Observation`、标记读取是否完整、区分「不存在」和「出错」；`TYPE_HOOKS` 的默认实现
- [x] 调度：一轮扫描的创建、按账号错开、advisory lock 选主、全量扫描创建串行化
- [x] 优雅退出；清理任务
- [x] 测试：DESIGN.md 7.1 的 A–H 每种情况（另有围栏令牌、取消、优雅退出、访问状态并发）
- [x] 上线前并发修复（2026-11-14）：资源写入、删除判断、统计、租约取得/续约/释放、区域与 recorder 保存、子任务和确认任务创建都在事务内核对 claim_token（先锁 shard 再锁 task）；清理锁定候选后复查保留期，重试改为 task → request 锁顺序并在取消胜出时回滚；Config 删除证据使用 configurationItemCaptureTime；用真实 handler 的「暂停 → 回收 → 重新领取」回归测试覆盖
- [x] 上线前清理修复（2026-11-15）：不活跃账号清理与账号写入共用按账号的 advisory lock，锁内复查资格；资源保留清理在删除语句外层重复判断状态和保留期，写入方恢复的资源不会被删；新增 3 个真实 Postgres 并发回归测试
- [x] 上线前日志安全（2026-11-15）：Config 记录、Cloud Control 标签校验失败、捕获时间不合法或字段转换出错时，任务 `error_message` 和日志都不带原始值；新增 4 个用官方样本变形的回归测试
- [x] 账号主数据和扫描范围（2026-11-16）：本服务成为账号主数据 owner，账号表增加 `source`、`scan_enabled`、`scan_region_mode`、`updated_at`，新增 `t_inv_aws_account_scan_region`；迁移把存量账号回填为 `legacy` / 纳入扫描 / 所有区域，并允许本地账号的 `registry_fetched_at` 为空。清理只处理已删除且超过保留期的账号，停止扫描保留全部数据
- [x] 关系表增强（2026-11-15）：类型配置可以显式声明 `derivedRelationships`，支持 `[]` 数组路径和按 `resourceId`/ARN 解析目标；Writer 每次写入按当前 `configuration` 重算派生关系（Config 关系优先、同一实际目标去重、字段消失时删除旧边）；登记 ECS Service → TaskDefinition / Cluster / Target Group、EC2 Instance → Subnet / Image / VPC、Subnet / SecurityGroup / Target Group → VPC、Target Group → Load Balancer、Lambda → Subnet / SecurityGroup、Secret → Rotation Lambda
- [x] 关系边界复核（2026-11-15）：迟到解析改为写入提交后先锁来源资源，再更新关系，复查 claim token；覆盖双方并发写入、ARN 后补、同范围歧义、跨账号/区域隔离、数组移除和旧观测重放。未上线，不保留兼容迁移或回填命令；目标字段直接进入初始 schema。后端 386 项、devtools 10 项及 Ruff / ty 通过；本地重建数据库和扫描后，60 个 ECS Service、60 个 Lambda、120 个 Target Group 均有关系。
- [x] 关系增强本地验收（2026-11-15）：前端 TypeScript / Biome / build 通过，Playwright 12 项通过、1 项按设计跳过；更新镜像后重扫 880 个任务全部成功，未产生资源新增、变更或删除；资源 JSON 与关系表数量一致，已解析目标无重复边。
- [x] 关系增强迟到评审修复（2026-11-15）：评审复现了 active/missing 混合范围与关系重查的死锁、OR 入边查询的全表扫描、初次已解析出边在目标 ARN 并发变更后的错误关联，以及 ARN 歧义通过候选目标改 ARN 或删除后未恢复的问题。已统一锁定删除步骤会更新的全部行，拆分入边索引查询，提交后重查本批全部来源，删除确认后按目标重查；删除提交后丢失 claim 时，重新领取的确认任务按 `last_task_id` 分批补做关系重查。并发回归测试通过。20 万资源、10 万关系的 1/100/500 个目标查询计划均使用索引；完整后端 397 项、Writer 38 项、devtools 10 项、Ruff / ty、前端 lint / build 通过，Playwright 12 项通过、1 项按设计跳过；最终镜像完整扫描 883 个任务成功。

## M4 数据源
- [x] 凭证提供器（`RefreshableCredentials` + 凭证服务 ②）
- [x] 账号清单客户端 ①；现在只作为一次性导入源：仅插入本地不存在的账号，不覆盖本地字段、不改扫描策略、不把清单外账号标为不活跃；① 不可用不影响启动、扫描或 `doctor`
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
- [x] 账号主数据与扫描范围 API（2026-11-16）：新增、修改、软删除、恢复、按账号 ID 或 EIM/应用/环境等条件批量改策略、一次导入 ①、区域选项、生效范围预览；列表/详情返回 `scanEnabled`、`scanRegionMode` 和每个区域的「已选择 / 账号未开启 / 网络不可达 / 待确认」状态。全量扫描只取 `active AND scan_enabled`；按需扫描默认遵守策略，`overrideScanPolicy` 只能显式覆盖；已停止的账号即使有排队任务也不会创建区域任务，不产生 missing/deleted
- [x] 扫描：摘要、发起（含 `Idempotency-Key`）、记录、详情、任务、重试、取消、任务日志、异常范围
- [x] `GET /scan-scopes/{id}`（2026-11-15）：核对页按 ID 读取范围，列表分页变化不会让直接链接误报不存在；确认时带上看到的检测时间，有新检测时返回 409 `scope_changed`
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
- [x] 第 6 阶段·Playwright 端到端测试和各宽度检查：13 个测试（概览、资源筛选和导出、资源预览和详情、发起扫描、账号、待核对的范围、异常范围超过 200 个、语言和主题，以及 1440 / 1920 / 1024 / 390 四个宽度：页面不横向溢出、表格在自己的框里滚动、800 px 以下菜单是抽屉、扫描表单不出屏）；待核对的范围在没有触发范围保护的栈里跳过
- [x] 账号管理界面（2026-11-16）：账号列表增加「纳入扫描」和操作列、批量选择/改策略、新增、编辑、删除确认和恢复；详情增加扫描策略卡片（账号开关、所有/指定区域、区域状态、保存后下一轮生效），并区分「停止扫描」和「删除账号」。Playwright 覆盖新增、策略保存、批量停止、删除和恢复
- [x] 按原型严格复核（2026-11-13）：概览去掉最近更新资源，全局范围常显按服务 / 应用 / 环境 / 区域的分布，应用和账号范围收进「资源分布」；服务页有类型页签、状态 / 环境 / 区域胶囊和上下文数量，分组页有统计和分布；应用、账号和账号扫描情况页补面包屑并统一摘要、扫描情况和区域表格；资源详情改为摘要加配置 / 关联资源 / 标识与记录 / 标签卡片，并保留「原始配置」和「原始实时数据」；资源列表保留「更多筛选 / 导出 / 列」工具栏；扫描管理、扫描详情、任务详情和待核对范围按原型重写（摘要、进度、任务筛选、日志搜索）；扫描页返回走面包屑和侧栏的范围入口；环境值统一显示为 dev / preprod / prod；端到端测试同步
- [x] 异常范围超过 200 个（2026-11-15）：扫描页的「待核对的范围」每页 50 个、可翻页；核对页按 ID 读取范围，每 15 秒刷新，检测变化时重新读取候选并关闭确认框，异常解除后显示「找不到」；新增 Playwright 回归测试，用本地扫描出的 201 个范围验证翻页、直接链接、读取失败重试和状态变化（`frontend/e2e/scan-scopes.spec.ts`）。

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

## M9 报表（v2）
- [x] 定义格式和校验（`app/reports/definition.py`）：主体类型 + `all` / `any` / `not` 条件树、每个字段类型允许的操作符、值类型和长度限制、嵌套深度和条件数上限、列必须存在；不认识的 `definitionVersion` 拒绝（[specs/reports.md](specs/reports.md) 第 3 节）
- [x] 类型配置新增 `reportFields`，路径可以用 `[]` 穿过数组（穿过数组必须是 `text`，语义是「任一元素满足」）；ECS 任务定义声明容器镜像（[specs/resource-types.md](specs/resource-types.md) 1.2.1）
- [x] 求值（`app/query/reports.py`）：条件树编译成 SQL（`jsonb_path_exists` / `jsonb_path_query_array`），NULL 安全（每个叶子「不匹配 = false」，`not` 包含空值）；标签和关系计数字段；条件树没提到 `status` 时默认只看 `active` / `missing`；范围 `s` 和资源列表一致；排序和分页
- [x] 表 `t_inv_aws_report` 和迁移 0006；名称唯一；定义的 `subjectType` 和单独的列同步写入
- [x] API（`/reports`）：列表、新建、详情、修改、删除、`overview`（计数缓存 60 秒）、`fields`、`results`、`export`（CSV / JSON Lines）；错误码 `report_not_found`、`report_name_taken`
- [x] Dashboard：侧栏第三个功能区「报表」；列表页显示当前匹配数（打开页面时重新计算）；详情页复用资源表格（身份列 + 报表的列，可排序、导出、编辑、删除）；条件编辑器（分组、NOT、按字段类型给出操作符，标签单独给键）；中英文
- [x] 测试：定义校验；API 的 CRUD、重名、校验错误、AND / OR / NOT（含空值）、标签、关系计数、默认状态、范围、分页和排序错误、概览、Redis 缓存、导出、存着的定义失效；迁移
- [x] 独立只读评审（Codex，2026-11-15）并修复发现的问题：编辑时丢失 NOT、跳转时丢失范围、SQL 的 NOT / NULL 语义、默认列重复、值没有上限、NaN 和非文本的标签值、切换操作符后值的形状、布尔初值、定义失效的报表无法删除
- [x] 时间相对条件：`olderThanDays`（整数天）、`beforeNow`、`afterNow`，所以规则不用写死日期，可以表达「创建超过 29 天」「已经过了弃用时间」
- [x] findings 汇总：表 `t_inv_aws_finding_summary`（按资源身份存 critical / high / medium / low 计数和最高分，迁移 0007）；没有行表示还没检查过，不等于「没有问题」；AMI 类型声明 `findings: true`
- [x] findings 服务 ③ 客户端（`app/aws/findings.py`，地址 `FINDINGS_SERVICE_URL`）：worker 扫 AMI 时按镜像 ID 读取并写入汇总表，失败只记日志、不影响扫描
- [x] 报表字段：AMI 增加 `deprecationTime`；有 findings 的类型增加 4 个级别计数和最高分；主体类型的 `derivedRelationships` 可以在条件里用一跳关联字段（如 EC2 的 `image.creationDate`、`image.findingsHighCount`），编译成关联表上的 `EXISTS`；关联字段也可以作为显示列，值是关联目标上该字段的去重数组
- [x] 关联字段可以引用目标类型穿过数组的 `reportFields`（如 ECS 服务的 `taskdefinition.image`）：条件表示任一关联目标的任一元素满足，显示列把所有元素摊平、去重；关联本身仍然只走一跳
- [x] 报表 UI：条件字段按通用 / 类型 / 关联分组，关联字段带说明；显示列可选关联字段；超长资源名在结果表里截断，不再压到账号列
- [x] 侧栏：Inventory 改为和扫描、报表并列的一级入口，带图标和总数，常驻展开；概览不再是单独一行
- [x] 参考报表已在本地演示：AMI「过期或有高危发现」（10 条）、运行中的 EC2「使用了过期或有高危发现的 AMI」（7 条）；moto 不能设置 AMI 创建时间和弃用时间，本地只能演示 findings 分支，时间分支由后端测试覆盖
- [x] 报表的 Playwright 端到端测试（`frontend/e2e/reports.spec.ts`）：从侧栏进入、按账号新建条件、结果表和计数、CSV 导出、改名后回到列表、删除
- [ ] 用户确认后合并到 main

### 性能优化（等报表和清单规模上来再做，不影响正确性）
- [ ] 概览计数：`/reports/overview` 现在对每个启用报表各跑一条 `count(*)`，按报表 ID、范围和定义摘要缓存 60 秒。触发条件：启用报表到几十条，或单条计数明显变慢。候选做法：合成一条查询减少往返、列表懒加载（打开详情时再算）、后台预计算并延长缓存；结果仍然不落表（D-07）。
- [ ] `configuration` 查询：类型字段和 `reportFields` 编译成 `jsonb_path_exists` / `jsonb_path_query_array`，目前靠主体类型、状态和范围的索引收窄，逐行对 JSONB 求值；「所有资源」报表、超大类型和跨数组字段成本最高。触发条件：`EXPLAIN (ANALYZE)` 显示大类型报表的时间主要花在顺序扫描和 JSONPath 求值。候选做法：按真实高频字段建表达式索引、把热点字段提升为列、对超大类型建部分索引；通用 JSONB GIN 索引会增加每次扫描写入的维护成本，先不建。

