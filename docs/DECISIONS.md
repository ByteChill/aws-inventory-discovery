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
- **否决**：MUI Base UI（表格生态少）；Ant Design ProTable（外观难以符合设计原则）；AG Grid（关键功能收费）。

### D-11 资源列表有类型专属列，筛选控件按列自动生成
- **结论**：选定一个类型后显示这个类型的专属列；每一列都可以筛选和排序，筛选控件按列的数据类型自动生成，在后端执行。列定义写在资源类型配置文件里。
- **原因**：满足日常云运维按类型字段筛选的需求，不用单独做巡检页。

### D-12 本地用 moto 模拟 AWS，Cloud Control 用自己写的模拟服务
- **结论**：moto 模拟 Config、EC2、STS、CloudWatch Logs；`devtools/fake_aws` 模拟 Cloud Control 和两个内部服务；通过 boto3 原生的 `AWS_ENDPOINT_URL*` 环境变量切换。
- **原因**：moto 不支持 Cloud Control，它的 `SelectResourceConfig` 只返回预设结果；用环境变量切换，程序代码里不需要任何只在本地生效的逻辑。

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

### D-20 Codex review 发现的四处修正
- **叠加在写入时、锁住现有记录之后进行**，避免用旧快照覆盖并发的更新。映射覆盖到的字段以 Cloud Control 为准，返回里没有就视为被清空；映射覆盖不到的字段才保留原值。
- **刷新时只传 ARN 可能有歧义**：共享 AMI 的 ARN 对应多个账号时返回 `409 ambiguous_arn`；请求主要用 `{accountId, awsRegion, resourceType, resourceId}`。
- **单资源刷新发现不存在，不能直接删除**：两条证据分别记录。`absent_in_scan_at` 是第一条（完整扫描没看到）；按 ID 读取明确返回不存在是第二条。只有第二条时只标为 `missing`。
- **「不存在」和「出错」严格区分**：没权限、限流、不支持读取都不算不存在。Config 的 `ResourceDeleted` 和 `ResourceDeletedNotRecorded` 都算不存在（补充 D-08）。
- 脱敏由 Writer 在叠加和补充数据之后、计算哈希之前统一执行，是唯一的脱敏关卡。
