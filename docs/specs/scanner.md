# 规格：扫描（worker）

本文是 [DESIGN.md](../DESIGN.md) 第 4、6、7 节的实现细节。两者不一致时以 DESIGN.md 为准，并修正本文。

## 1. 任务种类

| kind | 由谁创建 | 做什么 | 数据源 |
|---|---|---|---|
| `sweep_account` | 调度器（一轮全量扫描）、`POST /scans` | 刷新账号的区域和 Config 记录器状态，为每个区域拆出子任务 | EC2、Config |
| `config_group` | `sweep_account` | 一个账号区域的一次 Config 查询，覆盖这个区域所有由 Config 负责的类型 | Config |
| `shard` | `sweep_account`、`POST /scans` | 一个范围（账号 + 区域 + 类型）的 Cloud Control 扫描 | Cloud Control |
| `resource` | `POST /resources/refresh` | 刷新单个资源 | Cloud Control |
| `confirm_missing` | `config_group`、`shard` 结束时 | 对一个范围里「可能已删除」的资源逐个确认 | 按 ID 读取：类型钩子的 `get_live`，默认 Cloud Control；Cloud Control 读不了时看 Config 状态 |

**分层**（见 DESIGN.md 10.1）：任务处理函数取得租约 → `Discovery` 读取，产出 `Observation`，并标记读取是否完整 → `ResourceWriter` 写入和做删除判断。`Discovery` 返回的是生成器时，**必须在租约内消费完**，期间心跳持续续约；只有读取完整（所有分页、上级资源、补充数据都成功）时才做删除判断。

按需扫描（`POST /scans`）直接为每个账号创建 `sweep_account`，但标记为按需：子任务一律用 Cloud Control，不创建 `config_group`。

## 2. 任务状态

```
queued ──领取──> running ──成功──> succeeded
   │                │
   │                ├──失败，attempt < max_attempts──> queued（not_before = 现在 + 退避时间）
   │                ├──失败，attempt = max_attempts，或错误不可重试──> failed
   │                ├──范围正忙（定时任务）──> skipped
   │                └──心跳超时（worker 挂了）──> queued，attempt + 1
   ├──被合并──> coalesced（coalesced_into 指向合并后的任务）
   └──扫描被取消──> cancelled
```

- **退避时间**：`30 秒 × 2^(attempt-1)`，加 0–30 秒随机值。
- **扫描请求的状态**：子任务全部结束后汇总。全部成功是 `succeeded`；有失败也有成功是 `partial`；全部失败是 `failed`；被取消是 `cancelled`。

## 3. 任务队列

### 3.1 领取

每个 worker 有 `WORKER_THREADS` 个线程，其中 `WORKER_ONDEMAND_THREADS` 个只领按需任务（`priority = 0`）。

```sql
UPDATE t_inv_aws_scan_task
   SET status = 'running', worker_id = :worker_id, attempt = attempt + 1,
       started_at = now(), heartbeat_at = now()
 WHERE id = (
   SELECT t.id FROM t_inv_aws_scan_task t
    WHERE t.status = 'queued'
      AND t.not_before <= now()
      AND t.priority <= :max_priority
      AND (SELECT count(*) FROM t_inv_aws_scan_task r
            WHERE r.account_id = t.account_id AND r.status = 'running') < :per_account_concurrency
    ORDER BY t.priority, t.not_before
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *;
```

每个账号的并发上限是软上限：两个 worker 同时领取时偶尔会多一个，对防止限流来说足够。

### 3.2 心跳与回收

| 参数 | 值 |
|---|---|
| 心跳间隔 | 30 秒：更新 `heartbeat_at`，同时续租范围租约 |
| 心跳超时 | 2 分钟没有心跳，视为 worker 已挂 |
| 回收 | 负责调度的 worker 每分钟检查一次：超时的 `running` 任务放回 `queued`；`attempt` 达到上限的标为 `failed`（`error_code = worker_lost`） |

### 3.3 取消
`POST /scans/{id}/cancel` 把这个请求下所有 `queued` 任务标为 `cancelled`。正在运行的任务每处理完一页，检查所属请求是否已取消；已取消就停止，不做删除判断，释放租约。

## 4. 范围租约（R1–R3）

范围的主键是 `(account_id, region, resource_type)`。`config_group` 对它覆盖的每个类型分别取租约。

### 4.1 取得租约

```sql
INSERT INTO t_inv_aws_scan_shard (account_id, region, resource_type)
VALUES (:a, :r, :t) ON CONFLICT DO NOTHING;

UPDATE t_inv_aws_scan_shard
   SET lease_task_id = :task_id, lease_expires_at = now() + interval '5 minutes',
       last_started_at = now()
 WHERE (account_id, region, resource_type) = (:a, :r, :t)
   AND (lease_task_id IS NULL OR lease_expires_at < now())
RETURNING *;
```

没有返回行，说明范围正忙：
- **定时任务**：标为 `skipped`。
- **按需任务**：写入 `pending_task_id`。如果原来已经有一个，把原来的标为 `coalesced`，`coalesced_into` 指向新的。当前任务本身标为 `coalesced`，等租约释放后由 `pending_task_id` 指向的任务执行。

### 4.2 跳过刚扫过的范围（R3）
定时任务取得租约前，如果 `last_succeeded_at > now() - 30 分钟`，直接标为 `skipped`。按需任务不检查。

### 4.3 释放
任务结束时（无论成功或失败）：清空 `lease_task_id`；成功则更新 `last_succeeded_at`、清零 `consecutive_failures`，失败则记录 `last_error_code`、`consecutive_failures + 1`。如果 `pending_task_id` 不为空，把那个任务的 `not_before` 设为现在，并清空 `pending_task_id`。

## 5. 一轮全量扫描

### 5.1 创建（调度器）
调度器每分钟运行一次（只有持有 advisory lock 的 worker 运行），当 `SWEEP_ENABLED=true` 且距离上一轮开始已满 `SWEEP_INTERVAL_HOURS` 时：
1. 调用账号清单服务 ①，更新 `t_inv_aws_account`：新账号插入，已有账号更新字段，清单里没有的设为 `active = false` 并记 `inactive_since`；重新出现的恢复 `active = true`。
2. 创建一条 `t_inv_aws_scan_request`（`trigger = schedule`，`priority = 1`）。
3. 为每个活跃账号创建 `sweep_account` 任务：`not_before = 本轮开始时间 + (hash(account_id) mod 错开窗口)`。错开窗口 = 扫描间隔 − 30 分钟，保证最后一个账号在下一轮开始前有时间完成。上一轮的 `sweep_account` 还没结束的账号，这一轮跳过。

账号清单服务调用失败：按退避重试 3 次；仍然失败就用库里现有的账号快照继续，**不把任何账号标为不活跃**，并在扫描请求的 `counts` 里记录 `registryError`。

### 5.2 `POST /scans` 的 `all = true`
如果已经有一轮 `trigger = schedule` 或 `all = true` 的扫描处于 `queued` / `running`，直接返回那一条。否则按 5.1 创建，但 `priority = 0`，并且不做错开（`not_before = now()`）。

## 6. 各任务的执行流程

### 6.1 `sweep_account`
1. 向凭证服务 ② 取得凭证。失败：账号 `access_status = credential_error`，任务失败。
2. `ec2:DescribeRegions(AllRegions=True)`（在 ap-east-1 调用），更新 `t_inv_aws_account_region`，`reachable` 按 `REACHABLE_REGIONS` 计算。
3. 对每个可扫描的区域：`config:DescribeConfigurationRecorders` 和 `DescribeConfigurationRecorderStatus`，写入 `config_recorder`。
4. 为每个可扫描的区域规划子任务：
   - 定时扫描：Config 正在记录、类型在记录范围内、并且是持续记录（不是按天）的类型，合成一个 `config_group`；其余类型各一个 `shard`（Cloud Control）。
   - 按需扫描：每个请求的类型各一个 `shard`。
5. 全局类型（Route 53 Hosted Zone）只规划一次，区域记为 `global`：如果 us-east-1 的记录器包含全局资源，由 us-east-1 的 `config_group` 读取；否则创建一个 `shard`，用 us-east-1 的客户端调用 Cloud Control。

### 6.2 `config_group`
1. 观察时间取各条记录的 `configurationItemCaptureTime`；本次查询开始时间记为 `query_started_at`。
2. 执行，分页读取（每页最多 100 条）：

   ```sql
   SELECT arn, resourceId, resourceName, resourceType, awsRegion, accountId,
          configuration, supplementaryConfiguration, tags, relationships,
          configurationItemCaptureTime, configurationItemStatus
   WHERE resourceType IN (<本组的类型>)
   ```
3. 按类型分组。对每个类型：取得范围租约（第 4 节）→ 写入（第 7 节）→ 删除判断（第 8 节，截止时间 = `query_started_at − CONFIG_LAG_GRACE_MINUTES`）→ 释放租约。
4. 任何一页失败，整组不做删除判断；已经写入的数据保留。

### 6.3 `shard`
1. 观察时间 = 第一次调用前的时间。
2. 列出资源：类型的 `Hooks.list_live` 有值就用它（比如 ECS Service 先列出 Cluster 再逐个列出 Service；AMI 用 EC2 `DescribeImages`）；否则用默认的 Cloud Control `ListResources`。`ListResources` 返回的 `Properties` 不完整的类型（`listReturnsFullProperties = false`），对每个资源再调用 `GetResource`。
3. 有 `Hooks.filter` 就过滤（比如 ECS Task Definition 每个 family 只保留最新的 ACTIVE 版本）；有 `Hooks.enrich` 就补充数据。
4. 按映射转成 Config 格式，交给 Writer 写入；读取完整时再做删除判断（截止时间 = 观察时间）。

### 6.4 `resource`
1. 观察时间 = 调用前的时间。
2. 确定资源：请求是 `{accountId, awsRegion, resourceType, resourceId}` 时直接使用；请求只有 `arn` 时在库里查找，对应多个账号时 API 已经返回 `409 ambiguous_arn`，不会创建任务。再按类型规则把 `resourceId` 换算成 Cloud Control 的 Identifier（规则见 [resource-types.md](resource-types.md)）。
3. 按 ID 读取：类型的 `Hooks.get_live` 有值就用它，否则用 Cloud Control `GetResource`。结果只有三种：
   - **读到了**：映射后交给 Writer 写入。
   - **明确不存在**（`ResourceNotFoundException`，或钩子判断为不存在）：库里有这个资源时，按第 8 节第 6 步处理（原来是 `active` 只改为 `missing`；原来是 `missing` 且有 `absent_in_scan_at` 才确认删除）；库里没有时，任务成功，结果为 `not_found`。
   - **出错**（没权限、被限流、不支持读取等）：任务失败，库里的数据不变。出错永远不能当作不存在。
4. API 的 `wait` 由 API 轮询任务状态实现（每 500 毫秒查询一次，最长 `wait` 秒）。

### 6.5 `confirm_missing`
对范围内所有 `status = 'missing'` 并且 `absent_in_scan_at` 不为空（已有第一条证据）的资源逐个处理，在同一个范围的租约内执行。按 ID 读取的方式和 6.4 相同：

| 结果 | 处理 |
|---|---|
| 明确不存在 | 交给 Writer 确认删除：`status = 'deleted'`，`deleted_at = 观察时间`（遵守 R4，观察时间必须晚于资源的 `observed_at`） |
| 读到了 | 交给 Writer 按 R4 写入，`status = 'active'`，清空 `missing_since`、`absent_in_scan_at`，记一条 `missing_but_found` 日志 |
| 类型不支持 Cloud Control 读取，也没有 `get_live` 钩子 | 用 Config `BatchGetResourceConfig`：`configurationItemStatus` 是 `ResourceDeleted` 或 `ResourceDeletedNotRecorded` 时视为明确不存在，否则保持 |
| 出错 | 保持 `missing`，`missing_checks + 1` |

## 7. 写入（R4）

由 `ResourceWriter` 执行。对每一批 `Observation`（同一个范围内，每批最多 500 条），在一个事务里：
1. **加锁**：资源按 `(account_id, aws_region, resource_type, resource_id)` 识别（ARN 不唯一，见 DESIGN.md 5.1）。按这个键排序执行 `SELECT … FOR UPDATE`，避免死锁。
2. **叠加**（只对 Cloud Control 和服务 API 来源）：把转换结果叠加到**刚锁住的**现有 `configuration` 上。映射覆盖到的字段以新数据为准，新数据里没有就视为被清空；映射覆盖不到的字段保留原值。Config 来源直接使用。
3. **脱敏**：按资源类型配置的路径，对 `configuration` 和 `live_raw` 都执行。这是唯一的脱敏关卡，之前的数据只在内存里。
4. **内容哈希**：`sha256(resourceName, tags, configuration 去掉会自己变化的字段)`，JSON 按键排序后计算。
5. **写入**：
   - 新资源：插入，`first_seen_at = last_changed_at = 观察时间`，计入 `new`。
   - 已有资源，且 `observed_at` 晚于库里的值：
     - 哈希不同：更新所有字段，`last_changed_at = 观察时间`，计入 `updated`。
     - 哈希相同：只更新 `observed_at`、`observed_source` 和时间字段（不改内容）。
     - 状态是 `missing` 或 `deleted`：恢复为 `active`，清空 `missing_since`、`absent_in_scan_at`、`deleted_at`。
   - 已有资源，`observed_at` 不晚于库里的值：跳过（R4）。
   - 只有内容真正被更新时，才同时整体替换这个资源在 `t_inv_aws_resource_relation` 里的记录，并按 账号 + 区域 + 类型 + resourceId 解析 `to_resource_id`。
6. 把 `new`、`updated` 的数量累加到所属扫描请求的 `counts`。

## 8. 删除判断（R5、R6）

只在范围的扫描**完整成功**后执行。

1. **候选**：这个范围内 `status = 'active'`、不在本次结果里、并且 `observed_at < 截止时间` 的资源。截止时间：Cloud Control 和服务 API 来源是观察时间；Config 来源是 `query_started_at − CONFIG_LAG_GRACE_MINUTES`。本次结果的 `resource_id` 先写入临时表，再用 `NOT EXISTS` 比较。
2. **整批保护**：如果 `候选数 > MISSING_GUARD_MIN` 且 `候选数 / 范围内 active 资源数 > MISSING_GUARD_RATIO`，并且范围的 `guard_state` 不是 `confirmed`：不处理任何候选，`guard_state = 'abnormal'`，记录 `guard_detected_at`，结束。
3. 否则候选全部设为 `status = 'missing'`、`missing_since = 观察时间`、`absent_in_scan_at = 截止时间`（第一条证据），计入 `missing`。范围内原来就是 `missing`、这次也没出现的资源（比如之前由单资源刷新标记的），同样补上 `absent_in_scan_at`。然后为这个范围创建一个 `confirm_missing` 任务（同一个范围已经有排队中的就不重复创建）。
4. `guard_state = 'confirmed'` 的范围在这一次处理完后恢复为 `normal`。
5. 每轮全量扫描对还处于 `missing` 的资源再确认一次，不会自动变成删除。
6. **单资源刷新发现明确不存在时**：资源原来是 `active` 的，只设为 `missing`、`missing_since = 观察时间`，不设 `absent_in_scan_at`；原来是 `missing` 并且已有 `absent_in_scan_at` 的，确认删除。

状态变化：

```
active ──完整扫描没看到，并通过整批保护──> missing（有第一条证据）
active ──单资源刷新明确不存在──> missing（只有第二条证据，不会被删除）
missing（没有第一条证据）──之后的完整扫描也没看到──> missing（补上第一条证据）
missing（有第一条证据）──按 ID 读取明确不存在──> deleted
missing ──再次被看到，或按 ID 读取读到了──> active（清空证据）
deleted ──再次被看到──> active
```

## 9. 错误分类

| error_code | 来源 | 重试 | 页面上的说明 | 处理建议 |
|---|---|---|---|---|
| `access_denied` | `AccessDeniedException`、`AccessDenied`、`UnauthorizedOperation` | 否 | 没有读取权限 | 检查这个账号的 ADFS-readonly 角色是否还附加着 ReadOnlyAccess |
| `credential_error` | 凭证服务 ② 返回错误或超时 | 是 | 无法获取凭证 | 检查凭证服务和这个账号的角色配置 |
| `throttled` | botocore 重试用完后仍是 `ThrottlingException` 等 | 是 | 被 AWS 限流 | 通常会自动恢复；经常出现时调低 `PER_ACCOUNT_CONCURRENCY` |
| `endpoint_unreachable` | `EndpointConnectionError`、连接超时 | 是 | 无法连接 AWS 服务 | 检查这个区域的 VPC endpoint、私有托管区和 peering 路由 |
| `region_disabled` | `OptInRequired`，或区域未开启时的认证失败 | 否 | 区域未开启 | 无需处理 |
| `unsupported` | Cloud Control `UnsupportedActionException`、`TypeNotFoundException` | 否 | 这个类型不支持读取 | 检查资源类型配置的数据来源设置 |
| `registry_error` | 账号清单服务 ① 返回错误或超时 | 是 | 无法获取账号清单 | 检查账号清单服务；本轮使用现有的账号快照 |
| `worker_lost` | 心跳超时且重试用完 | — | 处理任务的程序中断了 | 查看任务日志 |
| `internal` | 其他异常 | 是 | 内部错误 | 查看任务日志 |

## 10. 账号权限状态

每个 `sweep_account` 结束时，根据这个账号本轮各任务的结果计算 `access_status`：

| 情况 | access_status |
|---|---|
| 取不到凭证 | `credential_error` |
| 所有调用都是 `access_denied` | `access_denied` |
| 部分区域或类型是 `access_denied` | `partial` |
| 其他 | `ok`，并更新 `last_scan_ok_at` |

`access_error` 记录最近一条错误的说明文字。

## 11. 优雅退出

ECS 停止任务时发送 SIGTERM，`stopTimeout` 为 120 秒：
1. 停止领取新任务。
2. 正在运行的任务处理完当前这一页后停止：数据已写入的部分保留，不做删除判断，释放租约，任务放回 `queued`（`attempt` 不增加）。
3. 负责调度的 worker 释放 advisory lock，由其他实例接手。
4. 60 秒内还没结束的线程放弃，交给心跳回收处理。

## 12. 清理

负责调度的 worker 每天 03:00（UTC）执行：
- 删除 `finished_at` 早于 `TASK_RETENTION_DAYS` 天的任务；删除 `finished_at` 早于 `REQUEST_RETENTION_DAYS` 天、并且已经没有任务的扫描请求。
- 删除 `deleted_at` 早于 `DELETED_PURGE_DAYS` 天的资源。
- 删除 `inactive_since` 早于 `INACTIVE_ACCOUNT_PURGE_DAYS` 天的账号，以及它的资源、区域和范围记录。

每次最多删除 10,000 行，循环直到完成，避免长事务。
