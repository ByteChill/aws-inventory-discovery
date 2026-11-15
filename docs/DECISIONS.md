# 决策记录

记录关键决定的结论、原因和被否决的方案。新增决定时追加到末尾，已有的决定被推翻时，在原条目下注明「已被 D-xx 取代」，不要删除。

完整设计见 [DESIGN.md](DESIGN.md)。

---

### D-01 两个 ECS 服务：数据生产与数据消费分离
- **结论**：`aws-inventory-discovery-worker` 负责扫描和写入；`aws-inventory-discovery-api` 负责 API 和 dashboard。
- **原因**：扫描耗时长，放在 API 进程里会占住请求线程，ALB 也会超时；两者可以分别扩缩、分别发布；只有 worker 持有目标账号的凭证，安全边界清楚。
- **否决**：按需扫描和定时扫描各用一个 worker 服务。v1 改为在同一个 worker 里给按需任务预留线程，效果接近，少一个服务。

### D-02 任务队列放在 Postgres，不用 Celery
- **结论**：worker 用 `SELECT … FOR UPDATE SKIP LOCKED` 从 `t_inv_aws_scan_task` 领任务；定时调度在 worker 里，用 advisory lock 选出一个实例负责。
- **原因**：已有的 ElastiCache 是否开启集群模式不确定，而 Celery 的 Redis broker 不支持集群模式；用 Celery 时任务同时存在于 Postgres 和 Redis，两边可能不一致；负载只有每秒约 3 个任务，Aurora 足够。
- **否决**：Celery + Redis；SQS 作为队列。

### D-03 Redis 只做缓存
- **结论**：Redis 只缓存只读的派生数据：分类计数、概览统计、筛选后的资源计数（列表 `total` 和 `/resources/summary` 共用）、账号列表、应用列表和扫描页摘要；限流、去重、凭证缓存都不放 Redis。
- **原因**：缓存丢失或 Redis 不可用时只影响速度，不影响数据正确性；这些都是可以由数据库重新算出的结果，TTL 到期自然恢复。
- **否决**：用 Redis 做限流（扫描不常触发，最后决定不做限流）；用 Redis 缓存凭证（凭证服务 ② 自带缓存）。

### D-04 账号清单在每轮扫描开始时拉取，凭证每次都向凭证服务要
- **结论**：没有单独的账号同步任务；本服务不缓存凭证。
- **原因**：账号清单服务 ① 和凭证服务 ② 已经存在，凭证服务自带缓存。

### D-05 定时扫描 Config 优先，Cloud Control 兜底；按需扫描和单资源刷新用 Cloud Control
- **结论**：见 DESIGN.md 4.3。
- **原因**：Config 一次查询覆盖一个账号区域的所有记录类型，调用次数少、速度快；Cloud Control 提供统一的 List/Get 接口，数据是实时的，能按 ID 读取单个资源，也不用为每个服务写采集代码。
- **否决**：Resource Explorer（只有 ARN、类型、区域、tags，没有配置详情，而且不确定每个账号都开启了）；为每个服务写 `Describe*` 采集器（代码量大）。

### D-06 统一使用 AWS Config 格式，Cloud Control 的数据通过 JSON 配置映射
- **结论**：对外的资源数据用 Config 配置项的格式；Cloud Control 的属性按资源类型配置文件转换，转不了的字段保留 Config 原来的值。
- **原因**：Config 是定时扫描的主要来源，覆盖最多；用一份配置文件维护映射，不用为每个类型写转换代码。
- **否决**：`configuration` 有时是 Config 格式、有时是 CloudFormation 格式（来源一切换就产生假变化）；Config 和 Cloud Control 的数据分两块各自存放（结构更复杂）。
- 已被 D-25 补充：Config 的键风格因类型而异，不能一律用 lowerCamelCase 的默认规则。

### D-07 不做变更历史
- **结论**：只保留资源的当前状态。
- **原因**：本服务的目的是获取资源的真实现状，不是版本管理；每 6 小时才扫描一次，历史本来就不完整，不如 Config 自带的配置历史；需要「谁改的」时看 CloudTrail。

### D-08 删除要有两条独立的证据
- **结论**：完整扫描的列表里没有它，**并且** Cloud Control `GetResource` 明确返回不存在，才标为删除；确认不了就永远保持「可能已删除」；一次扫描超过一半的资源消失时整批不处理。
- **原因**：没删除的资源绝不能被标为删除。

### D-09 不做应用内登录，身份暂时用假数据展示
- **结论**：不自己做登录、会话和权限判断。访问仍由网络层控制（内网 ALB、安全组），每个操作记录触发方。当前设计阶段用前端内置的假身份展示姓名、邮箱和登录状态；正式实现时由网关传入身份，Dashboard 只显示，过期时跳转网关重新认证。
- **原因**：只在内网使用，管理操作都不具破坏性；先确认身份区的布局和状态，不为此引入认证服务。生产构建必须移除假身份，不能把占位姓名当成真实用户。
- **补充**：网关身份的具体请求头由平台确认；没有身份信息时隐藏身份区。

### D-10 前端技术栈和 full-stack-fastapi-template 一致
- **结论**：React 19、Vite、TypeScript、TanStack Router / Query / Table、shadcn/ui、Tailwind v4、hey-api 生成 API 客户端。
- **原因**：和参考模板一致，少做决定；shadcn 的组件源码在项目里，可以完全按设计原则修改外观；资源列表需要「根据列定义自动生成筛选控件」，TanStack Table 加一层生成逻辑可以做到。
- 已被 D-29 补充：实际没有用 react-hook-form，多语言自己实现，Playwright 已接入。
- **否决**：MUI Base UI（表格生态少）；Ant Design ProTable（外观难以符合设计原则）；AG Grid（关键功能收费）。

### D-11 资源列表有类型专属列，筛选控件按列自动生成
- **结论**：选定一个类型后显示这个类型的专属列；每一列都可以筛选和排序，筛选控件按列的数据类型自动生成，在后端执行。列定义写在资源类型配置文件里。
- **原因**：满足日常云运维按类型字段筛选的需求，不用单独做巡检页。
- 已被 D-36 补充：v2 的报表要保存规则、表达 NOT、标签和跨数组字段，所以单独做了报表页；类型专属列仍然是报表字段的主要来源。

### D-12 本地用 moto 模拟 AWS，Cloud Control 用自己写的模拟服务
- **结论**：moto 模拟 Config、EC2、STS、CloudWatch Logs；`devtools/fake_aws` 模拟 Cloud Control 和两个内部服务；通过 boto3 原生的 `AWS_ENDPOINT_URL*` 环境变量切换。
- **原因**：moto 不支持 Cloud Control，它的 `SelectResourceConfig` 只返回预设结果；用环境变量切换，程序代码里不需要任何只在本地生效的逻辑。
- 已被 D-21 补充：Config 也由 `devtools/fake_aws` 模拟，moto 只负责 EC2、STS 和 CloudWatch Logs。

### D-13 测试数据只来自真实文件或 API 返回
- **结论**：样本按 AWS 官方文档整理并注明出处；测试代码里不手工构造数据；只写功能测试，不写冗余测试。

### D-14 所有资源命名带 `aws-inventory-discovery` 前缀
- **结论**：见 DESIGN.md 第 13 节。
- **原因**：Aurora、Redis 等资源和其他服务共用，需要一眼区分。

### D-15 worker 和 api 都按 CPU、内存自动扩缩
- **结论**：由平台配置自动扩缩，不发额外的队列积压指标。
- **否决**：worker 发布「排队任务数」「最早任务等待时间」指标，按它扩缩。

### D-16 AMI 用 EC2 `DescribeImages` 读取，只包括自有和共享的
- **结论**：`Owners=["self"]` 和 `ExecutableUsers=["self"]` 各调用一次，按 AMI ID 合并，带 `IncludeDeprecated=true`、`IncludeDisabled=true`；不包括 Amazon 官方、Marketplace 和其他公开的 AMI。删除确认用 `DescribeImages(ImageIds=[…])`。
- **原因**：Config 和 Cloud Control 都不支持 AMI（M1 核对）。按 AWS 文档，`ExecutableUsers=self` 会返回直接共享给账号的 AMI，以及共享给账号所在组织或 OU 的 AMI。
- **影响**：增加第三种数据源「服务自己的 API」，只用于 Config 和 Cloud Control 都不支持的类型，读取函数在 `hooks.py` 里显式登记。这是对 D-05 的补充，不改变 D-05 的结论。

### D-17 资源的唯一标识是 账号 + 区域 + 类型 + 资源 ID，ARN 不唯一
- **结论**：资源表的唯一约束和写入时的冲突键都用这四个字段；`/resources/lookup?arn=` 返回列表。
- **原因**：AMI 的 ARN 不带账号，共享给多个账号的同一个 AMI 在每个账号下 ARN 相同。共享 AMI 在每个能看到它的账号下各存一行，和 Config 按账号记录资源的做法一致，也能直接回答「这个账号能用哪些 AMI」。
- **否决**：共享 AMI 只在所有者账号下存一份，另建共享关系表（多一张表和合并逻辑；所有者账号不在账号清单里时拿不到数据）。

### D-18 资源类型分两批完成
- **结论**：第一批（重点）是 VPC、Subnet、ALB/NLB、ECS（Cluster / Service / Task Definition）、EC2、AMI、Lambda、Secret、SNS、SQS，以及每个账号开启的区域；第二批是 Security Group、Target Group、Route 53 Hosted Zone，仍在 v1 范围内。

### D-19 代码结构：每个类型一个可选的 `Hooks` 对象，读取和写入分层
- **结论**：资源类型仍然是 JSON 配置 + 通用函数；配置表达不了的逻辑写成普通函数，放进这个类型的 `Hooks` 对象（`list_live`、`get_live`、`filter`、`enrich`，都可选），登记到 `types/hooks.py` 的显式字典 `TYPE_HOOKS`。`Discovery` 负责选数据源和读取，`ResourceWriter` 负责叠加、脱敏、写入和删除判断，所有类型共用。
- **原因**：有三种数据源，特殊情况的种类会增加。按类型聚合钩子，既能一处看到某个类型的全部特殊处理，也能一处看到整个系统有哪些特殊处理；把写入集中在 Writer，钩子就无法绕过并发规则、删除规则和脱敏。
- **否决**：按「特殊情况的种类」分成多个字典（`LIST_PARENTS`、`LIST_FILTERS`、`NATIVE_SOURCES`），种类一多，中心函数的分支越来越长；每个类型一个子类继承 `ResourceType`，继承没有明显好处，基类会随扩展点变大。
- **来源**：用户的思路（每个类型一个发现对象）+ Codex 的独立 review（2026-11-08）。
- 已被 D-24 补充：`Hooks` 多了 `cloudcontrol_identifier`；M1 核对后，`filter` 和 ECS Service 的 `list_live` 都没有类型需要。

### D-20 Codex review 发现的四处修正
- **叠加在写入时、锁住现有记录之后进行**，避免用旧快照覆盖并发的更新。映射覆盖到的字段以 Cloud Control 为准，返回里没有就视为被清空；映射覆盖不到的字段才保留原值。
- **刷新时只传 ARN 可能有歧义**：共享 AMI 的 ARN 对应多个账号时返回 `409 ambiguous_arn`；请求主要用 `{accountId, awsRegion, resourceType, resourceId}`。
- **单资源刷新发现不存在，不能直接删除**：两条证据分别记录。`absent_in_scan_at` 是第一条（完整扫描没看到）；按 ID 读取明确返回不存在是第二条。只有第二条时只标为 `missing`。
- **「不存在」和「出错」严格区分**：没权限、限流、不支持读取都不算不存在。Config 的 `ResourceDeleted` 和 `ResourceDeletedNotRecorded` 都算不存在（补充 D-08）。
- 脱敏由 Writer 在叠加和补充数据之后、计算哈希之前统一执行，是唯一的脱敏关卡。

### D-21 本地环境：AWS Config 也由 `devtools/fake_aws` 模拟
- **结论**：moto 只提供 EC2（`DescribeRegions`、`DescribeImages`）、STS 和 CloudWatch Logs；Cloud Control 和 AWS Config 都由 `devtools/fake_aws` 提供，包括 `SelectResourceConfig`、`DescribeConfigurationRecorders`、`DescribeConfigurationRecorderStatus`、`BatchGetResourceConfig`。`fake_aws` 另有管理接口 `/_admin`（灌入数据集、删除资源、注入 `AccessDenied` / 限流等错误），`seed.py` 通过它灌数据。环境变量：`AWS_ENDPOINT_URL`（moto）、`AWS_ENDPOINT_URL_CLOUDCONTROL`、`AWS_ENDPOINT_URL_CONFIG_SERVICE`（fake-aws）。moto 在宿主机上用 5001 端口（macOS 的 AirPlay 占着 5000），并设置 `MOTO_EC2_LOAD_DEFAULT_AMIS=false`；容器间的调用设 `NO_PROXY`，本地设 `SWEEP_ENABLED=false`，靠按需扫描演示。
- **原因**：moto 的服务器模式没有 HTTP 接口可以预设 `SelectResourceConfig` 的结果，D-12 原来的设想做不到；自己模拟 Config 还能测记录器关闭、停止、只记录部分类型，以及 Config 的删除状态（`ResourceDeleted`、`ResourceDeletedNotRecorded`）。
- **已知限制**：moto 的 AMI 按账号隔离，共享 AMI 在别的账号里看不到；moto 里没有任务日志的日志组，本地查不到任务日志。

### D-22 任务和租约用围栏令牌，行锁有固定顺序
- **结论**：每次领取任务生成新的 `scan_task.claim_token`，范围租约记着持有者的 `scan_shard.lease_token`；心跳、租约操作、检查点、结束任务都要出示当前令牌，令牌过期的 worker 停止并且什么都不再写。回收（心跳超时）清掉令牌，不释放租约，租约自己到期。所有事务按「范围行 → 任务行 → 扫描请求行」的顺序取行锁。
- **原因**：只靠心跳超时判断「worker 挂了」，一个只是卡住（长时间 GC、网络抖动）的 worker 醒来后会继续写数据、续租、甚至释放新持有者的租约。令牌让旧的运行在下一次调用时就被拒绝。固定的加锁顺序避免取消、领取、结束任务之间的死锁。

### D-23 `DescribeRegions` 在第一个默认开启的可达区域调用
- **结论**：调用 `ec2:DescribeRegions(AllRegions=true)` 的区域是 `REACHABLE_REGIONS` 里第一个不是 opt-in 的区域（opt-in 区域清单来自 AWS 官方文档，写在 `aws/regions.py`）；没有这样的区域就明确失败。
- **原因**：ap-east-1 是 opt-in 区域，在没有开启它的目标账号里无法调用任何服务，而 `REACHABLE_REGIONS` 的第一项恰好就是它。
- **影响**：默认配置下 `DescribeRegions` 走 us-east-1 的 `ec2` endpoint；部署清单要确保它存在。

### D-24 ECS Task Definition 不过滤，ECS Service 不需要 `list_live`
- **结论**：M1 对照 CloudFormation 资源类型 schema 核对后：Cloud Control 的 ECS Task Definition 数据里没有 `Status` 和 `Revision`，「每个 family 只保留最新的 ACTIVE 版本」算不出来。它的 list handler 调用 `ecs:ListTaskDefinitions`（默认只列 ACTIVE），所以**所有 ACTIVE 的版本都入库**。某个版本变成 INACTIVE 后从列表里消失，先标为「可能已删除」，按 ID 读取（`DescribeTaskDefinition` 对 INACTIVE 的版本仍然返回）确认它还在，不会被误删。ECS Service 的 list handler 自己枚举集群，用默认的 `ListResources` 就是完整的，不需要 `list_live`。
- **原因**：核对结果和原来的设计假设不符（见 `backend/tests/fixtures/aws/VERIFICATION.md` 第 2、4 条）；没有依据就不推断「最新版本」，先完整入库（版本号只能从 ARN 末尾解析，见 VERIFICATION 第 4 条）。
- **影响**：`Hooks` 里没有类型使用 `filter`；登记的钩子是 AMI 的 `list_live` / `get_live`，以及 ECS Service、ECS Cluster 的 `cloudcontrol_identifier`（只靠 `resourceId`（ARN）推出复合 Identifier，让刷新库里没有的资源成为可能）。

### D-25 Config 的键风格因类型而异：`mapping.keyCase` 和 `mapping.properties`
- **结论**：每个类型配置自己的键风格：`camel`（默认）或 `pascal`。ECS、Secret、SNS、SQS、Route 53 的 Config 保留 PascalCase（M1 核对 `awslabs/aws-config-resource-schema`），其余类型是 lowerCamelCase。配置里还要列出映射覆盖的 Cloud Control 属性（`mapping.properties`）：没有 schema，叠加就不知道「Cloud Control 本来会返回哪些字段」，也就无法清掉被删除的字段。覆盖的路径只能整体替换数组，不能穿过数组，加载时检查。`identity.nameFrom` 增加 `tail:<路径>`（取 ARN 或 URL 的末段）；`toString` 把布尔值转成 `"true"` / `"false"`。配置文件名必须等于小写点分的类型名，否则启动失败。
- **原因**：一律用默认的 lowerCamelCase，Config 的数据和 Cloud Control 转换出来的数据键对不上，每次来源一切换就产生假变化和重复字段。

### D-26 扫描逐页读写，合并只合并能覆盖的任务
- **结论**：读取、写入、检查点交替进行：每读到一页就写一页，页与页之间（以及删除判断之前、每次按 ID 读取之前）检查是否被停止、取消或失去所有权。每个写入批次的统计在同一个事务里累加到 `scan_task.stats`，扫描请求的计数由任务统计汇总。排在范围后面的按需任务只合并它能覆盖的（种类、范围、数据源都相同，且不是 `resource`），覆盖不了的不占位置，等租约到期再试。一个排队任务可能被多个请求共用，取消一个请求只取消没有别的请求还需要的任务，共用的任务继续运行，直到所有需要它的请求都被取消。`config_group` 一开始就取得它覆盖的所有类型的租约，读取期间一直持有。`confirm_missing` 不受「刚扫过就跳过」限制。`resource` 任务不取租约。
- **原因**：先把一个范围全部读完再写，内存里攒着整个范围的数据，取消和关闭要等很久；任务失败时已经写入的数据和统计不能丢；把不同种类的任务合并在一起会让某个请求拿不到它要的数据；取消一个请求不能让别的请求饿死。

### D-27 全量扫描的创建串行化，advisory lock 的 key 带上 schema
- **结论**：调度器和 `POST /scans {all: true}` 用同一个事务级 advisory lock（`aws-inventory-discovery:full-sweep`）串行化「检查有没有正在跑的全量扫描、创建新的」。所有 advisory lock 的 key 由 `<名称>:<DATABASE_SCHEMA>` 的 SHA-256 前 8 字节算出（按有符号 64 位整数）。
- **原因**：先查后插不加锁，两个同时到达的请求会各建一轮；advisory lock 在整个数据库范围内有效，共用一个数据库、schema 不同的部署或测试不能互相抢锁。

### D-28 Secrets Manager 的密钥是 JSON 对象；worker 的健康检查看心跳文件
- **结论**：`aws-inventory-discovery/db` 存 `DATABASE_URL` 和 `DATABASE_READ_URL`，`aws-inventory-discovery/redis` 存 `REDIS_URL`，任务定义用 `…:<密钥名>:<键>::` 注入。worker 容器的健康检查是 `python -m app.cli healthcheck`：心跳线程每 30 秒刷新 `WORKER_HEARTBEAT_FILE`，文件超过 60 秒没更新就不健康，检查不访问数据库；容器里这个路径（默认在 `/tmp`）必须可写。
- **原因**：连接串不只是密码（还有主机、库名，api 另有只读连接串），放成一个 JSON 对象里的几个键，一个密钥就能给每个服务注入它需要的值；worker 不提供 HTTP，健康检查不需要为了判断存活再去连数据库。

### D-29 Dashboard 的实现选择：自己写 i18n，配色定为胭脂
- **结论**：多语言用自己写的、约 60 行的带类型检查的实现（扁平 JSON 文案、`{name}` 插值、` | ` 区分单复数，键在编译时检查），不用 react-i18next。配色最终使用胭脂；黛绿和黛蓝保留在 `frontend/PALETTES.md` 作为历史记录，界面不再提供切换。没有用 react-hook-form（表单都是简单的受控组件）；Playwright 已接入（`frontend/e2e/`，6 个流程）。
- **原因**：文案是一个扁平的集合、两种语言、简单的复数，自己写比多一个运行时依赖更简单；三套配色并排比较后选胭脂。

### D-30 挂在已有 ALB 的 `/aws-inventory` 下，路径在构建镜像时确定
- **结论**：服务挂在平台已有的内网 ALB 的 `/aws-inventory` 下，不新建 ALB。ALB 原样转发路径，不做 URL 重写；应用的 API、文档和 dashboard 都挂在 `BASE_PATH` 下。这个值只在 Dockerfile 的构建参数里设置：前端用它作为 Vite `base` 构建，后端从镜像的环境变量读取。设置了前缀后，前缀以外不提供服务。
- **原因**：uvicorn 的 `--root-path` 只适用于代理已经去掉前缀的情况，ALB 原样转发时请求会 404；在共享 ALB 上配 URL 重写，平台要多配规则，而且漏配就整个不可用。不用 hash 路由，是为了保持干净的地址。不在运行时由服务端改写页面里的前缀，是为了不维护自定义的 HTML 处理。代价是换路径要重新构建镜像；路径已经确定，可以接受。

### D-31 Dashboard 的信息架构：服务树、范围选择、当前清单
- **结论**：左侧栏按服务组织资源（Overall，以及计算、Serverless、网络、消息、安全分组），左上角单独选择资源范围（全部、应用、账号）；应用有独立的列表页。概览不单独放大搜索框，全局搜索只在顶栏。服务页、分组页、范围页和扫描范围页都复用资源列表，通过 `resourceType`、`s`、`scanRequestId` 区分。点击资源行打开预览侧栏，点击名称进入详情。扫描记录的结果数字只作统计，不链接到资源列表；另提供「查看当前范围资源」。
- **原因**：账号超过 1,000 个、资源类型有 15 种，按服务浏览比一个长筛选栏更直接；范围和筛选分开，切换应用时不会丢掉仍然适用的筛选。系统只保存当前状态（D-07），无法还原某次扫描当时的新增、更新或删除明细，所以结果数字不能伪装成可以点开的历史列表。

### D-32 异常范围保存候选资源 ID，供核对页读取
- **结论**：触发 R6 整批保护时，除 `candidate_count` 外把候选资源的 ID 保存到 `scan_shard.candidate_ids`；范围恢复 `normal` 时清空。新增 `GET /scan-scopes/{id}/candidates` 供核对页分页读取，返回库里的名称、ARN 和当前状态；记录已清理时只返回 ID 并标记 `purged`。
- **原因**：只有数量时，管理员无法判断消失的是哪些资源，也就无法判断是不是真实变化。候选 ID 属于当前保护事件的数据，不是变更历史，不违反 D-07；确认后仍按 R5 逐条按 ID 读取，不会直接删除。

### D-33 `scanRequestId` 的范围：保留期内按任务，之后按请求记录的范围
- **结论**：扫描创建后 `TASK_RETENTION_DAYS`（14 天）内，`scanRequestId` 的范围按这次扫描的任务计算；超过后改按请求记录的 `scope` 或 `target` 计算。`config_group` 只覆盖 Config 能提供的类型，不覆盖只走 Cloud Control 或服务 API 的类型。
- **原因**：任务明细只保留 14 天，扫描请求保留 90 天，而每条扫描记录都有「查看当前范围资源」。只按任务计算时，超过 14 天的扫描会显示空列表，和范围本来就没有资源无法区分。任务按完成时间逐个清理，所以用「创建时间超过保留期」作为切换点，不用「任务是否已经清空」：前者不会出现只剩一部分任务、范围越来越小的中间状态。`config_group` 的任务不带类型，早先按「区域内所有类型」计算，会把同一轮里被去重跳过的 AMI、Target Group 也算进来（2026-11-13 独立评审发现）。

### D-34 缓存用专用 ElastiCache for Valkey（cluster mode disabled）
- **结论**：新建专用 ElastiCache for Valkey，不共用现有 Redis。cluster mode disabled，1 主 1 副本、Multi-AZ 自动故障转移，生产 `cache.t4g.small`（非生产 `cache.t4g.micro`）；自定义端口 16379，与 api / worker 在同一个 ap-east-1 VPC 的私有子网，安全组只放行 api / worker 到 16379；开启 TLS 和 AUTH（token 放 Secrets Manager 的 `aws-inventory-discovery/redis`）；关闭备份；参数组 `allkeys-lru`。缓存只存派生数据，key 仍以 `aws-inventory-discovery:<DATABASE_SCHEMA>:` 开头。
- **原因**：缓存丢失只影响速度，不影响数据正确性；CMD 让本地和线上都用 `redis.Redis`，不需要 cluster-aware 客户端；自定义端口满足安全规范；专用实例避免共享缓存的内存、淘汰策略和 `FLUSHDB` 风险；Multi-AZ 避免节点故障时把查询压力打回 Aurora；Valkey 比 Redis OSS 便宜，仍在积极开发。
- **否决**：ElastiCache Serverless（固定 6379/6380，不能自定义端口；强制 cluster mode enabled，客户端要换成 `RedisCluster`，本地环境也要另起集群或加配置分支）；共用现有 Redis（噪声邻居、内存策略不可控、cluster mode 未知）；MemoryDB（持久化数据库，不是缓存）；cluster mode enabled（数据量不需要分片，客户端和跨 slot 限制不值得）。
- **备注**：D-02 的任务队列选择不受影响；D-03 的「Redis 只做缓存」不变。

### D-35 关系 = Config 提供 + 类型配置显式声明的派生关系
- **结论**：关系有两个来源：Config 的 `relationships`，以及类型配置里的 `derivedRelationships`。派生路径支持 `[]` 数组，一条规则可以产生多条边；`toField` 指定按目标类型的 `resourceId`（默认）或 ARN 解析，范围是同一账号、同一区域（全局目标不限区域）。Config 关系优先；同一目标重复时只保留 Config 的边。每次写入按当前 `configuration` 重算，字段消失时删除旧派生边。派生边带 `origin: "derived"`，Config 边不带 `origin`。关系仍不参与内容哈希。
- **原因**：Config 的 relationships 并不覆盖所有明确引用，例如 ECS Service 的 `relationships` 为空，但 `configuration.TaskDefinition` 是一条约定的 ARN。只靠 Config 会丢边；允许通用推导又需要猜字段和类型，容易产生错误的图。显式声明把每个类型的关系规则集中在一个可校验、可测试的地方，启动时还能检查目标类型是否存在。
- **补充原因**：很多资源没有独立于 ARN 的 `resourceId`，而配置字段也经常用 ARN 指向名称型 ID；只支持 resourceId 会漏掉 Lambda 等常见引用。数组路径则覆盖 ECS 负载均衡、Lambda VPC 配置这类一对多关系。
- **否决**：通用或模糊的关系推导；只保留 Config 关系，让巡检等上层功能各自做 join。

### D-36 报表是声明式规则，在读取时按当前清单求值
- **结论**：第三个功能叫报表（原来的叫法「巡检」指的是同一个功能）。报表保存的是数据，不是代码：一条定义由主体类型、`all` / `any` / `not` 条件树、字段操作符和值组成，保存时按该类型的字段清单校验，读取时由后端编译成 SQL，在 `t_inv_aws_resource` 上求值。不保存匹配结果（没有 findings 表），每次打开页面重新计算，计数最多缓存 60 秒。报表与扫描完全解耦：不参与扫描，不写资源表，不调用目标账号的 AWS API。结果导出（CSV / JSON Lines）属于报表本身。规格见 `docs/specs/reports.md`。
- **原因**：用户要回答的是「现在有哪些资源满足这条规则」；D-07 只保存当前状态，把结果另存一张表会得到第二份可能过期的清单，还要面对「资源后来变了、当时的匹配还算不算」的问题。声明式规则可以在保存时就把错误的字段、操作符和值拦下来，不需要运行时沙箱，也符合服务的只读边界。读取时求值让报表永远和资源列表看到同一份数据，范围（`s`）、状态语义和筛选行为都能直接复用。跨数组字段（例如 ECS 任务定义的容器镜像）在类型配置里用 `reportFields` 显式声明，仍然集中在可校验的地方。
- **否决**：在浏览器里执行用户写的 JS（需要沙箱，无法在后端校验，权限和审计都做不了）；把匹配结果保存成新的资源表或 findings 表（违反 D-07，且要处理结果随扫描变化）；扫描结束时计算并保存结果（让报表耦合扫描流程，按需扫描和全量扫描都要触发，结果照样会过期）；只让用户用资源列表的筛选来表达规则（无法保存、无法跨数组、无法表达 NOT 和关系计数）。
- **备注**：权限暂不做（D-09），当前任何能打开 dashboard 的人都可以定义报表；正式接入时再按「管理员定义、所有人查看」收口。推送和外部告警推迟（DESIGN 第 14 节）。「巡检」改称「报表」只改用户可见的叫法和文档，数据和接口都用 report。报表结果仍然不保存；D-37 的 findings 汇总表存的是资源的外部检查结果，不是报表结果。

### D-37 AMI 的 findings 存当前汇总，报表时间用相对条件，关联字段只走一跳
- **结论**：findings 服务 ③ 按镜像 ID 返回 critical / high / medium / low 计数和最高风险分；**接口格式暂按假设实现**（`GET {FINDINGS_SERVICE_URL}/ami/{imageId}/findings`），上线前确认，与 ①② 相同。只保存**当前汇总**表 `t_inv_aws_finding_summary`，按资源身份 `(account_id, aws_region, resource_type, resource_id)` 唯一，不保存每条 finding，不保存历史（D-07）。**没有行表示还没检查过，不等于 0**，所以「high > 0」不会把未检查的资源算进来。worker 扫 AMI 时顺手读取并 upsert，失败只记日志，不影响扫描和删除判定；报表读取时不做任何外部调用，findings 字段是汇总表上的标量子查询。报表条件支持相对时间 `olderThanDays` / `beforeNow` / `afterNow`，规则不用写死日期。条件可以引用**一跳**关联字段（类型配置的 `derivedRelationships`，如 EC2 的 `image.creationDate`、`image.findingsHighCount`），编译成关联表上的 `EXISTS`；只支持已声明的关联，报表字段不再从目标资源继续走第二条关联（派生关系本身可以从主体配置的数组里取多个目标，如 ECS 服务的 `LoadBalancers[].TargetGroupArn`）。目标字段可以使用穿过数组的 `reportFields`（如 ECS 服务的 `taskdefinition.image`），正向操作符表示「任一关联目标的数组里任一元素满足」，否定操作符先在一个目标内部取反；关联字段作为显示列时，值是该关联目标上同一字段的**去重数组**（多个目标、跨数组时把所有元素摊平；一个目标时就是单元素），因此不能排序。
- **原因**：用户要的是「运行中的 EC2 所使用 AMI 过期了或有 findings」和「账号里的 AMI 过期了或有 findings」两类报表。findings 是外部服务在扫描时给出的当前值，按 D-07 只存最新汇总最省事；把关联字段放进条件，可以让报表保持纯 SQL、读取时不必逐行调用外部服务，也不把 findings 的格式耦合进报表定义。
- **否决**：把每条 finding 存成结果表（违反 D-07，数据量大且需要结果生命周期）；读取报表时逐资源调用 ③（读取变慢、外部服务不可用时报表整体失败）；让报表自己写 join 或 JSONPath 指到关联资源（定义无法校验，跨类型字段容易写错）；在条件里支持多跳关联（代价是指数级展开，当前两个报表只需要一跳）。
- **备注**：findings 写入和其他资源写入一样受任务围栏保护（丢令牌不写），并用 `checked_at` 保证旧读取不覆盖新汇总。资源被删除并清理后，它的 findings 行随之删除；账号被清理时一并删除。
