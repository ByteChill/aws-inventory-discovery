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
- **结论**：Redis 只缓存分类计数和概览统计；限流、去重、凭证缓存都不放 Redis。
- **原因**：Redis 不可用时只影响速度，不影响数据正确性。
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

### D-09 不做应用内登录
- **结论**：v1 访问由网络层控制（内网 ALB、安全组），每个操作记录触发方。
- **原因**：只在内网使用，管理操作都不具破坏性。以后需要时再加。

### D-10 前端技术栈和 full-stack-fastapi-template 一致
- **结论**：React 19、Vite、TypeScript、TanStack Router / Query / Table、shadcn/ui、Tailwind v4、hey-api 生成 API 客户端。
- **原因**：和参考模板一致，少做决定；shadcn 的组件源码在项目里，可以完全按设计原则修改外观；资源列表需要「根据列定义自动生成筛选控件」，TanStack Table 加一层生成逻辑可以做到。
- 已被 D-29 补充：实际没有用 react-hook-form，多语言自己实现，Playwright 还没有接入。
- **否决**：MUI Base UI（表格生态少）；Ant Design ProTable（外观难以符合设计原则）；AG Grid（关键功能收费）。

### D-11 资源列表有类型专属列，筛选控件按列自动生成
- **结论**：选定一个类型后显示这个类型的专属列；每一列都可以筛选和排序，筛选控件按列的数据类型自动生成，在后端执行。列定义写在资源类型配置文件里。
- **原因**：满足日常云运维按类型字段筛选的需求，不用单独做巡检页。

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

### D-29 Dashboard 的实现选择：自己写 i18n，三套配色可切换
- **结论**：多语言用自己写的、约 60 行的带类型检查的实现（扁平 JSON 文案、`{name}` 插值、` | ` 区分单复数，键在编译时检查），不用 react-i18next。配色三套都做了：黛绿（默认）、黛蓝、胭脂，用 `?palette=` 切换，记在 localStorage，最终保留哪套由用户决定。没有用 react-hook-form（表单都是简单的受控组件）；Playwright 还没有接入。
- **原因**：文案是一个扁平的集合、两种语言、简单的复数，自己写比多一个运行时依赖更简单；三套配色并排比较才能选。

### D-30 挂在已有 ALB 的 `/aws-inventory` 下，路径在构建镜像时确定
- **结论**：服务挂在平台已有的内网 ALB 的 `/aws-inventory` 下，不新建 ALB。ALB 原样转发路径，不做 URL 重写；应用的 API、文档和 dashboard 都挂在 `BASE_PATH` 下。这个值只在 Dockerfile 的构建参数里设置：前端用它作为 Vite `base` 构建，后端从镜像的环境变量读取。设置了前缀后，前缀以外不提供服务。
- **原因**：uvicorn 的 `--root-path` 只适用于代理已经去掉前缀的情况，ALB 原样转发时请求会 404；在共享 ALB 上配 URL 重写，平台要多配规则，而且漏配就整个不可用。不用 hash 路由，是为了保持干净的地址。不在运行时由服务端改写页面里的前缀，是为了不维护自定义的 HTML 处理。代价是换路径要重新构建镜像；路径已经确定，可以接受。
