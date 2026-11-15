# 规格：自定义报表

本文是 [DESIGN.md](../DESIGN.md) 第 1 节 v2 范围和第 9.3 节报表页面的实现细节，也是 [DECISIONS.md](../DECISIONS.md) D-36 的具体化。报表原来的叫法是「巡检」，指的是同一个功能。

## 1. 范围与边界

- 报表是在**当前清单**上的一次规则匹配：管理员保存一条规则，打开页面时按当前数据重新计算，列出满足规则的资源。**导出属于报表**（CSV / JSON Lines）。
- 只读：报表不写 `t_inv_aws_resource`，不调用任何目标账号的 AWS API，不改变扫描流程。
- 不保存结果（D-07）：没有报表结果表（历史上常叫 findings 表），没有「上次什么时候匹配到几条」的历史。页面上的计数来自当前数据，可缓存 60 秒。外部 findings 服务 ③ 的**当前汇总**是另一回事，见第 2 节和 D-37。
- 规则是**数据，不是代码**：没有 JS 沙箱。字段、操作符和值在保存时校验，查询由后端编译成 SQL。
- 权限暂不做（D-09）：任何能打开 dashboard 的人都可以新建、修改、删除报表；正式接入时再加「管理员定义、所有人查看」。
- 推送和外部告警不在范围内（DESIGN 第 14 节）。

## 2. 数据模型

迁移 `0006` 新增一张表（全部表放在 `DATABASE_SCHEMA`）：

```sql
CREATE TABLE t_inv_aws_report (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name                text        NOT NULL,
  description         text,
  subject_type        text,                 -- 资源类型；为空表示所有类型
  definition_version  smallint    NOT NULL DEFAULT 1,
  definition          jsonb       NOT NULL, -- 条件树、列、版本（见第 3 节）
  enabled             boolean     NOT NULL DEFAULT true,
  created_by          text,
  updated_by          text,
  created_at          timestamptz NOT NULL,
  updated_at          timestamptz NOT NULL
);
-- 唯一索引 uq_t_inv_aws_report_name (name)；普通索引 ix_t_inv_aws_report_enabled (enabled)
```

`subject_type` 单独成列只为列表展示和筛选；**求值时以 `definition.subjectType` 为准**（两者始终同步写入）。`definitionVersion` 目前只有 `1`，不认识的版本在保存和求值时都返回 422，方便以后改格式。

迁移 `0007` 再新增 findings 当前汇总表（同样放在 `DATABASE_SCHEMA`）：

```sql
CREATE TABLE t_inv_aws_finding_summary (
  account_id      char(12)    NOT NULL,
  aws_region      text        NOT NULL,
  resource_type   text        NOT NULL,
  resource_id     text        NOT NULL,
  critical_count  integer     NOT NULL DEFAULT 0,
  high_count      integer     NOT NULL DEFAULT 0,
  medium_count    integer     NOT NULL DEFAULT 0,
  low_count       integer     NOT NULL DEFAULT 0,
  max_score       double precision,
  checked_at      timestamptz NOT NULL,
  PRIMARY KEY (account_id, aws_region, resource_type, resource_id)
);
```

这张表只存**当前**汇总（D-07、D-37）：每个资源一行，没有行表示「还没检查过」，查询时是 NULL，**不等于 0 条发现**。它不存单条 finding，也不存历史；资源被清理时这一行一起删除。写入只发生在 worker 扫描声明了 `findings` 的类型时（目前是 AMI），并且和其他资源写入一样受任务围栏保护。

## 3. 定义格式

```json
{
  "subjectType": "AWS::ECS::TaskDefinition",
  "where": {
    "all": [
      { "field": "image", "op": "contains", "value": "123456789012.dkr.ecr." },
      { "field": "inboundRelationshipCount", "op": "gte", "value": 1 }
    ]
  },
  "columns": ["image", "family"]
}
```

### 3.1 条件节点

一个节点**只能**是下面四种之一，否则 422：

| 形式 | 说明 |
|---|---|
| `{ "all": [节点, ...] }` | 全部满足，至少一个子节点 |
| `{ "any": [节点, ...] }` | 满足任一，至少一个子节点 |
| `{ "not": 节点 }` | 取反 |
| `{ "field": string, "op": string, "value": 任意, "key": string? }` | 一次字段比较 |

`key` 只用于标签条件。限制：深度 ≤ 10、节点数 ≤ 50、单个文本值 ≤ 500 字符、`in` / `notIn` 列表 ≤ 100 项。

### 3.2 操作符

操作符必须属于字段类型允许的集合：

| 类型 | 允许的操作符 |
|---|---|
| `text` | `eq` `neq` `in` `notIn` `contains` `startsWith` `exists` `notExists` |
| `enum` | `eq` `neq` `in` `notIn` `exists` `notExists` |
| `number` | `eq` `neq` `in` `notIn` `gt` `gte` `lt` `lte` `exists` |
| `bool` | `eq` `neq` |
| `datetime` | `gt` `gte` `lt` `lte` `olderThanDays` `beforeNow` `afterNow` |
| `tags` | `has`（有这个键）`hasValue`（键值都相等）`missing`（没有这个键） |

- `eq` / `neq` / `in` / `notIn` / `contains` / `startsWith` 之外的值：`number` 必须是有限的数字（`NaN`、`Infinity` 拒绝），`bool` 必须是布尔值，`datetime` 必须是 ISO 8601 字符串，`tags` 的键和值必须是文本。
- `olderThanDays` 的值是**非负整数天**，语义是 `字段 < now() - 天数`，规则不用写死日期（例如「创建超过 29 天」）；`beforeNow` / `afterNow` 分别表示「早于当前时间」和「晚于当前时间」，不带值。
- `exists` / `notExists` / `missing` 不带值；`has` 不带值；`hasValue` 必须带文本值。
- `in` / `notIn` 必须是非空列表。

### 3.3 字段

字段由「主体类型」决定，条件里出现其他字段返回 422 `validation_error`：

| 来源 | 内容 |
|---|---|
| 通用列 | 名称、类型、资源 ID、ARN、账号、账号名称、EIM、EIM 应用、BA、环境、区域、状态、配置更新时间、首次发现、最近变化、疑似删除时间、删除时间 |
| `tags` | 标签，只能用标签操作符和 `key` |
| 虚拟字段 | `inboundRelationshipCount`（被引用次数）、`outboundRelationshipCount`（引用次数），类型 `number`，来自 `t_inv_aws_resource_relation` 的计数子查询 |
| 类型专属列 | 该类型配置里的 `columns`（与资源列表的类型专属列相同） |
| 报表字段 | 该类型配置里的 `reportFields`，见下 |
| findings 字段 | 类型配置打开 `findings: true` 时可用：`findingsCriticalCount`、`findingsHighCount`、`findingsMediumCount`、`findingsLowCount`（`number`）和 `findingsMaxScore`（`number`），来自 `t_inv_aws_finding_summary` |
| 关联字段 | `关系名.字段`，只限该类型 `derivedRelationships` 声明的关联，见下 |

**跨数组字段**：类型配置的 `columns` 不能穿过数组，报表需要「容器的镜像」这类字段，所以新增 `reportFields`（格式见 [resource-types.md](resource-types.md) 1.2）。它的 `path` 可以用 `[]` 穿过数组（如 `ContainerDefinitions[].Image`），这样的字段必须是 `text`，语义是「数组里**任一**元素满足」。通用字段和类型字段同名时以通用字段为准（例如 ECS 任务定义配置里的 `status` 列不会覆盖清单状态）。

**findings 字段**：没有汇总行时值为 NULL，因此 `findingsHighCount eq 0` 只匹配真正检查过且没有高危发现的资源，未检查的资源不会出现。级别由 findings 服务 ③ 判定，报表不按分数重新分级。

**关联字段**：前缀是 `derivedRelationships` 里的关系名（小写），后面可以跟目标类型的通用字段、类型专属列、`reportFields` 和 findings 字段（例如 EC2 的 `image.creationDate`、`image.deprecationTime`、`image.findingsHighCount`，ECS 服务的 `taskdefinition.image`）。语义是「存在一条关联边，且目标资源满足条件」；关联本身只走**一跳**，不再从目标资源继续走关联，但目标字段可以穿过数组（`reportFields` 的 `[]`）。穿过数组时，正向操作符（`contains` / `eq` / `in` / `startsWith`）表示关联目标的数组里**任一**元素满足，`neq` / `notIn` / `notExists` 先在一个目标内部取反（该目标**没有任何**元素满足），再要求存在这样一条关联。作为显示列时，值是所有关联目标上该字段的**去重数组**（穿过数组时把所有元素摊平；一个目标就是单元素，没有目标时为空），因此不能排序。

## 4. 求值

### 4.1 WHERE

`WHERE` 由三部分组成（AND）：

1. 主体类型：`subject_type` 非空时限定 `resource_type = subjectType`。
2. 状态默认：条件树里**没有提到** `status` 时，只算 `active` 和 `missing`（不包含 `deleted`）。树里提到 `status` 就完全按用户的条件来，因此也可以专门做「已删除资源」的报表。
3. 条件树本身，编译成 SQL（`jsonb_path_exists` / `jsonb_path_query_array` 用于跨数组字段，普通比较用于标量）。

findings 字段是 `t_inv_aws_finding_summary` 上按资源身份取值的标量子查询，没有汇总行时得到 NULL；相对时间条件编译成 `now() - make_interval(...)` 或 `now()` 的比较。关联字段编译成关联表 `t_inv_aws_resource_relation` 上的 `EXISTS`：关联边从当前资源出发、指向声明的关系目标类型，并在目标资源行上判断字段条件；一条边都没有时为 false。

**NULL 语义**：每个叶子条件都被包成「不匹配 = false」（`coalesce(predicate, false)`），所以 `not` 严格是「不满足」，能把字段为空的行包括进来。例如 `not(name eq "x")` 会包括没有名称的资源，和 `neq` 一致。数组字段的 `neq` / `notIn` 是「没有任何元素匹配」。findings 没有汇总行时同样按 NULL 处理：`not(findingsHighCount gt 0)` 会**包括**还没检查过的资源。

**范围**：dashboard 的 `s=app:<EIM>` / `s=acct:<账号>` 会额外限定 `eim_id` 或 `account_id`，与资源列表的范围一致；不带 `s` 时是全组织。

**排序**：默认 `-lastChangedAt`；可以按通用列、报表显示的列、关系计数字段排序。数组字段不能排序（返回 400 `invalid_sort`）。分页默认 50、最大 200。

### 4.2 计数与缓存

`GET /reports/overview` 对每个启用的报表执行一次 `count`。计数缓存在 Redis，key 为
`aws-inventory-discovery:<DATABASE_SCHEMA>:reports:count:<报表 id>:<范围>:<定义 sha256>`，TTL 为 `CACHE_TTL_SECONDS`（默认 60 秒）。缓存不可用只是变慢。列表页打开时重新计算；详情页的 `total` 和列表都可能比实时行慢最多 60 秒，`generatedAt` 是本次请求的时间。

已知取舍：概览是每个报表一次计数（报表数量少时可以接受，报表多了再改成批量）；`configuration` 没有通用索引，跨数组和类型字段的条件依靠主体类型的索引收窄后逐行判断。

## 5. API

所有接口在 `/api/v1` 下，JSON 用 camelCase。

```ts
type Condition = {
  all?: Condition[]; any?: Condition[]; not?: Condition;
  field?: string; op?: string; value?: unknown; key?: string
}

type ReportInfo = {
  id: number; name: string; description: string | null; subjectType: string | null;
  enabled: boolean; createdBy: string | null; updatedBy: string | null;
  createdAt: string; updatedAt: string
}

type ReportDetail = ReportInfo & {
  definitionVersion: number; where: Condition; columns: string[]
}

type ReportFieldInfo = {
  key: string; label: string;
  type: "text" | "enum" | "number" | "bool" | "datetime" | "tags";
  array: boolean;              // 这个字段是否跨数组
  group: "common" | "type" | "relation"
}

type ReportRow = ResourceItem & { values: Record<string, unknown> }

type ReportPage = {
  items: ReportRow[]; page: number; pageSize: number; total: number;
  columns: { key: string; label: string; type: string; mono: boolean; array: boolean }[];
  generatedAt: string
}

type ReportOverview = {
  generatedAt: string;
  items: { reportId: number; name: string; subjectType: string | null;
           count: number; error: string | null }[]
}
```

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/reports?enabled=` | 全部报表（含停用的），按名称排序 |
| POST | `/reports` | 新建，`ReportInput`（`name` 1–200 字、`description` ≤ 2000 字、`subjectType`、`where`、`columns`、`enabled`），名称重复返回 409 `report_name_taken`，返回 201 `ReportDetail` |
| GET | `/reports/overview?s=` | 每个启用报表的当前匹配数；定义失效的报表 `count = 0` 且带 `error`，不影响其他报表 |
| GET | `/reports/fields?subjectType=` | 编辑器可用的字段清单，顺序与后端校验一致；不传类型时只有通用字段 |
| GET | `/reports/{id}` | `ReportDetail`；定义失效返回 422 `validation_error` |
| PUT | `/reports/{id}` | 覆盖 `ReportInput`，同名校验同 POST |
| DELETE | `/reports/{id}` | 删除，返回 204 |
| GET | `/reports/{id}/results?page=&pageSize=&sort=&s=` | `ReportPage`；每行是资源本身加上 `values`（本报表各列的值，数组保持数组） |
| GET | `/reports/{id}/export?format=csv\|jsonl&lang=&sort=&s=` | 流式导出：CSV 是「列名 + 值」（数组用 `;` 连接，带 UTF-8 BOM，表头语言由 `lang` 决定，默认跟随 `Accept-Language`）；JSON Lines 每行是 `ReportRow` |

`X-Caller-Id` 记录在 `createdBy` / `updatedBy`。定义不合法时 422 的 `details` 带出错的条件或字段。`fields` 里 `group: "relation"` 的字段既能用于条件，也能作为显示列；作为列时 `array` 为 `true`，`values` 里是去重后的数组，不能排序。

## 6. Dashboard

- 左侧栏第三个功能区是「报表」，与清单、扫描并列。
- **报表列表** `/reports`：名称、说明或主体类型、当前匹配数（来自 `/reports/overview`，跟随全局范围）、最近修改时间；停用的报表带「已停用」；定义失效显示「规则不可用」。打开页面时重新计算。
- **报表详情** `/reports/{id}`：结果表复用资源列表的表格组件（分页、排序、列宽、行状态标记），名称链接到资源详情；身份列（名称、账号、区域）总是显示，其余是本报表选择的列。顶部有导出、编辑、删除；删除需要二次确认。定义失效时仍可删除。
- **编辑器**（对话框）：名称、说明、主体类型、条件树、显示列、启用。条件编辑器支持 all/any 分组、NOT、按字段类型给出操作符；标签条件单独给键。切换主体类型会重置条件（旧类型的字段可能不存在）。`columns` 留空时显示默认列和该类型的列。
- 只读页面，没有「保存结果」；换语言会重新取列名（列名来自 API 的语言）。

## 7. 测试数据

- 后端测试（`backend/tests/api/test_reports_api.py`、`backend/tests/test_report_definition.py`、`backend/tests/test_findings_service.py`）只使用 `tests/fixtures/aws/` 的官方样本经 `tests/support/seed.py` 写入的数据，期望值由样本行计算，不手写 AWS 数据；findings 服务 ③ 还没有真实样本，使用 `tests/fixtures/findings/ami_findings.json`（按本文假设的格式，`SOURCE.md` 注明上线前要换成真实响应）。覆盖：数组字段任一匹配、否定语义含空值、标签、关系计数、默认状态、范围、分页、排序错误、CRUD 与重名、概览、Redis 缓存、CSV/JSONL、失效定义、相对时间、关联字段、findings 汇总与未检查语义、findings 客户端的重试和拒绝规则。
- 类型配置的 `reportFields` 校验测试在 `backend/tests/test_resource_types.py`（路径语法、跨数组必须 text、不能和列重名）。
