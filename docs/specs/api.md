# 规格：API

本文是 [DESIGN.md](../DESIGN.md) 第 8 节的实现细节。实现以 FastAPI 生成的 OpenAPI（`/api/v1/openapi.json`）为最终依据；接口有变化时同步修改本文。

## 1. 通用约定

- 前缀 `/api/v1`（部署时在 `BASE_PATH` 之下，生产是 `/aws-inventory/api/v1`）；JSON 字段用 camelCase；时间是 ISO 8601 UTC 字符串。
- 分页参数：`page`（从 1 开始，默认 1）、`pageSize`（默认 50，最大 200）。
- 排序参数：`sort`，多个字段用逗号分隔，前面加 `-` 表示倒序。只允许对列定义里 `sortable = true` 的列排序：通用列写列的 `key`（如 `-lastChangedAt`），类型专属列写 `attr.<key>`（只在指定了单一 `resourceType` 时可用）；NULL 总是排在最后；最后再按资源 ID 排序，翻页稳定。
- 请求头：`X-Caller-Id`（可选，记录到扫描请求的 `callerId`，也是确认异常范围时的 `guardConfirmedBy`，dashboard 传 `dashboard`）；`Idempotency-Key`（只用于 `POST /scans`）；`Accept-Language`（`zh*` 或 `en*`，决定列名、类型名、分类标签的语言，默认中文）。
- 读写分离：查询类接口读 `DATABASE_READ_URL`（没设置就用 `DATABASE_URL`），创建扫描、刷新、确认异常范围写主库。
- 鉴权：不自己做登录，访问由网络层控制；当前设计阶段用假身份展示，正式接入时由网关传入，仅用于显示。

### 1.1 分页结构

```ts
type Page<T> = { items: T[]; page: number; pageSize: number; total: number }
```

### 1.2 错误

```ts
type Error = { code: string; message: string; details?: Record<string, unknown> }
```

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_filter` | 筛选参数不合法：**不认识的查询参数**（资源列表、分类计数、导出只认第 8.2 节的参数和 `attr.*`），没指定单一类型就使用 `attr.*`，`attr.*` 的列不存在或不可筛选、取值和列类型不符、或对这个类型的列不适用（比如对文本列用 `.gte`），分类计数的 `field` 不支持 |
| 400 | `invalid_sort` | 排序字段不允许（资源列表、导出、账号列表、应用列表） |
| 404 | `resource_not_found` | 资源不存在（详情、按 ARN 查询、刷新一个库里没有的 ARN 或 AWS 里也不存在的资源） |
| 404 | `account_not_found` | 账号不在账号快照里，查询账号清单服务后也没有；`POST /scans` 里 `details.accountIds` 列出这些账号，也包括已经不活跃的账号 |
| 404 | `scan_not_found` / `task_not_found` | |
| 404 | `scope_not_found` | 确认异常范围时，这个范围从没扫描过（`t_inv_aws_scan_shard` 里没有） |
| 404 | `not_found` | 路径不存在 |
| 405 | `method_not_allowed` | |
| 409 | `idempotency_conflict` | 同一个 `Idempotency-Key` 对应了不同的请求内容 |
| 409 | `ambiguous_arn` | 刷新时只传了 ARN，但它对应多个账号（共享 AMI） |
| 409 | `scan_not_cancellable` | 扫描已经结束 |
| 409 | `scan_cancelled` | 对已取消的扫描重试失败的任务 |
| 409 | `scope_not_abnormal` | 确认异常范围时，这个范围当前不是 `abnormal`（`details.guardState`） |
| 422 | `validation_error` | 请求体或参数校验失败（FastAPI 默认的校验错误，转为统一格式，`details.errors` 里带字段错误；也包括未知的 `resourceType`、不能换算成 Cloud Control Identifier 的 `resourceId`、未知的导出列，以及账号列表、扫描列表等接口收到不认识的参数） |
| 422 | `scope_too_large` | 按需扫描超过 `MAX_SCAN_ACCOUNTS` 个账号（`details.limit`、`details.requested`） |
| 502 | `refresh_failed` | 刷新单个资源失败，而且库里没有这个资源可以返回（`details.taskId`、`details.errorCode`）；库里有时返回 200 加 `refreshError` |
| 503 | `dependency_unavailable` | 数据库不可用，账号清单服务不可用（只在需要确认账号时），或查询 CloudWatch Logs 失败（包括日志组不存在） |

`message` 是给人看的中文说明；dashboard 根据 `code` 显示本地化后的文字。

## 2. 数据结构

```ts
type AccountRef = {
  accountId: string; accountName: string; accountType: "dev" | "preprod" | "prod";
  eimId: string | null; eimApp: string | null; baId: string | null
}

type ResourceStatus = "active" | "missing" | "deleted"

// 列表里的一行
type ResourceItem = {
  id: number; arn: string; accountId: string; awsRegion: string;
  resourceType: string; resourceId: string; resourceName: string | null;
  tags: { key: string; value: string }[];
  account: AccountRef;
  attrs: Record<string, unknown>;          // 当前类型的专属列，键来自列定义
  relationshipCount: number;
  status: ResourceStatus;
  // stale：资源没有被删除，并且所在范围（账号 + 区域 + 类型）从没成功扫描过，或最近一次成功扫描超过 12 小时前
  stale: boolean;
  configurationItemCaptureTime: string | null;
  firstSeenAt: string; lastChangedAt: string;
  missingSince: string | null; deletedAt: string | null
}

// 详情
type ResourceDetail = ResourceItem & {
  configuration: Record<string, unknown> | null;   // Config 格式
  relationships: { resourceType: string; resourceId: string; name: string }[];
  live: { properties: Record<string, unknown>; observedAt: string } | null;  // Cloud Control 原始属性（已脱敏）
  observedAt: string; observedSource: "config" | "cloudcontrol" | "native"
}

type Column = {
  key: string; label: string;        // 按 Accept-Language 返回中文或英文
  type: "text" | "enum" | "number" | "bool" | "datetime";
  mono: boolean; sortable: boolean; filterable: boolean;
  scope: "common" | "type"           // 通用列 / 类型专属列
}

type FacetBucket = { value: string; label: string; count: number }

type Application = {
  eimId: string; eimApp: string; baId: string | null;
  accountCount: number;
  environments: ("dev" | "preprod" | "prod")[];
  resourceCount: number
}

type ScopeCandidate = {
  id: number | null;            // 数据库 ID；记录已清理时为 null
  resourceId: string; resourceName: string | null; arn: string;
  inventoryStatus: "active" | "missing" | "deleted" | "purged"
}
```

## 3. 资源

### `GET /resources`
- 参数：筛选参数（见 DESIGN.md 8.2）、分页、排序。默认排序 `-lastChangedAt`。
- 返回：`Page<ResourceItem>`。
- `attrs` 只在请求里指定了单一 `resourceType` 时返回该类型的专属列；否则为 `{}`。详情和按 ARN 查询的 `attrs` 总是按资源自己的类型计算。数据里没有值的列是 `null`。
- 筛选参数的规则：同名参数重复传是「或」；不同参数之间是「且」；`status` 默认是 `active` 加 `missing`。`tag=key:value` 按**第一个冒号**切分（所以值里可以有冒号），没有冒号就是「有这个标签」，重复的 `tag` 是「或」；`tagMissing=key` 重复传是「且」（这些标签都没有）。`q` 对名称、ARN、资源 ID 做不区分大小写的包含匹配。时间范围参数是 `<字段>.gte` / `<字段>.lte`，字段是 `configurationItemCaptureTime`、`firstSeenAt`、`lastChangedAt`、`missingSince`、`deletedAt`。`scanRequestId` 只列这次扫描范围内的当前资源，范围按已解析的账号、区域、类型计算，不是简单拼接请求里的 `scope`；显示的是当前清单，不代表这次扫描当时的结果。扫描创建后 `TASK_RETENTION_DAYS` 天内，范围按这次扫描的任务计算：`shard` 和 `confirm_missing` 覆盖自己的账号、区域、类型；`config_group` 覆盖它的账号、区域里 Config 能提供的类型（不含只走 Cloud Control 或服务 API 的类型，比如 Target Group、AMI，它们各有自己的 `shard`），请求限定了 `resourceTypes` 时再按它收窄；`resource` 只覆盖那一个资源；`sweep_account` 不覆盖任何资源；全局类型的区域记为 `global`，由 us-east-1 的 `config_group` 覆盖。超过这个时间后任务明细可能已经清理（先完成的任务先清理），改按请求记录的范围计算：有 `target` 的请求是那个资源或那个范围，其余按 `scope` 的账号（或 `all`）、区域（给了 us-east-1 就包含全局类型）和类型。`attr.<key>`：文本、枚举、布尔列只能等于（可多值，是「或」）；数字列可以等于，也可以 `.gte` / `.lte`；时间列只能 `.gte` / `.lte`；只在 `resourceType` 恰好指定一个类型时可用。
- **类型专属列和通用列重名**（ECS Task Definition 的 `status` 是 Config 里的任务定义状态，和资源的 `status` 同名）：类型专属列在查询参数里**一律**写成 `attr.<key>`（`attr.status`），不带前缀的 `status` 永远是资源状态。`GET /columns` 会返回两个 `key` 都是 `status` 的列，用 `scope`（`common` / `type`）区分；dashboard 据此给类型专属列加前缀。
- 缓存：`total` 单独缓存在 Redis，60 秒，key 由筛选条件决定（不含分页和排序）；与 `GET /resources/summary` 的 `resources` 共用。列表内容本身不缓存。

### `GET /resources/facets`
- 参数：`field`（可重复，允许 `accountId`、`eimId`、`baId`、`accountType`、`awsRegion`、`resourceType`、`status`，以及单一类型时的 `attr.<key>`，后者要求列类型为 `enum` 或 `bool`）、筛选参数。
- 计算某个字段的分类时，不应用这个字段本身的筛选条件，这样已选中的选项旁边也能显示其他选项的数量；`status` 的分类也一样，不应用默认的「`active` 加 `missing`」，所以能看到各个状态（包括 `deleted`）的数量。计算 `resourceType` 的分类时也不应用 `attr.*` 条件（它们依赖选定的类型）。
- 返回：`{ [field: string]: FacetBucket[] }`，每个字段最多 200 个选项，按数量倒序；`label` 按 `Accept-Language`：账号是账号名称，EIM 是 EIM 应用名，类型是类型的显示名，状态和环境是本地化的词，`global` 区域显示为「全局」，布尔是「是 / 否」。
- 缓存：Redis，按字段分别缓存，key `aws-inventory-discovery:facets:<筛选条件的哈希>:<field>:<语言>`，60 秒；同一筛选条件下，不同页面请求的字段集合共用重叠字段。

### `GET /resources/summary`
- 参数：筛选参数（同 `GET /resources`）。
- 返回：`{ resources, accounts, applications, resourceTypes, regions }`，分别是当前筛选条件下的资源数、账号数、EIM 应用数、资源类型数和区域数，用 `count(distinct …)` 在 SQL 里计算，不受分类计数最多 200 个选项的限制。`resources` 与列表的 `total` 一致；没有 EIM 的账号不计入 `applications`。
- 缓存：`resources` 复用列表 `total` 的 60 秒缓存；另外四个去重计数每次查询。

### `GET /resources/export`
- 参数：`format=csv|jsonl`（默认 `csv`）、`columns`（CSV 用，逗号分隔的列键，通用列写 `key`，类型专属列写 `attr.<key>`；默认是全部通用列，指定了单一类型时再加上它的全部专属列；不认识的列返回 422 `validation_error`）、`lang`（`zh-CN` 或 `en`，指定表头用的语言，优先于 `Accept-Language`，方便直接在浏览器地址里下载）、筛选参数、排序。
- CSV：UTF-8 带 BOM（方便 Excel 打开），换行 `\r\n`，第一行是列名；标签写成 `k=v;k=v`，布尔值写 `true` / `false`，时间是 UTC 的 ISO 8601；以 `=`、`+`、`-`、`@`、制表符、回车开头的单元格前面会补一个 `'`，避免被表格程序当作公式执行。
- JSON Lines：每行一条完整记录（`ResourceDetail` 的格式，不含 `live`）。
- 流式输出，用服务端游标分批读取，每批 1,000 行；文件名 `aws-inventory-<UTC 时间>.csv` / `.jsonl`（`Content-Disposition: attachment`）。

### `GET /resources/lookup`
- 参数：`arn`（必填）。
- 返回：`ResourceDetail[]`（带 `live`）。ARN 不唯一：共享 AMI 的同一个 ARN 会出现在多个账号下，排序时自有的（`configuration.ownership = "self"`）排在前面，其余按账号 ID。一条都没有时 404 `resource_not_found`。dashboard 搜索框输入 ARN 时用它：只有一条就直接打开，有多条就先让用户选账号。

### `GET /resources/{id}`
- 返回：`ResourceDetail`。

### `GET /resources/{id}/relationships`
- 参数：`direction=out|in|both`（默认 `both`）、分页。
- 返回：`Page<{ direction: "out" | "in"; relationName: string; resourceType: string; resourceKey: string; resource: ResourceItem | null }>`。`resource` 为空表示库里没有对应的资源。

### `POST /resources/refresh`
- 参数：`wait`（秒，0–20，默认 0）。
- 请求体，二选一：
  - `{ "accountId", "awsRegion", "resourceType", "resourceId" }`：**主要写法**，永远没有歧义，也能刷新库里还没有的资源。`resourceId` 就是 Config 格式里的 `resourceId`（和列表、详情里显示的一样），系统按类型规则在内部换算成 Cloud Control 的 Identifier：库里有这个资源时用它存着的 `configuration`；库里没有时用类型钩子从 `resourceId` 推出，目前 **ECS Service**（`resourceId` 是长格式的 Service ARN，Cluster 从 ARN 解析，不带集群名的旧格式 ARN 返回 422 `validation_error`）和 **ECS Cluster**（`resourceId` 是 Cluster ARN，Identifier 是集群名）都支持用 `resourceId` 刷新库里没有的资源。账号不在快照里时先向账号清单服务确认（404 `account_not_found` 或 503 `dependency_unavailable`）。
  - `{ "arn": string }`：方便的写法，只能刷新库里已有的资源（库里没有返回 404 `resource_not_found`）。ARN 对应多个账号时（共享 AMI）返回 409 `ambiguous_arn`。
- 返回：

| 情况 | HTTP | 返回内容 |
|---|---|---|
| 在 `wait` 秒内完成 | 200 | `ResourceDetail` |
| 没有及时完成，或 `wait = 0` | 202 | `{ "taskId": number }` |
| AWS 里明确不存在，资源原来是 `active` | 200 | `ResourceDetail`，`status = "missing"`（只有一条证据，等完整扫描确认） |
| AWS 里明确不存在，资源原来是 `missing` 且完整扫描也没看到它 | 200 | `ResourceDetail`，`status = "deleted"` |
| AWS 里明确不存在，库里也没有 | 404 | `resource_not_found` |
| 只传 `arn`，对应多个账号 | 409 | `ambiguous_arn`，`details.accountIds` 列出账号 |
| 没有权限、被限流等，库里有这个资源 | 200 | `ResourceDetail`（库里现有的数据）+ `refreshError: { code, message }`（`code` 是任务的错误码） |
| 没有权限、被限流等，库里没有这个资源 | 502 | `refresh_failed`，`details.taskId`、`details.errorCode` |

同一个资源已经有排队或运行中的刷新任务时，复用那个任务（任务表上的部分唯一索引保证只有一个，同时到达的两个请求也只会调用一次 AWS）。`wait` 由 API 每 500 毫秒查询一次任务状态实现。

## 4. 元数据

### `GET /resource-types`
返回：`{ typeName: string; label: string; global: boolean }[]`，`label` 按 `Accept-Language`。

### `GET /columns`
- 参数：`resourceType`（可选）。
- 返回：`Column[]`。不传类型（或类型不认识）时只有通用列；传了类型时是通用列加这个类型的专属列。通用列有：`resourceName`、`resourceType`、`resourceId`、`arn`、`accountId`、`accountName`、`eimId`、`eimApp`、`baId`、`accountType`、`awsRegion`、`status`、`tags`、`configurationItemCaptureTime`、`firstSeenAt`、`lastChangedAt`、`missingSince`、`deletedAt`；其中 `tags` 不能排序。

### `GET /stats/overview`
返回：

```ts
{
  resourceTotal: number;
  // 资源数不含已删除的；账号数只含活跃账号；无法访问 = access_status 为 access_denied 或 credential_error
  accountTotal: number; accountUnreachable: number;
  byResourceType: { resourceType: string; label: string; count: number }[];
  byAccountType: { accountType: string; label: string; count: number }[];
  generatedAt: string
}
```
缓存：Redis，key `aws-inventory-discovery:stats:overview:<语言>`，60 秒。

## 5. 账号

### `GET /accounts`
- 参数：`accountType`、`eimId`、`baId`、`accessStatus`（都可以重复，是「或」）、`q`（名称或 ID，包含匹配）、`active`（默认 `true`；`false` 是从清单里移除的账号）、分页、排序（默认 `accountName`，可以排序的有 `accountId`、`accountName`、`accountType`、`eimId`、`baId`、`accessStatus`、`lastScanOkAt`、`active`、`resourceCount`；不认识的参数返回 422）。`resourceCount` 不含已删除的资源。
- 返回：`Page<AccountRef & { active: boolean; accessStatus: string; accessError: string | null; resourceCount: number; lastScanOkAt: string | null; purgeAt: string | null }>`。`purgeAt` 只对不活跃的账号有值。
- 缓存：整页缓存在 Redis，60 秒，key 由筛选、分页、排序和 `INACTIVE_ACCOUNT_PURGE_DAYS` 决定。

### `GET /accounts/{accountId}`
返回：上面的账号信息，加上 `regions: { region: string; optInStatus: string; reachable: boolean; configRecording: boolean | null }[]`。

### `GET /accounts/{accountId}/coverage`
返回：`{ region: string; resourceType: string; state: "ok" | "failed" | "access_denied" | "stale" | "never"; lastSucceededAt: string | null; lastErrorCode: string | null }[]`，来自 `t_inv_aws_scan_shard`，所以只有扫描过的范围有格子，没有的组合 dashboard 显示为「未扫描」。`state`：连续失败次数大于 0 时是 `failed`（最近一次错误码是 `access_denied` 一类时是 `access_denied`）；否则从没成功过是 `never`；最近一次成功超过 12 小时前是 `stale`；其余是 `ok`。

### `GET /applications`
- 参数：`q`（应用名、EIM、BA，包含匹配）、`accountType`（可重复）、分页、排序（`eimApp`、`accountCount`、`resourceCount`，默认 `eimApp`；不认识的参数返回 422，不能排序的字段返回 400 `invalid_sort`）。
- 返回：`Page<Application>`。只统计活跃账号；没有 EIM 的账号不出现；`resourceCount` 不含已删除资源。`total` 和列表按同一组（EIM、应用名、BA）计数。先分页，再只为这一页的应用统计资源数；只有按 `resourceCount` 排序时才需要统计全部应用。
- 应用的身份是 EIM。EIM、应用名、BA 一一对应的假设来自 DESIGN 2，真实接口格式确认前不变。
- 缓存：整页缓存在 Redis，60 秒，key 由筛选、分页和排序决定。

## 6. 扫描

```ts
type ScanRequest = {
  id: number; trigger: "schedule" | "api"; callerId: string | null;
  scope: { accountIds?: string[]; awsRegions?: string[]; resourceTypes?: string[]; all?: boolean } | null;
  target: Record<string, string> | null;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  counts: {
    tasks: Record<string, number>;
    resources: { new: number; updated: number; missing: number; deleted: number }
  };
  createdAt: string; startedAt: string | null; finishedAt: string | null
}

type Task = {
  id: number; scanRequestId: number; kind: string; source: string | null;
  accountId: string; region: string | null; resourceType: string | null; resourceId: string | null;
  status: string; attempt: number; maxAttempts: number;
  errorCode: string | null; errorMessage: string | null; stats: Record<string, unknown> | null;
  createdAt: string; startedAt: string | null; finishedAt: string | null
}
```

### `GET /scans/summary`
返回：

```ts
{
  sweep: { scanRequestId: number; accountsDone: number; accountsTotal: number;
           startedAt: string; estimatedFinishAt: string | null; behindSchedule: boolean } | null;
  backlog: { onDemand: { queued: number; oldestWaitSeconds: number };
             scheduled: { queued: number; oldestWaitSeconds: number } };
  failures24h: { errorCode: string; count: number }[];
  missingCount: number;
  abnormalScopeCount: number;
  workers: { activeTasks: number; lastHeartbeatAt: string | null }   // 由运行中任务的心跳推算
}
```

- `sweep`：最近一个还在 `queued` / `running`、并且是定时的（`trigger = schedule`）或范围是全部账号的扫描；没有则为 `null`。`accountsTotal` 是这个请求里有任务的账号数，`accountsDone` 是其中已经没有排队或运行中任务的账号数。`estimatedFinishAt` = 开始时间 + 已用时间 × 总数 ÷ 已完成数，一个账号都没完成时为 `null`。`behindSchedule`：预计完成时间（没有就用现在）晚于开始时间加 `SWEEP_INTERVAL_HOURS`。**接口不返回计划完成时间，所以不能算出「慢了多久」。**
- `backlog`：`queued` 是已经到时间可以领取的排队任务数（`not_before` 不晚于现在），`oldestWaitSeconds` 是最早那个到时间以来等了多久；`onDemand` 是 `priority = 0`，`scheduled` 是其余。
- `failures24h`：最近 24 小时内结束的失败任务，按错误码汇总（没有错误码的记为 `internal`）。`missingCount` 是全库状态为 `missing` 的资源数。
- 缓存：Redis，key `aws-inventory-discovery:scans:summary`，10 秒（dashboard 每 3–5 秒轮询）。

### `POST /scans`
- 请求头：`Idempotency-Key`（可选）。
- 请求体：`{ "accountIds": string[], "awsRegions"?: string[], "resourceTypes"?: string[] }` 或 `{ "all": true }`，两者必须且只能给一个；账号 ID 必须是 12 位数字；`all` 不能再限定区域和类型；`resourceTypes` 里有不认识的类型返回 422 `validation_error`。
- `all = true` 时先刷新账号快照（失败就用现有的）；账号列表里不在快照里的账号先向账号清单服务确认（404 `account_not_found` / 503 `dependency_unavailable`）；超过 `MAX_SCAN_ACCOUNTS` 返回 422 `scope_too_large`。
- 返回：`201 ScanRequest`；`all` 并且已有一轮（定时的或手动的）在跑时返回 `200` 和那一轮；同一个 `Idempotency-Key` 再次提交相同内容时返回 `200` 和原来的请求，内容不同返回 409 `idempotency_conflict`。请求的 `scope` 保存规范化后的内容（账号、区域、类型排序去重）。

### `GET /scans`
参数：`trigger`、`status`、分页（默认按 `createdAt` 倒序）。返回：`Page<ScanRequest>`。

### `GET /scans/{id}`
返回：`ScanRequest`，加上 `failuresByCode: { errorCode: string; count: number }[]`。

### `GET /scans/{id}/tasks`
参数：`status`、`errorCode`、`accountId`、`kind`、分页。返回：`Page<Task>`。

### `POST /scans/{id}/retry-failed`
把这个请求下 `failed` 的任务重新放回队列（`attempt` 清零，清掉错误）。返回：`{ requeued: number }`。已取消的请求返回 409 `scan_cancelled`；一个失败的刷新任务，如果同一个资源已经有更新的刷新任务在排队或运行，保持失败，不计入 `requeued`。

### `POST /scans/{id}/cancel`
返回：`ScanRequest`；已结束时 409 `scan_not_cancellable`。

### `GET /tasks/{id}`
返回：`Task`。

### `GET /tasks/{id}/logs`
- 参数：`limit`（默认 200，最大 1,000）、`nextToken`。
- 实现：在 `WORKER_LOG_GROUP` 上调用 `FilterLogEvents`，过滤条件 `{ $.taskId = <id> }`，时间范围是任务的开始到结束时间（还没开始用创建时间，还没结束用现在），前后各留 1 分钟；返回前再按 `taskId` 过滤一遍。worker 的日志是 JSON，一行一条，`taskId` 等字段在每行都有。
- 调用失败（没权限、日志组不存在……）返回 503 `dependency_unavailable`。**本地环境里 moto 没有这个日志组，所以本地总是 503**，dashboard 显示「日志暂时无法查看」。
- 返回：`{ events: { timestamp: string; level: string; message: string; data: Record<string, unknown> }[]; nextToken: string | null }`。

### `GET /scan-scopes`
参数：`guardState`（`normal` / `abnormal` / `confirmed`，默认 `abnormal`）、分页。返回：`Page<{ scanShardId: number; accountId; region; resourceType; guardState; guardDetectedAt; candidateCount: number; activeCount: number }>`，按检测时间倒序。`candidateCount` 是触发保护的那次扫描想标为「可能已删除」的资源数（`scan_shard.candidate_count`），`activeCount` 是现在这个范围里 `active` 的资源数。

### `GET /scan-scopes/{id}/candidates`
- `id` 是 `GET /scan-scopes` 返回的 `scanShardId`；不存在返回 404 `scope_not_found`。
- 参数：`q`（名称、资源 ID、ARN，包含匹配）、分页。
- 返回：`Page<ScopeCandidate>`。候选来自触发保护时保存的候选资源 ID（`scan_shard.candidate_ids`），按资源 ID 排序。资源还在库里时返回库里的名称、ARN 和状态；已经清理的 `id` 为 `null`，`inventoryStatus` 为 `purged`。范围当前不是 `abnormal` 时返回空页。迁移 0005 之前触发保护的范围没有保存候选：`candidateCount` 大于 0，这里仍返回空页，下一次触发保护后才有。

### `POST /scan-scopes/confirm-change`
请求体：`{ "accountId", "awsRegion", "resourceType" }`（账号必须是 12 位数字，类型必须认识，否则 422 `validation_error`）。范围从没扫描过返回 404 `scope_not_found`；当前不是 `abnormal` 返回 409 `scope_not_abnormal`。否则把这个范围设为 `confirmed`，记录 `guardConfirmedBy`（`X-Caller-Id`，没有就记 `api`），并立即为它创建一个按需 `shard` 任务（请求的 `target` 是 `{accountId, awsRegion, resourceType}`）。返回：`{ scanRequestId: number }`。这个 `shard` 完成后，这个范围里没看到的资源逐个按 R5 第 4 步确认，不会直接删除。

## 7. 健康检查

### `GET /health`
返回 `200 { "database": "ok", "redis": "ok" | "unavailable" }`。数据库不可用时返回 503，body 是 `{ "database": "unavailable", "redis": … }`；Redis 不可用（或没有配置 `REDIS_URL`）时仍返回 200（只影响速度）。
