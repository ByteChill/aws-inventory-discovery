# 规格：API

本文是 [DESIGN.md](../DESIGN.md) 第 8 节的实现细节。实现以 FastAPI 生成的 OpenAPI（`/api/v1/openapi.json`）为最终依据；接口有变化时同步修改本文。

## 1. 通用约定

- 前缀 `/api/v1`；JSON 字段用 camelCase；时间是 ISO 8601 UTC 字符串。
- 分页参数：`page`（从 1 开始，默认 1）、`pageSize`（默认 50，最大 200）。
- 排序参数：`sort`，多个字段用逗号分隔，前面加 `-` 表示倒序。只允许对列定义里 `sortable = true` 的列排序。
- 请求头：`X-Caller-Id`（可选，记录到扫描请求的 `callerId`，dashboard 传 `dashboard`）；`Idempotency-Key`（只用于 `POST /scans`）。
- 鉴权：无，访问由网络层控制。

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
| 400 | `invalid_filter` | 筛选参数不合法，比如没指定单一类型就使用 `attr.*` |
| 400 | `invalid_sort` | 排序字段不允许 |
| 404 | `resource_not_found` | 资源不存在 |
| 404 | `account_not_found` | 账号不在账号快照里，查询账号清单服务后也没有 |
| 404 | `scan_not_found` / `task_not_found` | |
| 409 | `idempotency_conflict` | 同一个 `Idempotency-Key` 对应了不同的请求内容 |
| 409 | `ambiguous_arn` | 刷新时只传了 ARN，但它对应多个账号（共享 AMI） |
| 409 | `scan_not_cancellable` | 扫描已经结束 |
| 422 | `validation_error` | 请求体校验失败（FastAPI 默认的校验错误，转为统一格式，`details` 里带字段错误） |
| 422 | `scope_too_large` | 按需扫描超过 `MAX_SCAN_ACCOUNTS` 个账号 |
| 503 | `dependency_unavailable` | 数据库不可用，或查询 CloudWatch Logs 失败 |

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
  status: ResourceStatus; stale: boolean;  // stale：所在范围超过 12 小时没有成功扫描
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
```

## 3. 资源

### `GET /resources`
- 参数：筛选参数（见 DESIGN.md 8.2）、分页、排序。默认排序 `-lastChangedAt`。
- 返回：`Page<ResourceItem>`。
- `attrs` 只在请求里指定了单一 `resourceType` 时返回该类型的专属列；否则为 `{}`。

### `GET /resources/facets`
- 参数：`field`（可重复，允许 `accountId`、`eimId`、`baId`、`accountType`、`awsRegion`、`resourceType`、`status`，以及单一类型时的 `attr.<key>`，后者要求列类型为 `enum` 或 `bool`）、筛选参数。
- 计算某个字段的分类时，不应用这个字段本身的筛选条件，这样已选中的选项旁边也能显示其他选项的数量。
- 返回：`{ [field: string]: FacetBucket[] }`，每个字段最多 200 个选项，按数量倒序。
- 缓存：Redis，key `aws-inventory-discovery:facets:<筛选条件哈希>`，60 秒。

### `GET /resources/export`
- 参数：`format=csv|jsonl`、`columns`（CSV 用，逗号分隔的列键，默认是当前类型的全部列）、筛选参数、排序。
- CSV：UTF-8 带 BOM（方便 Excel 打开），第一行是列名（按 `Accept-Language`）。
- JSON Lines：每行一条完整记录（`ResourceDetail` 的格式，不含 `live`）。
- 流式输出，用服务端游标分批读取，每批 1,000 行；文件名 `aws-inventory-<UTC 时间>.csv` / `.jsonl`。

### `GET /resources/lookup`
- 参数：`arn`（必填）。
- 返回：`ResourceDetail[]`。ARN 不唯一：共享 AMI 的同一个 ARN 会出现在多个账号下，排序时自有的（`configuration.ownership = "self"`）排在前面，其余按账号 ID。一条都没有时 404 `resource_not_found`。

### `GET /resources/{id}`
- 返回：`ResourceDetail`。

### `GET /resources/{id}/relationships`
- 参数：`direction=out|in|both`（默认 `both`）、分页。
- 返回：`Page<{ direction: "out" | "in"; relationName: string; resourceType: string; resourceKey: string; resource: ResourceItem | null }>`。`resource` 为空表示库里没有对应的资源。

### `POST /resources/refresh`
- 参数：`wait`（秒，0–20，默认 0）。
- 请求体，二选一：
  - `{ "accountId", "awsRegion", "resourceType", "resourceId" }`：**主要写法**，永远没有歧义，也能刷新库里还没有的资源。`resourceId` 就是 Config 格式里的 `resourceId`（和列表、详情里显示的一样），系统按类型规则在内部换算成 Cloud Control 的 Identifier，例如 ECS Service 的复合 Identifier 中的 Cluster 从 Service ARN 解析。
  - `{ "arn": string }`：方便的写法，只能刷新库里已有的资源。ARN 对应多个账号时（共享 AMI）返回 409 `ambiguous_arn`。
- 返回：

| 情况 | HTTP | 返回内容 |
|---|---|---|
| 在 `wait` 秒内完成 | 200 | `ResourceDetail` |
| 没有及时完成，或 `wait = 0` | 202 | `{ "taskId": number }` |
| AWS 里明确不存在，资源原来是 `active` | 200 | `ResourceDetail`，`status = "missing"`（只有一条证据，等完整扫描确认） |
| AWS 里明确不存在，资源原来是 `missing` 且完整扫描也没看到它 | 200 | `ResourceDetail`，`status = "deleted"` |
| AWS 里明确不存在，库里也没有 | 404 | `resource_not_found` |
| 只传 `arn`，对应多个账号 | 409 | `ambiguous_arn`，`details.accountIds` 列出账号 |
| 没有权限、被限流等 | 200 | `ResourceDetail`（库里现有的数据）+ `refreshError: { code, message }` |

同一个资源已经有排队或运行中的刷新任务时，复用那个任务。

## 4. 元数据

### `GET /resource-types`
返回：`{ typeName: string; label: string; global: boolean }[]`。

### `GET /columns`
- 参数：`resourceType`（可选）。
- 返回：`Column[]`。不传类型时只有通用列；传了类型时是通用列加这个类型的专属列。

### `GET /stats/overview`
返回：

```ts
{
  resourceTotal: number;
  accountTotal: number; accountUnreachable: number;   // 无法访问 = access_status 为 access_denied 或 credential_error
  byResourceType: { resourceType: string; label: string; count: number }[];
  byAccountType: { accountType: string; label: string; count: number }[];
  generatedAt: string
}
```
缓存：Redis，60 秒。

## 5. 账号

### `GET /accounts`
- 参数：`accountType`、`eimId`、`baId`、`accessStatus`、`q`（名称或 ID）、`active`（默认 `true`）、分页、排序。
- 返回：`Page<AccountRef & { active: boolean; accessStatus: string; accessError: string | null; resourceCount: number; lastScanOkAt: string | null; purgeAt: string | null }>`。`purgeAt` 只对不活跃的账号有值。

### `GET /accounts/{accountId}`
返回：上面的账号信息，加上 `regions: { region: string; optInStatus: string; reachable: boolean; configRecording: boolean | null }[]`。

### `GET /accounts/{accountId}/coverage`
返回：`{ region: string; resourceType: string; state: "ok" | "failed" | "access_denied" | "stale" | "never"; lastSucceededAt: string | null; lastErrorCode: string | null }[]`，来自 `t_inv_aws_scan_shard`。

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

### `POST /scans`
- 请求头：`Idempotency-Key`（可选）。
- 请求体：`{ "accountIds": string[], "awsRegions"?: string[], "resourceTypes"?: string[] }` 或 `{ "all": true }`。
- 返回：`201 ScanRequest`；`all` 并且已有一轮在跑时返回 `200` 和那一轮；同一个 `Idempotency-Key` 再次提交相同内容时返回 `200` 和原来的请求。

### `GET /scans`
参数：`trigger`、`status`、分页（默认按 `createdAt` 倒序）。返回：`Page<ScanRequest>`。

### `GET /scans/{id}`
返回：`ScanRequest`，加上 `failuresByCode: { errorCode: string; count: number }[]`。

### `GET /scans/{id}/tasks`
参数：`status`、`errorCode`、`accountId`、`kind`、分页。返回：`Page<Task>`。

### `POST /scans/{id}/retry-failed`
把这个请求下 `failed` 的任务重新放回队列（`attempt` 清零）。返回：`{ requeued: number }`。

### `POST /scans/{id}/cancel`
返回：`ScanRequest`；已结束时 409 `scan_not_cancellable`。

### `GET /tasks/{id}`
返回：`Task`。

### `GET /tasks/{id}/logs`
- 参数：`limit`（默认 200，最大 1,000）、`nextToken`。
- 实现：在 `WORKER_LOG_GROUP` 上调用 `FilterLogEvents`，过滤条件 `{ $.taskId = <id> }`，时间范围是任务的开始到结束时间，前后各留 1 分钟。
- 返回：`{ events: { timestamp: string; level: string; message: string; data: Record<string, unknown> }[]; nextToken: string | null }`。

### `GET /scan-scopes`
参数：`guardState`（默认 `abnormal`）、分页。返回：`Page<{ accountId; region; resourceType; guardState; guardDetectedAt; candidateCount: number; activeCount: number }>`。

### `POST /scan-scopes/confirm-change`
请求体：`{ "accountId", "awsRegion", "resourceType" }`。把这个范围设为 `confirmed`，记录 `guardConfirmedBy`（`X-Caller-Id`），并立即为它创建一个按需 `shard` 任务。返回：`{ scanRequestId: number }`。

## 7. 健康检查

### `GET /health`
返回 `200 { "database": "ok", "redis": "ok" | "unavailable" }`。数据库不可用时返回 503；Redis 不可用时仍返回 200（只影响速度）。
