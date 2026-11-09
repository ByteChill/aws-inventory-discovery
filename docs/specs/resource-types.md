# 规格：资源类型

本文是 [DESIGN.md](../DESIGN.md) 5.1–5.3 节的实现细节：资源类型配置文件的格式、映射规则、v1 的类型清单。

## 1. 配置文件

每个类型一个文件，放在 `backend/app/types/specs/`，文件名**必须**是类型名的小写点分形式，例如 `AWS::EC2::VPC` 对应 `aws.ec2.vpc.json`。启动时（api 和 worker 都一样）由 `TypeRegistry.from_directory()` 读取，并用 Pydantic 模型 `ResourceTypeSpec` 校验；文件不合法、文件名和 `typeName` 对不上、类型重复、目录里没有文件、或 `TYPE_HOOKS` 和配置对不上（见 `sources.native`），程序直接启动失败，错误信息里带文件名。文件里的键用 camelCase，多余的键一律拒绝。

### 1.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `typeName` | string | 是 | CloudFormation / Config 的类型名，如 `AWS::EC2::VPC` |
| `label` | `{ "zh-CN": string, "en": string }` | 是 | 显示名称 |
| `global` | bool | 否，默认 `false` | 全局资源（区域记为 `global`，在 us-east-1 调用） |
| `identity.identifier` | string 或 string[] | 是 | Cloud Control 的 Identifier 由 Config 格式中的哪个字段组成（字段名按这个类型的 `keyCase`，见下）。复合 Identifier 用数组，按顺序以 `\|` 连接 |
| `identity.resourceIdFrom` | path | 是 | `resourceId` 取自 Config 格式中的哪个字段 |
| `identity.arnFrom` / `identity.arnTemplate` | path / string | 二选一，必须且只能有一个 | ARN 取自哪个字段，或者按模板拼。模板变量：`{partition}`、`{region}`、`{account}`、`{id}` |
| `identity.nameFrom` | path、`tag:<Key>` 或 `tail:<path>` | 否 | `resourceName` 的来源。`tag:<Key>` 取这个标签的值；`tail:<path>` 取这个路径上 ARN 或 URL 的最后一段（按 `:` 和 `/` 切分），比如 SNS Topic 的名称取 `TopicArn` 的末段 |
| `sources.config` | bool | 是 | Config 是否支持这个类型，并且 `SelectResourceConfig` 能返回它（有 Config 资源 schema） |
| `sources.cloudControl.read` | bool | 是 | Cloud Control 是否支持 `GetResource` |
| `sources.cloudControl.list` | bool | 是 | Cloud Control 是否支持 `ListResources` |
| `sources.cloudControl.listReturnsFullProperties` | bool | 否，默认 `false` | `ListResources` 返回的属性是否完整；不完整时会对每个资源再调用 `GetResource`。官方资料没有列出哪些类型会返回不完整的属性（M1 核对第 11 条），所以现有的类型都保持默认 `false` |
| `sources.native` | bool | 否，默认 `false` | Config 和 Cloud Control 都不支持，由这个类型的 `Hooks` 提供 `list_live` 和 `get_live`（见第 4 节）。启动时校验：为 `true` 时 `TYPE_HOOKS` 里必须有这两个函数，并且 `config`、`cloudControl.read`、`cloudControl.list` 都必须为 `false`；`TYPE_HOOKS` 里登记了不存在的类型也会启动失败。至少要有一个数据源 |
| `mapping.keyCase` | `camel`（默认）或 `pascal` | 否 | Cloud Control 属性转成 Config 格式时键的大小写。**Config 的键风格因类型而异**（M1 核对）：VPC、Subnet、ELBv2 LoadBalancer、EC2 Instance、Lambda 用 lowerCamelCase，默认规则即可；ECS（Cluster / Service / Task Definition）、Secret、SNS、SQS、Route 53 HostedZone 的 Config 保留 CloudFormation 的 PascalCase 键，要写 `pascal`，转换时键保持原样。AMI（服务 API）用默认的 `camel` |
| `mapping.properties` | CloudControl 路径[] | 否 | 这个映射覆盖的 Cloud Control（或服务 API）属性：顶层属性名，或点号路径。没有 schema 就无法知道 Cloud Control「本来会返回哪些字段」，而叠加时必须靠它清掉被删除的字段（返回里没有就视为被清空），所以由配置文件列出。被 `rename` 改名的属性通过 `rename` 算入，不用重复写；被 `exclude` 的属性不算 |
| `mapping.rename` | `{ CloudControl 路径: Config 路径 }` | 否 | 默认规则对不上的字段。不能改名数组里面的字段 |
| `mapping.exclude` | CloudControl 路径[] | 否 | 不参与转换的字段（Config 格式里没有，或结构差异太大） |
| `mapping.transform` | `{ Config 路径: 转换名 }` | 否 | 值类型的转换，见 2.3 |
| `mapping.preserveKeysUnder` | CloudControl 路径[] | 否 | 这些路径下的子键是用户数据（如环境变量名），**不改大小写** |
| `redact` | `[{ "path": Config 路径, "mode": "values" \| "drop" \| "summarize", "by": 字段 }]` | 否 | 脱敏，见第 3 节。`by` 只用于、也必须用于 `summarize` |
| `volatile` | Config 路径[] | 否 | 会自己变化的字段：存储，但不参与内容哈希 |
| `columns` | 见 1.2 | 否 | 资源列表上的类型专属列，`key` 不能重复 |

路径写法：用点号分隔，数组用 `[]`，例如 `vpcConfig.subnetIds[]`。Config 路径都相对于 `configuration`。

**数组整体替换**：`mapping.properties`、`rename` 的两端涵盖的路径，可以以 `[]` 结尾（整个数组），但不能**穿过**数组继续往下（如 `Foo[].Bar`）。叠加把数组当作一个整体值，无法对数组元素逐个叠加；配置文件里出现这样的路径，启动时直接报错。

### 1.2 列定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 列的键，API 里用 `attr.<key>` 筛选 |
| `label` | `{ "zh-CN", "en" }` | 列名 |
| `type` | `text` \| `enum` \| `number` \| `bool` \| `datetime` | 决定显示方式和自动生成的筛选控件：输入框 / 多选（选项由后端统计）/ 范围 / 是否 / 时间范围 |
| `path` | Config 路径 | 取值路径，**不能穿过数组**（路径里不能有 `[]`，数组本身用 `count` 列） |
| `exists` | bool | 为 `true` 时，列的值是「这个路径是否有值」，类型必须是 `bool` |
| `count` | bool | 为 `true` 时，列的值是这个数组的长度，类型必须是 `number`；`exists` 和 `count` 不能同时为 `true` |
| `mono` | bool | 用等宽字体显示（只用于 ID、ARN、DNS 名称这类内容） |
| `sortable` / `filterable` | bool | 默认 `true` |

通用列（名称、类型、账号、区域、EIM、BA、环境、更新时间、首次发现时间、状态）由后端固定提供，不写在类型配置里。

### 1.3 示例

```json
{
  "typeName": "AWS::Lambda::Function",
  "label": { "zh-CN": "Lambda 函数", "en": "Lambda function" },
  "identity": {
    "identifier": "functionName",
    "resourceIdFrom": "functionName",
    "arnFrom": "functionArn",
    "nameFrom": "functionName"
  },
  "sources": {
    "config": true,
    "cloudControl": { "read": true, "list": true }
  },
  "mapping": {
    "properties": ["FunctionName", "Runtime", "Timeout", "MemorySize", "VpcConfig", "Environment"],
    "rename": { "Arn": "functionArn" },
    "preserveKeysUnder": ["Environment.Variables"]
  },
  "redact": [{ "path": "environment.variables", "mode": "values" }],
  "columns": [
    { "key": "runtime", "label": { "zh-CN": "运行时", "en": "Runtime" }, "type": "enum", "path": "runtime" },
    { "key": "memorySize", "label": { "zh-CN": "内存（MB）", "en": "Memory (MB)" }, "type": "number", "path": "memorySize" },
    { "key": "timeout", "label": { "zh-CN": "超时（秒）", "en": "Timeout (s)" }, "type": "number", "path": "timeout" }
  ]
}
```

示例是 `aws.lambda.function.json` 的节选（`mapping.properties` 只列了几项）；实际取值以 `backend/app/types/specs/` 里的文件为准，这些文件按 `backend/tests/fixtures/aws/` 里来自 AWS 官方资料的样本核对过。Lambda 的 Config 和 Cloud Control 都没有 VPC ID（只有子网和安全组），所以没有 VPC 列。

## 2. 映射规则（Cloud Control → Config 格式）

### 2.1 外层字段

| Config 字段 | 来源 |
|---|---|
| `accountId`、`awsRegion` | 调用时的账号和区域（全局资源为 `global`） |
| `resourceType` | `typeName` |
| `resourceId` | `identity.resourceIdFrom` |
| `arn` | `identity.arnFrom` 或 `identity.arnTemplate` |
| `resourceName` | `identity.nameFrom` |
| `tags` | Cloud Control 的 `Tags: [{Key, Value}]` 转为 `[{key, value}]`；所有类型通用，`Tags` 不进入 `configuration` |
| `relationships` | Cloud Control 没有，保留库里现有的值（新资源为空） |
| `configurationItemCaptureTime` | 不修改；Cloud Control 的观察时间另存 `live_observed_at` |

### 2.2 `configuration` 的转换
1. **默认规则**（`mapping.keyCase = "camel"`）：所有键转为 lowerCamelCase，递归处理嵌套对象和数组中的对象。开头连续的大写字母整体转为小写，最后一个大写字母后面紧跟小写字母时，那个大写字母保留给下一个单词：`VpcId → vpcId`、`DNSName → dnsName`、`VPCId → vpcId`、`Arn → arn`。`keyCase = "pascal"` 的类型键保持 Cloud Control 的原样（Config 本身用 PascalCase 的类型，见第 1.1 节）。Config 自己的例外用 `rename` 处理，比如 ELBv2 的 `DNSName` 在 Config 里是 `dNSName`。
2. `preserveKeysUnder` 里路径下的子键保持原样。
3. 按 `rename` 改名（`rename` 的目标路径直接按 Config 的写法给出，不再转换大小写），按 `exclude` 删除。
4. 按 `transform` 转换值的类型。**Config 和 Cloud Control 的值类型有差异时才用**，比如 SNS 的 `FifoTopic`、SQS 的 `SqsManagedSseEnabled`，Cloud Control 是布尔值，Config 是字符串 `"true"` / `"false"`。
5. **叠加**：由 Writer 在锁住现有记录之后执行（见 [scanner.md](scanner.md) 第 7 节），不在转换时做。规则：
   - **映射覆盖到的字段**（转换结果里可能出现的路径，由 `mapping.properties` 里每个属性的转换后路径、`rename` 的目标和 `transform` 决定）以新数据为准；新数据里没有，就视为被清空，从结果里删掉。数组整体替换，不会逐个元素合并。
   - **映射覆盖不到的字段**（Config 有、Cloud Control 没有，或者被 `exclude` 的）保留原值。
   - 库里没有这条资源时，直接使用转换结果。

   「哪些路径被映射覆盖」由类型配置算出来（`covered_paths`），转换函数和叠加用同一份结果，避免两边不一致。叠加时如果被覆盖的路径落在数组里面，直接报错（配置文件加载时已经拒绝这种路径）。

### 2.3 内置转换

| 名称 | 作用 |
|---|---|
| `toString` | 转为字符串；布尔值转为 `"true"` / `"false"`（不是 Python 的 `True` / `False`） |
| `toInt`、`toFloat` | 转为数字 |
| `toBool` | `"true"` / `"false"` 转为布尔 |
| `toIsoDatetime` | 时间戳或其他时间格式转为 ISO 8601 UTC |

需要新的转换时，在 `types/mapping.py` 的转换字典里显式加一项。

## 3. 脱敏

由 Writer 在叠加和补充数据之后、计算内容哈希之前执行，是唯一的脱敏关卡。对 `configuration` 和 `live_raw` 都执行：规则里写的是 Config 路径，处理 `live_raw`（Cloud Control 的原始属性）时，先把每个键用转换时同一个函数换算成 Config 路径再匹配，所以一条规则对两种数据都有效。`values` 对数组的每个元素也一律替换为 `"***"`。

| mode | 作用 | 例子 |
|---|---|---|
| `values` | 保留对象的键，所有值替换为 `"***"` | Lambda 环境变量：`{"DB_PASSWORD": "***"}` |
| `drop` | 删除这个字段 | EC2 `userData` |
| `summarize` | 把数组替换为按 `by` 字段分组的数量 | SNS 订阅：`{"email": 3, "sqs": 2}` |

## 4. 特殊处理（`types/hooks.py`）

只有配置文件表达不了的逻辑才写在这里。每个类型最多一个 `Hooks` 对象，所有字段都可选，没设置的用默认实现；登记在显式字典 `TYPE_HOOKS` 里，不用继承，也没有自动注册：

```python
@dataclass(frozen=True)
class Hooks:
    list_live: ListLive | None = None   # 默认：Cloud Control ListResources
    get_live: GetLive | None = None     # 默认：Cloud Control GetResource
    filter: Filter | None = None        # 默认：不过滤
    enrich: Enrich | None = None        # 默认：不补充
    # 只用于复合 Identifier：库里还没有这个资源（没有存着的 configuration）时，
    # 只靠 resourceId 拼出 Cloud Control 的 Identifier。默认：需要存着的 configuration
    cloudcontrol_identifier: CloudControlIdentifier | None = None

TYPE_HOOKS: dict[str, Hooks] = {
    "AWS::ECS::Cluster": Hooks(cloudcontrol_identifier=ecs_cluster_identifier),
    "AWS::ECS::Service": Hooks(cloudcontrol_identifier=ecs_service_identifier),
    "AWS::EC2::Image": Hooks(list_live=list_amis, get_live=get_ami),
}
```

函数签名：`list_live(session, *, account_id, region)` 返回按页产出资源属性列表的迭代器；`get_live(session, *, account_id, region, resource_id)` 返回属性字典或 `GONE`；`filter(items)` 看到整个列表（所以逐页读取会被缓冲起来）；`enrich(session, *, account_id, region, item)`；`cloudcontrol_identifier(resource_id)` 推不出来时抛 `MappingError`。

**约定**
- 钩子只负责读取和补充数据，不读写数据库，不做脱敏，不判断删除。
- `get_live` 的返回值必须区分三种结果：读到了、**明确不存在**（`GONE`）、出错（抛异常）。没权限、被限流、不支持读取都必须抛异常，不能当作不存在。
- `list_live` 是生成器，由编排层在范围租约内逐页消费；只有在所有分页、所有上级资源都成功时才算完整，中途出错就抛异常，这个范围本次不做删除判断。
- 以后出现新的特殊情况，在 `Hooks` 里加一个可选字段，并在编排层给出默认实现。

| 类型 | 钩子 | 作用 |
|---|---|---|
| `AWS::EC2::Image` | `list_live`、`get_live` | 用 EC2 API 读取 AMI，见 4.1 |
| `AWS::ECS::Service` | `cloudcontrol_identifier` | 复合 Identifier `<Service ARN>\|<Cluster ARN>`：Cluster 从长格式的 Service ARN（`…:service/<cluster>/<service>`）里取出；不带集群名的旧格式 ARN 推不出来，报错。用于刷新库里还没有的 Service，以及 `confirm_missing` |
| `AWS::ECS::Cluster` | `cloudcontrol_identifier` | `resourceId` 是 Cluster ARN，Cloud Control 的 Identifier 是集群名：取 ARN `cluster/` 后面的名字 |

**没有登记钩子的两个类型**（M1 对照 CloudFormation 资源类型 schema 核对的结果，见 `backend/app/types/hooks.py` 的说明和 `backend/tests/fixtures/aws/VERIFICATION.md`）：
- **ECS Service 不需要 `list_live`**：官方资料没有说 `ListResources` 需要传 `Cluster`，而它的 list handler 的权限里有 `ecs:ListClusters` 和 `ecs:ListServices`，说明 Cloud Control 自己会枚举集群；直接用默认的 `ListResources` 就是完整的。
- **ECS Task Definition 没有 `filter`**：Cloud Control 的数据里既没有 `Status` 也没有 `Revision`，「每个 family 只保留最新的 ACTIVE 版本」算不出来。它的 list handler 调用 `ecs:ListTaskDefinitions`，默认只列 ACTIVE 的，所以**所有 ACTIVE 的版本都会入库**（不只是最新的）。某个版本变成 INACTIVE 后从列表里消失，先被标为「可能已删除」，再用按 ID 读取确认：`DescribeTaskDefinition` 对 INACTIVE 的版本仍然能返回，所以它不会被误删。见 DECISIONS D-24。

### 4.1 AMI
依据 EC2 API 参考文档 [DescribeImages](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeImages.html)：
- **list**：用 boto3 分页器调用两次 `DescribeImages`，都带 `IncludeDeprecated=True`、`IncludeDisabled=True`：
  1. `Owners=["self"]`：账号自有的 AMI。
  2. `ExecutableUsers=["self"]`：共享给账号的 AMI，包括共享给账号所在组织或 OU 的。
  
  按 `ImageId` 合并去重。不使用 `Owners=["amazon"|"aws-marketplace"]`，也不使用 `ExecutableUsers=["all"]`，所以不会拿到公开的 AMI。
- 同一个 AMI 同时出现在两次调用里（自有的同时也满足 `ExecutableUsers=self`）只产出一次，记为自有的。读取是逐页的：每个 `DescribeImages` 分页产出一页。
- 每条记录额外加一个字段 `Ownership`（`OwnerId` 等于本账号时为 `self`，否则为 `shared`），映射后是 `configuration.ownership`；`ownerId` 本来就在 `DescribeImages` 的返回里。
- **get**：`DescribeImages(ImageIds=[id], IncludeDeprecated=True, IncludeDisabled=True)`。返回空、错误码 `InvalidAMIID.NotFound`，或返回的 `State` 全部是 `deregistered`，都视为「不存在」，作为删除确认的第二条证据。文档说明刚注销的 AMI 会短暂地以 `deregistered` 状态出现，之后返回空，最终才是错误「AMI ID cannot be found」；文档里没有写出错误码 `InvalidAMIID.NotFound` 这个字符串，这里按 EC2 的惯例处理。
- 共享 AMI 被所有者停止共享后，在这个账号里同样表现为「不存在」，走同样的删除流程，界面上没有专门的文案（和其他资源一样显示为「可能已删除」或「已删除」）。

## 5. v1 的资源类型

所有 Identifier、ARN 规则和 Config 支持情况都在 M1 对照 CloudFormation 资源类型 schema（`primaryIdentifier`、`handlers`）、`awslabs/aws-config-resource-schema` 和 Config 支持的资源类型列表核对过，逐项结果和差异在 `backend/tests/fixtures/aws/VERIFICATION.md`，每个类型的样本出处在样本目录的 `SOURCE.md`。下表的「核对」一栏：**已核对**表示这一行的 Identifier、ARN 规则和 Config 支持情况与官方资料一致，或已按核对结果改正；官方资料里没有的内容在「备注」里写明。「配置文件」一栏表示 `backend/app/types/specs/` 里有没有这个类型。

共 15 个类型，分两批完成：两批共 15 个类型的配置文件和样本都已完成。另外，每个账号开启的区域存在 `t_inv_aws_account_region`，不是资源类型，随第一批一起完成。

**第一批（重点）**

| 类型 | Config | Cloud Control Identifier | `resourceId` | ARN | 名称 | 键风格 | 特殊处理 | 配置文件 | 核对 |
|---|---|---|---|---|---|---|---|---|---|
| `AWS::EC2::VPC` | 支持 | `VpcId` | `vpcId` | 模板 `arn:{partition}:ec2:{region}:{account}:vpc/{id}` | `tag:Name` | camel | — | 已实现 | 已核对 |
| `AWS::EC2::Subnet` | 支持 | `SubnetId` | `subnetId` | 模板 `…:subnet/{id}`（Config 有 `subnetArn`，CFN 没有，统一用模板） | `tag:Name` | camel | — | 已实现 | 已核对 |
| `AWS::ElasticLoadBalancingV2::LoadBalancer` | 支持 | `LoadBalancerArn` | `loadBalancerArn` | 取自 `loadBalancerArn`（就是 Identifier） | `loadBalancerName` | camel | ALB 和 NLB 都是这个类型，用「类型」列区分；Config 里 DNS 名称的键是 `dNSName`，用 `rename` | 已实现 | 已核对 |
| `AWS::ECS::Cluster` | 支持 | `ClusterName` | `Arn`（Cluster ARN） | 取自 `Arn` | `ClusterName` | pascal | 钩子 `cloudcontrol_identifier` | 已实现 | 已核对 |
| `AWS::ECS::Service` | 支持 | `ServiceArn` + `Cluster`（复合，`\|` 连接） | `ServiceArn` | 取自 `ServiceArn` | `ServiceName` | pascal | 钩子 `cloudcontrol_identifier`；不需要 `list_live`（见第 4 节） | 已实现 | 已核对（`list_live` 的前提不成立，已改正） |
| `AWS::ECS::TaskDefinition` | 支持 | `TaskDefinitionArn` | `TaskDefinitionArn` | 取自 `TaskDefinitionArn` | `Family` | pascal | 没有 `filter`，所有 ACTIVE 版本入库（见第 4 节）；脱敏容器环境变量 | 已实现 | 已核对（`filter` 的前提不成立，已改正） |
| `AWS::EC2::Instance` | 支持 | `InstanceId` | `instanceId` | 模板 `…:instance/{id}` | `tag:Name` | camel | 脱敏 `userData`（Cloud Control 会返回它，不是 writeOnly） | 已实现 | 已核对 |
| `AWS::EC2::Image`（AMI） | 不支持 | 不支持 | `imageId` | 模板 `arn:{partition}:ec2:{region}::image/{id}`（不带账号） | `name` | camel | 钩子 `list_live`、`get_live`，见 4.1；只包括自有和共享的 | 已实现 | 已核对（ARN 模板在允许的官方资料里无法验证） |
| `AWS::Lambda::Function` | 支持 | `FunctionName` | `functionName` | 取自 `functionArn`（Cloud Control 的 `Arn` 改名） | `functionName` | camel | 脱敏环境变量 | 已实现 | 已核对 |
| `AWS::SecretsManager::Secret` | 支持 | `Id`（ARN） | `ARN` | 取自 `ARN`（Cloud Control 的 `Id` 改名为 `ARN`） | `Name` | pascal | 不读取 secret 的值 | 已实现 | 已核对 |
| `AWS::SNS::Topic` | 支持 | `TopicArn` | `TopicArn` | 取自 `TopicArn` | `tail:TopicArn`（ARN 末段） | pascal | 订阅只保留数量（Config 里没有订阅数据） | 已实现 | 已核对 |
| `AWS::SQS::Queue` | 支持 | `QueueUrl` | `QueueUrl` | 取自 `QueueArn`（Cloud Control 的 `Arn` 改名） | `QueueName` | pascal | — | 已实现 | 已核对（Config 的 `resourceId` 是 URL 还是 ARN，官方资料没写，按 URL 取） |

**第二批**

| 类型 | Config | Cloud Control Identifier | `resourceId` | ARN | 名称 | 键风格 | 特殊处理 | 配置文件 | 核对 |
|---|---|---|---|---|---|---|---|---|---|
| `AWS::EC2::SecurityGroup` | 支持 | `Id`（VPC 里的安全组就是 GroupId；配置里用 `groupId`） | `groupId` | 模板 `…:security-group/{id}` | `groupName` | camel | 规则（Config 的 `ipPermissions` 和 CFN 的 `SecurityGroupIngress` 结构不同，无法互相转换）不参与映射，只留在 `live_raw`；`GroupDescription` 改名为 `description` | 已实现 | 已核对 |
| `AWS::ElasticLoadBalancingV2::TargetGroup` | Config 支持列表里有这个类型，但**没有 Config 资源 schema，`SelectResourceConfig` 查不到它**：`sources.config = false`，只按 Cloud Control 读取，没有 Config 样本 | `TargetGroupArn` | `targetGroupArn` | 取自 `targetGroupArn`（就是 Identifier） | `targetGroupName` | camel | `Targets`（随注册变化）和 `TargetGroupAttributes` 不参与映射，只留在 `live_raw` | 已实现 | 已核对（原来的「Config 待核对」已改正） |
| `AWS::Route53::HostedZone` | 支持（全局，Config schema 的键是 PascalCase；Config 报告的 `awsRegion` 和记录所在区域，官方资料没有明说，按设计记为 `global`、在 us-east-1 读取） | `Id`（不带 `/hostedzone/` 前缀的区域 ID） | `Id` | 模板 `arn:{partition}:route53:::hostedzone/{id}` | `Name` | pascal | `global: true`，每个账号只规划一次，区域记为 `global`（scanner.md 6.1）；`HostedZoneConfig`、`QueryLoggingConfig`、`HostedZoneFeatures`、`DelegationSetId` 不参与映射；Cloud Control 的标签属性叫 `HostedZoneTags`，通用的标签转换只读 `Tags`，所以这个类型从 Cloud Control 读到的数据没有标签（待用户确认是否加规则） | 已实现 | 已核对 |

`AWS::EC2::Image` 不是 CloudFormation 的资源类型名，这里沿用同样的命名风格，作为本系统内部的类型名。

### 5.1 类型专属列

下表是 `backend/app/types/specs/*.json` 里实际定义的列（括号里是列的 `key`、类型和取值路径）。原来建议的一部分列没有定义：有的在 Config 和 Cloud Control 里都没有数据来源（M1 核对），有的暂时没做；都列在「未定义」一栏。通用列由后端固定提供，不在这里。

| 类型 | 列 | 未定义 |
|---|---|---|
| VPC | CIDR（`cidrBlock`）、是否默认 VPC（`isDefault`，bool）、状态（`state` ← `state.value`，enum） | — |
| Subnet | VPC（`vpcId`）、CIDR（`cidrBlock`）、可用区（`availabilityZone`，enum）、自动分配公网 IP（`mapPublicIpOnLaunch`，bool） | 状态（只有 Config 有，Cloud Control 没有） |
| EC2 Instance | 实例类型（`instanceType`）、运行状态（`state` ← `state.name`）、私有 IP（`privateIp` ← `privateIpAddress`）、公网 IP（`publicIp` ← `publicIpAddress`）、VPC（`vpcId`）、AMI（`imageId`） | — |
| AMI | 名称（`name`）、来源（`ownership`，自有 / 共享，enum）、所有者账号（`ownerId`）、状态（`state`）、架构（`architecture`）、平台（`platformDetails`）、创建时间（`creationDate`，datetime）、是否公开（`public`，bool，只对自有的有意义） | 弃用时间（数据已存在 `configuration.deprecationTime`，只是没有列） |
| ALB / NLB | 类型（`type`，应用 / 网络）、访问方式（`scheme`，公网 / 内网）、DNS 名称（`dnsName` ← `dNSName`）、VPC（`vpcId`） | — |
| ECS Cluster | 容量提供者数（`capacityProviderCount`，数组长度） | 状态、运行中的任务数、服务数（Config 和 CFN 都没有） |
| ECS Service | 集群（`cluster`）、期望任务数（`desiredCount`）、任务定义（`taskDefinition`） | 启动类型、运行中的任务数（后者 Config 和 CFN 都没有） |
| ECS Task Definition | family（`family`）、状态（`status` ← `Status`，只有 Config 来源有值）、容器数（`containerCount`，数组长度） | 版本、CPU、内存、网络模式（暂未定义；版本号在 ARN 末尾） |
| Lambda | 运行时（`runtime`）、内存（`memorySize`）、超时（`timeout`） | VPC（Config 和 CFN 的 `VpcConfig` 里没有 VPC ID） |
| SNS Topic | 是否 FIFO（`fifo` ← `FifoTopic`，bool） | 是否加密（暂未定义） |
| SQS Queue | 是否加密（`sseEnabled` ← `SqsManagedSseEnabled`，bool）、可见性超时（`visibilityTimeout`）、死信队列（`deadLetterTarget` ← `RedrivePolicy.deadLetterTargetArn`，只有 Cloud Control 来源有值，Config 没有 `RedrivePolicy`） | 是否 FIFO（暂未定义） |
| Secret | 是否开启轮换（`rotationEnabled`，只有 Config 来源有值）、轮换天数（`rotationDays` ← `RotationRules.AutomaticallyAfterDays`） | 最近轮换时间、最近访问时间（Config 和 CFN 都没有） |
| Security Group | VPC（`vpcId`）、描述（`description` ← CFN 的 `GroupDescription`） | 入站 / 出站规则数（Config 和 CFN 的规则结构不同，Cloud Control 来源没有） |
| Target Group | 协议（`protocol`，enum）、端口（`port`，number）、目标类型（`targetType`，enum）、VPC（`vpcId`）、负载均衡器数（`loadBalancerCount` ← `loadBalancerArns` 数组长度） | 健康状态、已注册目标数（Cloud Control 的 `Targets` 随注册变化，不入库；官方资料也没有示例值） |
| Route 53 Hosted Zone | 关联 VPC 数（`vpcCount` ← `VPCs` 数组长度，0 表示公有区域） | 是否私有、记录数（Config 和 CFN 里都没有；是否私有可由「关联 VPC 数」推出）、备注（只有 Cloud Control 有） |

某一列的值在当前这条记录的数据来源里没有时，列显示为空（`null`）。一个列的 `key` 和通用列重名时（ECS Task Definition 的 `status`），查询参数里类型专属列一律加 `attr.` 前缀，见 [api.md](api.md)。
