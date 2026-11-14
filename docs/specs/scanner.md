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

**分层**（见 DESIGN.md 10.1）：任务处理函数（`worker/handlers.py` 的 `Scanner`，每种任务一个方法）取得租约 → `Discovery` 读取，产出 `Observation`，并标记读取是否完整 → `ResourceWriter` 写入和做删除判断。读取是**逐页**进行的：`Discovery` 返回的是一页一页的迭代器，处理函数在租约内消费它，每读到一页就写一页，页与页之间做检查点（第 3.3 节），期间心跳持续续约；只有读取完整（所有分页、上级资源、补充数据都成功）时才做删除判断。

按需扫描（`POST /scans`）直接为每个账号创建 `sweep_account`，但标记为按需（`priority = 0`）：它拆出的子任务继承这个优先级，一律用 Cloud Control（AMI 用服务 API），不创建 `config_group`，也不读取 Config 记录器。

`resource` 任务不取范围租约：R4 的 `observed_at` 条件保护它的写入，任务表上的部分唯一索引保证同一个资源同时只有一个刷新任务。

**围栏令牌**：每次领取任务都生成一个新的 `scan_task.claim_token`；范围租约上记着持有者的 `scan_shard.lease_token`。心跳、取得和续约租约、释放租约、检查点（每页之后）、结束任务都必须带着当前的令牌；任务被回收并被别人重新领取后，旧令牌什么也匹配不到，旧 worker 的调用全部被拒绝（`TaskLost`）。收到 `TaskLost` 的 worker 立即停止，不再写任何数据、不释放租约、不改任务状态，由新的持有者负责收尾。

## 2. 任务状态

```
queued ──领取──> running ──成功──> succeeded
   │                │
   │                ├──失败，attempt < max_attempts──> queued（not_before = 现在 + 退避时间）
   │                ├──失败，attempt = max_attempts，或错误不可重试──> failed
   │                ├──范围正忙或刚扫过（定时任务）──> skipped
   │                ├──范围正忙（按需任务）──> queued（排在范围后面，attempt 不增加，见 4.1）
   │                ├──worker 优雅退出──> queued（attempt 不增加，见第 11 节）
   │                ├──扫描被取消，没有别的扫描还需要它──> cancelled
   │                └──心跳超时（worker 挂了）──> queued；attempt 已达上限则 failed
   ├──被合并──> coalesced（coalesced_into 指向合并后的任务）
   └──扫描被取消──> cancelled
```

- **退避时间**：`30 秒 × 2^(attempt-1)`，加 0–30 秒随机值。
- **attempt** 在领取时加一；回收（心跳超时）不再额外加一，一次中断的运行就算用掉一次。
- **扫描请求的状态**：子任务全部结束后汇总。`skipped` 和 `coalesced` 算成功（它们代表的数据由别的运行产生）。没有失败是 `succeeded`；有失败也有成功是 `partial`；全部失败是 `failed`；被取消的请求保持 `cancelled`。汇总时把任务数量按状态写入 `counts.tasks`，把任务 `stats` 里的 `new`、`updated`、`missing`、`deleted` 求和写入 `counts.resources`；没有任务上报这些数字时不写 `resources`。`counts` 里的其他键（`accounts`、`skippedAccounts`、`registryError`）保留。
- 没有任何任务的请求（比如所有账号都因上一轮未结束而跳过）创建时就是 `succeeded`。

## 3. 任务队列

### 3.1 领取

每个 worker 有 `WORKER_THREADS` 个线程，其中 `WORKER_ONDEMAND_THREADS` 个只领按需任务（`priority = 0`）。

```sql
UPDATE t_inv_aws_scan_task
   SET status = 'running', worker_id = :worker_id, claim_token = :claim_token,
       attempt = attempt + 1, started_at = :now, heartbeat_at = :now, finished_at = NULL
 WHERE id = (
   SELECT t.id FROM t_inv_aws_scan_task t
    WHERE t.status = 'queued'
      AND t.not_before <= :now
      AND t.priority <= :max_priority
      AND (SELECT count(*) FROM t_inv_aws_scan_task r
            WHERE r.account_id = t.account_id AND r.status = 'running') < :per_account_concurrency
    ORDER BY t.priority, t.not_before
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *;
```

每个账号的并发上限是软上限：两个 worker 同时领取时偶尔会多一个，对防止限流来说足够。查询「这个账号正在跑几个任务」用任务表上的部分索引（`WHERE status = 'running'`，按 `account_id`）。领到任务的同时，把所属的扫描请求从 `queued` 改为 `running`。

### 3.2 心跳与回收

| 参数 | 值 |
|---|---|
| 心跳间隔 | 30 秒：一个心跳线程为这个 worker 所有在跑的任务带着各自的令牌更新 `heartbeat_at`，同时续租这些任务持有的范围租约（再延长 5 分钟）；令牌不匹配的任务记一条警告。同一个线程还会刷新健康检查文件 `WORKER_HEARTBEAT_FILE` |
| 心跳超时 | 2 分钟没有心跳，视为 worker 已挂 |
| 回收 | 负责调度的 worker 每分钟检查一次（与 `SWEEP_ENABLED` 无关）：超时的 `running` 任务放回 `queued`，清空 `claim_token`（旧 worker 被围栏挡在外面）；`attempt` 已达上限的标为 `failed`（`error_code = worker_lost`）并汇总所属请求。回收**不释放**范围租约：租约在最后一次续约 5 分钟后自己到期；同一个任务被重新领取时，会接管自己原来留下的租约 |

### 3.3 取消
`POST /scans/{id}/cancel` 把这个请求下所有 `queued` 任务标为 `cancelled`，并清掉指向它们的 `pending_task_id`。**任务可能被多个请求共用**（别的请求的任务被合并进来，见 4.1）：只有没有任何未取消的请求还需要某个排队中的任务时，才把它标为取消；否则保留，等最后一个需要它的请求也被取消时再取消。取消一个已经结束的请求不改变任何东西。

正在运行的任务每处理完一页（以及每次读取之前、做删除判断之前、释放每个租约之后）做一次检查点：worker 正在关闭、令牌已不是自己的、或者所有需要这个任务的请求都已取消，就停止，不做删除判断，不再写数据。共用的任务只要还有一个未取消的请求需要它就继续跑。`POST /scans/{id}/retry-failed` 对已取消的请求返回 409 `scan_cancelled`。

**行锁的全局顺序**：范围行（`scan_shard`，按 `(account_id, region, resource_type)`）→ 任务行（按 id 升序）→ 扫描请求行（按 id 升序）。所有事务按这个顺序取锁，不回头，所以取消、领取和结束任务之间不会互相等待成死锁。

## 4. 范围租约（R1–R3）

范围的主键是 `(account_id, region, resource_type)`。`config_group` 一开始就对它覆盖的**所有**类型各取一个租约，读取（分页）期间一直持有，直到这个类型的删除判断做完。租约只给带着当前令牌的任务：令牌已经不是任务当前的，取得租约返回 `LOST`（任务停止，什么都不写）。

### 4.1 取得租约

```sql
INSERT INTO t_inv_aws_scan_shard (account_id, region, resource_type)
VALUES (:a, :r, :t) ON CONFLICT DO NOTHING;

UPDATE t_inv_aws_scan_shard
   SET lease_task_id = :task_id, lease_token = :claim_token,
       lease_expires_at = :now + interval '5 minutes', last_started_at = :now,
       pending_task_id = CASE WHEN pending_task_id = :task_id THEN NULL ELSE pending_task_id END
 WHERE (account_id, region, resource_type) = (:a, :r, :t)
   AND (lease_task_id IS NULL OR lease_expires_at < :now OR lease_task_id = :task_id)
RETURNING *;
```

`lease_task_id = :task_id` 让同一个任务可以重复取得自己的租约：重试，或者被回收后重新领取，会接管自己留下的过期租约。没有返回行，说明范围正忙：
- **定时任务**（`config_group` 的某个类型除外，它只是少读这个类型）：标为 `skipped`，`stats.reason = "busy"`，因为正在跑的任务已经覆盖了它。
- **按需任务**：当前任务**自己**放回 `queued`（`attempt` 不增加，`not_before` = 租约到期时间），成为这个范围的 `pending_task_id`；释放租约时会立刻唤醒它。如果原来已经有一个排队中的任务，**只有新任务覆盖了它**（种类、账号、区域、类型、数据源都相同，且都不是 `resource`）时，才把原来的标为 `coalesced`、`coalesced_into` 指向新的，并由新的接替 `pending_task_id`；不能覆盖时，新任务不占 `pending_task_id`，只是等到租约到期后再试。

### 4.2 跳过刚扫过的范围（R3）
定时任务取得租约前，如果 `last_succeeded_at > now() - 30 分钟`，直接标为 `skipped`（`stats.reason = "fresh"`）。按需任务和 `confirm_missing`（它紧接在刚成功的扫描之后）不检查。`config_group` 里某个类型刚扫过或被占用，只少读这个类型。

### 4.3 释放
任务结束时（无论成功或失败）必须带着当前令牌释放：令牌或 `lease_task_id` 对不上（租约已过期并被别人拿走）就什么也不改，返回失败。清空 `lease_task_id`、`lease_token`、`lease_expires_at`，记下 `last_task_id`；成功则更新 `last_succeeded_at`、清零 `consecutive_failures`，失败则记录 `last_error_code`、`consecutive_failures + 1`；停止或取消时不记录结果（既不算成功也不算失败）。如果 `pending_task_id` 不为空，把那个任务的 `not_before` 设为现在，并清空 `pending_task_id`。worker 主循环在任务结束后还会释放这个任务仍持有的所有租约，作为兜底。

## 5. 一轮全量扫描

### 5.1 创建（调度器）
调度器每分钟运行一次。每个 worker 都运行调度器，但只有持有 advisory lock 的那一个真正执行：它在专用连接上用会话级的 `pg_try_advisory_lock` 选主，连接断了锁就释放，由别的 worker 在下一次检查时接手。锁的 key 由 `<名称>:<DATABASE_SCHEMA>` 取 SHA-256 的前 8 字节、按有符号 64 位整数计算（共用同一个数据库、schema 不同的部署或测试互不干扰）；名称是 `aws-inventory-discovery:scheduler`（选主）和 `aws-inventory-discovery:full-sweep`（见下）。选上之后每分钟依次做：开始一轮扫描（`SWEEP_ENABLED=true` 且距离上一轮的创建时间已满 `SWEEP_INTERVAL_HOURS`）、回收失联的任务（3.2，与 `SWEEP_ENABLED` 无关）、每天 03:00（UTC）的清理（第 12 节）。某一步失败只记日志，不影响其他步骤。

开始一轮扫描：
1. 调用账号清单服务 ①，更新 `t_inv_aws_account`：新账号插入，已有账号更新字段，清单里没有的设为 `active = false` 并记 `inactive_since`；重新出现的恢复 `active = true`。清单为空视为失败（否则会把所有账号标为不活跃）。
2. **全量扫描的创建用 `pg_advisory_xact_lock`（`full-sweep`）串行化**，调度器和 `POST /scans {all: true}` 共用这把锁；在锁内再确认一次「这一轮是否已经到期」。
3. 创建一条 `t_inv_aws_scan_request`（`trigger = schedule`，`priority = 1`，`scope = {all: true}`）。
4. 为每个活跃账号创建 `sweep_account` 任务：`not_before = 本轮开始时间 + 错开偏移`。偏移是账号 ID 的 SHA-256 前 8 字节对错开窗口取模（Python 内置的 `hash()` 每个进程都不同，不能用）。错开窗口 = 扫描间隔 − 30 分钟（默认 5.5 小时），保证最后一个账号在下一轮开始前有时间完成。上一轮的 `sweep_account` 还没结束（`queued` / `running`）的账号，这一轮跳过，数量记在请求 `counts.skippedAccounts`；`counts.accounts` 是实际创建的数量。
5. **子任务入队去重**：上一轮的 `sweep_account` 已经规划完、但它的子任务还在排队（积压）时，这一轮不再为同一个范围重复入队。定时扫描（`priority = 1`）规划子任务时，如果同一账号已经有排队中的、定时的同类子任务覆盖这个范围，就不创建：`config_group` 按账号 + 区域匹配（`config_group` 运行时才重新算出 Config 负责的类型，所以排队中的那个覆盖整个区域的类型集合）；`shard` 按账号 + 区域 + 类型 + 来源匹配。跳过的数量记在 `sweep_account` 的 `stats.skipped`。按需扫描（`priority = 0`）不去重，必须产生新数据（R2）。检查和插入在同一个事务里，用账号级的 advisory xact lock 串行化（`aws-inventory-discovery:enqueue:<账号>`，不在 shard → task → request 的行锁顺序之内），并用 `t_inv_aws_scan_task` 上按账号的排队中部分索引查找。

账号清单服务调用失败：按退避重试（2、4、8 秒，共 3 次重试）；仍然失败就用库里现有的账号快照继续，**不把任何账号标为不活跃**，并在扫描请求的 `counts` 里记录 `registryError`。

### 5.2 `POST /scans` 的 `all = true`
先由 API 调用一次账号清单服务 ① 刷新账号快照（最多重试 1 次；失败就用现有快照，清单为空也不处理）。然后在 `full-sweep` 锁内检查：如果已经有一轮 `scope.all = true`（定时的或按需的）的扫描处于 `queued` / `running`，直接返回那一条（HTTP 200）。否则按 5.1 创建，但 `priority = 0`，并且不做错开（`not_before = now()`），同样跳过上一轮 `sweep_account` 还没结束的账号。

### 5.3 `POST /scans` 的账号列表
超过 `MAX_SCAN_ACCOUNTS` 返回 422 `scope_too_large`。账号必须在快照里并且是活跃的；库里没有的账号，API 先调用一次账号清单服务 ① 确认：清单里有就把快照同步下来再创建，清单里也没有返回 404 `account_not_found`（`details.accountIds` 列出），账号清单服务不可用返回 503 `dependency_unavailable`。每个账号一个按需 `sweep_account`（`priority = 0`，不错开）；请求的 `scope` 保存规范化后的 `accountIds`、`awsRegions`、`resourceTypes`（排序、去重）。

## 6. 各任务的执行流程

### 6.1 `sweep_account`
1. 向凭证服务 ② 取得凭证。失败：任务以 `credential_error` 失败（可重试），账号的 `access_status` 在任务结束后重新计算（第 10 节）。
2. `ec2:DescribeRegions(AllRegions=True)`，更新 `t_inv_aws_account_region`（`opt_in_status`、`reachable`、`checked_at`），`reachable` 按 `REACHABLE_REGIONS` 计算。**调用的区域是 `REACHABLE_REGIONS` 里第一个默认开启（不是 opt-in）的区域**：opt-in 区域（如 ap-east-1）在目标账号里可能没开启，在那里调用会失败。`REACHABLE_REGIONS` 里没有默认开启的区域时，任务不可重试地失败（`internal`），错误信息写明原因，要求加一个（比如 us-east-1）。opt-in 区域的清单来自 AWS 官方文档，写在 `aws/regions.py`。
3. 可扫描的区域 = 在 `REACHABLE_REGIONS` 里、`OptInStatus` 是 `opt-in-not-required` 或 `opted-in`、并且在请求的 `awsRegions` 里（请求没指定区域时不限）。**只有定时扫描**读取 Config 记录器：`config:DescribeConfigurationRecorders` 和 `DescribeConfigurationRecorderStatus`，写入 `config_recorder`（`recording`、`allSupported`、`includeGlobal`、`resourceTypes`、`excludedTypes`、`dailyTypes`；`dailyTypes = ["*"]` 表示默认按天记录）。读取失败时当作「Config 什么都不记录」，所有类型都规划 Cloud Control，让子任务自己报告真实的错误。
4. 为每个可扫描的区域规划子任务（类型限定在请求的 `resourceTypes` 里，没指定则全部）：
   - 用服务 API 读取的类型（AMI）：各一个 `shard`，定时和按需都一样。
   - 定时扫描：Config 支持这个类型、记录器正在记录它、并且是持续记录（不是按天）的，合成一个 `config_group`；其余能用 Cloud Control `ListResources` 的类型各一个 `shard`。
   - 按需扫描：每个类型各一个 `shard`，不使用 Config。
   - 重新运行同一个 `sweep_account`（重试）时，已经为这个请求和账号创建过的子任务不会重复创建。
5. 全局类型（Route 53 Hosted Zone）只在 us-east-1 规划一次，区域记为 `global`：如果 us-east-1 的记录器包含全局资源（`includeGlobal`），由 us-east-1 的 `config_group` 读取；否则创建一个 `shard`，用 us-east-1 的客户端调用 Cloud Control。
6. 任务的 `stats` 是 `{regions, tasks, skipped}`（区域数、新建的子任务数、因 5.1 第 5 条去重而跳过的子任务数）。

### 6.2 `config_group`
1. 先读一次记录器，重新算出这个区域由 Config 负责的类型（全局类型只在 us-east-1 读取）；没有就直接结束。
2. **一开始就对这些类型各取一个范围租约**（第 4 节）。已经被别人占着或刚扫过的类型，这次不读（记入 `skipped`）；一个都取不到，任务结束。
3. 观察时间取各条记录的 `configurationItemCaptureTime`；本次查询开始时间记为 `query_started_at`。分页读取（每页最多 100 条）：

   ```sql
   SELECT arn, resourceId, resourceName, resourceType, awsRegion, accountId,
          configuration, supplementaryConfiguration, tags, relationships,
          configurationItemCaptureTime, configurationItemStatus
   WHERE resourceType IN (<本组取得租约的类型>)
   ```
4. **读一页、写一页、做一次检查点**（第 3.3 节），不是读完再写：每一页到手就交给 Writer（第 7 节），再检查是否被停止、取消或失去所有权，然后才读下一页。同一次查询里每个类型已经看到的 `resourceId` 记在内存里。
5. 读取完整结束后，再做一次检查点，然后对每个类型依次：删除判断（第 8 节，截止时间 = `query_started_at − CONFIG_LAG_GRACE_MINUTES`）→ 释放租约（成功或失败都带上结果）→ 需要的话创建 `confirm_missing` → 检查点。
6. 任何一页失败，整组不做删除判断，所有租约以失败释放，任务以对应的错误码失败；已经写入的数据和统计保留。

### 6.3 `shard`
1. 先取得范围租约（第 4 节）；范围正忙或刚扫过时，任务按 4.1、4.2 处理后直接返回。观察时间 = 第一次调用前的时间。
2. 列出资源，一页一页地读：类型的 `Hooks.list_live` 有值就用它（目前只有 AMI：EC2 `DescribeImages`）；否则用默认的 Cloud Control `ListResources`（每页 100 条）。`ListResources` 返回的 `Properties` 不完整的类型（`listReturnsFullProperties = false`，目前所有 Cloud Control 类型都是），对每个资源再调用 `GetResource`；资源在列出之后、读取之前被删掉（`ResourceNotFoundException`）不算失败，直接略过。
3. 有 `Hooks.filter` 就过滤：过滤函数要看到整个列表，所以这时才把所有页缓冲起来，不再逐页处理（目前没有类型登记 `filter`）；有 `Hooks.enrich` 就补充数据。
4. 按映射转成 Config 格式，**每一页交给 Writer 写入，写完做一次检查点**；全部读完且没有任何错误（所有页、上级资源、逐个读取、补充数据都成功；映射失败算 `internal` 错误）才算「读取完整」，这时再做一次检查点，然后做删除判断（截止时间 = 观察时间）。释放租约后，如果范围里有带第一条证据的 `missing` 资源，创建 `confirm_missing`（同一个范围已经有排队中的就不重复创建）。读取不完整时任务以第一个错误的错误码失败。

### 6.4 `resource`
1. 不取范围租约。观察时间 = 调用前的时间。
2. 确定资源：API 已经把请求换算成 `{accountId, awsRegion, resourceType, resourceId}`（请求只有 `arn` 时在库里查找，对应多个账号时返回 `409 ambiguous_arn`，不会创建任务）。Cloud Control 的 Identifier 这样得到：库里有这个资源时，用它存着的 `configuration` 按类型的 `identity.identifier` 拼出（复合 Identifier 用 `|` 连接）；库里没有时，用类型钩子 `cloudcontrol_identifier` 从 `resourceId` 推出（ECS Service：由 Service ARN 得到 `<Service ARN>|<Cluster ARN>`；ECS Cluster：由 Cluster ARN 得到集群名）；没有钩子但 Identifier 就是 `resourceId` 的类型直接使用；其余情况报错。推不出来的 `resourceId`（比如不带集群名的旧格式 ECS Service ARN）由 API 直接拒绝：`422 validation_error`。
3. 按 ID 读取：类型的 `Hooks.get_live` 有值就用它，否则用 Cloud Control `GetResource`。结果只有三种：
   - **读到了**：映射后交给 Writer 写入。
   - **明确不存在**（`ResourceNotFoundException`，或钩子判断为不存在）：库里有这个资源时，按第 8 节第 6 步处理（原来是 `active` 只改为 `missing`；原来是 `missing` 且有 `absent_in_scan_at` 才确认删除）；库里没有时，任务成功，结果为 `not_found`。任务 `stats.result` 是 `found`、`missing`、`deleted`、`unchanged`、`stale`（观察时间不晚于库里的，被 R4 拒绝）或 `not_found`。
   - **出错**（没权限、被限流、不支持读取等）：任务失败，库里的数据不变。出错永远不能当作不存在。
4. API 的 `wait` 由 API 轮询任务状态实现（每 500 毫秒查询一次，最长 `wait` 秒）。

### 6.5 `confirm_missing`
对范围内所有 `status = 'missing'` 并且 `absent_in_scan_at` 不为空（已有第一条证据）的资源逐个处理，先取得这个范围的租约（`confirm_missing` 不受 R3「刚扫过」的限制；范围正忙时按 4.1 处理）。每次按 ID 读取之前都做一次检查点（每次读取都可能导致删除）。按 ID 读取的方式和 6.4 相同：

| 结果 | 处理 |
|---|---|
| 明确不存在 | 交给 Writer 确认删除：`status = 'deleted'`，`deleted_at = 观察时间`（遵守 R4，观察时间必须晚于资源的 `observed_at`） |
| 读到了 | 交给 Writer 按 R4 写入，`status = 'active'`，清空 `missing_since`、`absent_in_scan_at`、`deleted_at`、`missing_checks`，记一条 `missing_but_found` 日志 |
| 类型不支持 Cloud Control 读取，也没有 `get_live` 钩子 | 用 Config `BatchGetResourceConfig`（一次最多 100 个）：`configurationItemStatus` 是 `ResourceDeleted` 或 `ResourceDeletedNotRecorded` 时视为明确不存在；Config 没返回这个资源，或状态是别的，不构成证据，保持 |
| 出错 | 保持 `missing`，`missing_checks + 1`；任务在处理完其余资源后以第一个错误的错误码失败 |

## 7. 写入（R4）

由 `ResourceWriter` 执行。读取是一页一页来的（第 6 节），每一页交给 `write`；一页里的 `Observation` 每批最多 500 条，**每批在一个事务里**（同一批里同一个资源出现多次，只保留观察时间最新的一条）：
1. **加锁**：资源按 `(account_id, aws_region, resource_type, resource_id)` 识别（ARN 不唯一，见 DESIGN.md 5.1）。按这个键排序执行 `SELECT … FOR UPDATE`，避免死锁。
2. **叠加**（只对 Cloud Control 和服务 API 来源）：把转换结果叠加到**刚锁住的**现有 `configuration` 上。映射覆盖到的字段以新数据为准，新数据里没有就视为被清空；映射覆盖不到的字段保留原值。Config 来源直接使用。
3. **脱敏**：按资源类型配置的路径，对 `configuration` 和 `live_raw` 都执行。这是唯一的脱敏关卡，之前的数据只在内存里。
4. **内容哈希**：`sha256(resourceName, tags, configuration 去掉会自己变化的字段)`，JSON 按键排序后计算。
5. **写入**：
   - 新资源：插入（冲突时说明别的 worker 抢先插入了，改走更新路径），`first_seen_at = last_changed_at = 观察时间`，计入 `new`。
   - 已有资源，且 `observed_at` 晚于库里的值：
     - 哈希不同：更新所有字段，`last_changed_at = 观察时间`，计入 `updated`，并整体替换这个资源在 `t_inv_aws_resource_relation` 里的记录。
     - 哈希相同：只更新 `observed_at`、`observed_source` 和时间字段（不改内容），计入 `unchanged`。**关系不在哈希里，也不算资源变化**：只有关系（按集合比较，Config 不保证顺序）变了时，只更新关系和关系表，不计入 `updated`，不动 `last_changed_at`。
     - 状态是 `missing` 或 `deleted`：恢复为 `active`，清空 `missing_since`、`absent_in_scan_at`、`deleted_at`、`missing_checks`，计入 `restored`。
   - 已有资源，`observed_at` 不晚于库里的值：跳过（R4）。
   - 关系表里 `to_resource_id` 按 账号 + 区域 + 类型 + resourceId 解析。**迟到的解析**：新资源插入后，同一个账号里（区域相同，目标是全局资源时不限区域）那些 `to_resource_id` 还是空的关系，指向它。
6. **统计在同一个事务里**写入 `scan_task.stats`（累加 `new`、`updated`、`unchanged`、`restored`；删除步骤累加 `missing`、`deleted`），所以任务上报的数字就是它已经提交的写入，任务失败或重试也不丢。所属扫描请求的 `counts.resources` 在任务结束时由各任务的 `stats` 汇总（见第 2 节）。

## 8. 删除判断（R5、R6）

只在范围的扫描**完整成功**后执行。

1. **候选**：这个范围内 `status = 'active'`、不在本次结果里、并且 `observed_at < 截止时间` 的资源。截止时间：Cloud Control 和服务 API 来源是观察时间；Config 来源是 `query_started_at − CONFIG_LAG_GRACE_MINUTES`。「本次结果」是读取过程中记下的 `resource_id` 集合。整个判断在范围行（`scan_shard`）的行锁内、一个事务里完成。
2. **整批保护**：如果 `候选数 > MISSING_GUARD_MIN` 且 `候选数 / 范围内 active 资源数 > MISSING_GUARD_RATIO`，并且范围的 `guard_state` 不是 `confirmed`：不处理任何候选，`guard_state = 'abnormal'`，记录 `guard_detected_at`、`candidate_count`（`GET /scan-scopes` 显示的「未发现」数量）和候选资源的 ID（`candidate_ids`，供核对页读取），结束。
3. 否则候选全部设为 `status = 'missing'`、`missing_since = 观察时间`、`absent_in_scan_at = 截止时间`（第一条证据）、`missing_checks = 0`，计入 `missing`。范围内原来就是 `missing`、这次也没出现的资源（比如之前由单资源刷新标记的），同样补上 `absent_in_scan_at`。然后，范围里只要有带第一条证据的 `missing` 资源，就为这个范围创建一个 `confirm_missing` 任务（同一个范围已经有排队中的就不重复创建）。
4. `guard_state` 是 `confirmed` 或 `abnormal` 的范围，在这一次没有触发整批保护的处理完后恢复为 `normal`，清空 `candidate_count` 和 `candidate_ids`。
5. 每轮全量扫描对还处于 `missing` 的资源再确认一次，不会自动变成删除。
6. **单资源刷新发现明确不存在时**（`record_gone`）：资源原来是 `active` 的，只设为 `missing`、`missing_since = 观察时间`，不设 `absent_in_scan_at`；原来是 `missing` 并且已有 `absent_in_scan_at` 的，确认删除。**不存在的观察也必须晚于资源现有的 `observed_at`（R4），否则不处理；被接受的观察（不管结果是什么）都把 `observed_at`、`observed_source` 推进到这个时间**，这样更早开始、还没写完的列表扫描就无法再把它「恢复」。按 ID 读取出错时，资源保持 `missing`，`missing_checks + 1`。

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
| `credential_error` | 凭证服务 ② 返回错误或超时；AWS 返回 `UnrecognizedClientException`、`InvalidClientTokenId`、`ExpiredToken(Exception)` | 是 | 无法获取凭证 | 检查凭证服务和这个账号的角色配置 |
| `throttled` | botocore 重试用完后仍是 `Throttling`、`ThrottlingException`、`TooManyRequestsException`、`RequestLimitExceeded` 等，或 HTTP 429 | 是 | 被 AWS 限流 | 通常会自动恢复；经常出现时调低 `PER_ACCOUNT_CONCURRENCY` |
| `endpoint_unreachable` | `EndpointConnectionError`、连接超时、读取超时 | 是 | 无法连接 AWS 服务 | 检查这个区域的 VPC endpoint、私有托管区和 peering 路由 |
| `region_disabled` | `OptInRequired`、`AuthFailure`（区域未开启时的认证失败） | 否 | 区域未开启 | 无需处理 |
| `unsupported` | Cloud Control `UnsupportedActionException`、`TypeNotFoundException` | 否 | 这个类型不支持读取 | 检查资源类型配置的数据来源设置 |
| `registry_error` | 账号清单服务 ① 返回错误或超时 | 是 | 无法获取账号清单 | 检查账号清单服务；本轮使用现有的账号快照。注意：它不会成为任务的 `error_code`，只以 `registryError` 记在扫描请求的 `counts` 里 |
| `worker_lost` | 心跳超时且重试用完（由回收设置） | —（回收放回队列，次数用完才标为失败） | 处理任务的程序中断了 | 查看任务日志 |
| `internal` | 其他异常，包括映射失败、`REACHABLE_REGIONS` 里没有默认开启的区域 | 是（后者不可重试） | 内部错误 | 查看任务日志 |

botocore 的重试用 `adaptive` 模式，最多 10 次，连接超时 10 秒、读取超时 60 秒；这些重试用完之后才轮到上面的任务级重试（最多 `max_attempts = 3` 次，退避见第 2 节）。

## 10. 账号权限状态

**每个任务结束（成功、失败，或失败后放回队列重试）之后**，重新计算这个账号的 `access_status`：取这个扫描请求里这个账号所有已经结束（`succeeded` / `failed`）的 `sweep_account`、`config_group`、`shard`、`confirm_missing` 任务的结果（`skipped`、`coalesced`、还在排队或运行的不算；已经拆出子任务并成功的 `sweep_account` 不算，由子任务上报）。计算时**锁住账号行**，所以两个任务同时结束时，后一个能看到前一个的结果，最后存下的是合并后的状态。还没有任何任务上报时不改。

| 情况 | access_status |
|---|---|
| 有任务以 `credential_error` 失败 | `credential_error` |
| 所有上报的任务都以 `access_denied` 失败 | `access_denied` |
| 部分任务是 `access_denied` | `partial` |
| 其他 | `ok`，并更新 `last_scan_ok_at` |

`access_error` 记录最近一个失败任务的错误说明，状态为 `ok` 时清空；`access_checked_at` 记录计算时间。账号不在快照里（比如已被清理）时只记一条警告。

## 11. 优雅退出

ECS 停止任务时发送 SIGTERM，`stopTimeout` 为 120 秒：
1. 停止领取新任务。
2. 正在运行的任务在下一个检查点（每页之后）停止：数据已写入的部分保留，不做删除判断，释放租约（不记录成功或失败），任务放回 `queued`（`attempt` 不增加，`not_before = 现在`）。
3. 负责调度的 worker 释放 advisory lock，由其他实例接手。心跳线程一直运行到最后一个处理函数结束。
4. 60 秒内还没结束的线程放弃，交给心跳回收处理（退出码 1）。

## 12. 清理

负责调度的 worker 每天 03:00（UTC）执行（当天只执行一次）：
- 删除 `finished_at` 早于 `TASK_RETENTION_DAYS` 天的任务（先把合并到它们上面的任务的 `coalesced_into` 清空）；删除 `finished_at` 早于 `REQUEST_RETENTION_DAYS` 天、并且已经没有任务的扫描请求。
- 删除 `deleted_at` 早于 `DELETED_PURGE_DAYS` 天的资源。
- 删除 `inactive_since` 早于 `INACTIVE_ACCOUNT_PURGE_DAYS` 天的账号，以及它的资源、区域和范围记录。

每个删除步骤都在事务里锁定候选并复查资格：任务已经结束且早于保留期，资源仍是 `deleted` 且早于保留期，账号仍不活跃且 `inactive_since` 早于保留期。账号清理与 `sync_accounts` 共用按账号的 advisory lock，清理期间恢复活跃的账号不会被删；资源清理在 `DELETE` 外层重复状态和保留期条件，等锁之后重新判断。

每次最多删除 10,000 行，循环直到完成，避免长事务。
