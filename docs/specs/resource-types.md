# 规格：资源类型

本文是 [DESIGN.md](../DESIGN.md) 5.1–5.3 节的实现细节：资源类型配置文件的格式、映射规则、v1 的类型清单。

## 1. 配置文件

每个类型一个文件，放在 `backend/app/types/specs/`，文件名用类型名的小写点分形式，例如 `AWS::EC2::VPC` 对应 `aws.ec2.vpc.json`。启动时由 `TypeRegistry.from_directory()` 读取，并用 Pydantic 模型 `ResourceTypeSpec` 校验；任何一个文件不合法，程序直接启动失败。

### 1.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `typeName` | string | 是 | CloudFormation / Config 的类型名，如 `AWS::EC2::VPC` |
| `label` | `{ "zh-CN": string, "en": string }` | 是 | 显示名称 |
| `global` | bool | 否，默认 `false` | 全局资源（区域记为 `global`，在 us-east-1 调用） |
| `identity.identifier` | string 或 string[] | 是 | Cloud Control 的 Identifier 由 Config 格式中的哪个字段组成。复合 Identifier 用数组，按顺序以 `\|` 连接 |
| `identity.resourceIdFrom` | path | 是 | `resourceId` 取自 Config 格式中的哪个字段 |
| `identity.arnFrom` / `identity.arnTemplate` | path / string | 二选一 | ARN 取自哪个字段，或者按模板拼。模板变量：`{partition}`、`{region}`、`{account}`、`{id}` |
| `identity.nameFrom` | path 或 `tag:<Key>` | 否 | `resourceName` 的来源 |
| `sources.config` | bool | 是 | Config 是否支持这个类型 |
| `sources.cloudControl.read` | bool | 是 | Cloud Control 是否支持 `GetResource` |
| `sources.cloudControl.list` | bool | 是 | Cloud Control 是否支持 `ListResources` |
| `sources.cloudControl.listReturnsFullProperties` | bool | 否，默认 `false` | `ListResources` 返回的属性是否完整；不完整时会对每个资源再调用 `GetResource` |
| `sources.native` | bool | 否，默认 `false` | Config 和 Cloud Control 都不支持，由这个类型的 `Hooks` 提供 `list_live` 和 `get_live`（见第 4 节）。启动时校验：为 `true` 时 `TYPE_HOOKS` 里必须有这两个函数 |
| `mapping.rename` | `{ CloudControl 路径: Config 路径 }` | 否 | 默认规则对不上的字段 |
| `mapping.exclude` | CloudControl 路径[] | 否 | 不参与转换的字段（Config 格式里没有，或结构差异太大） |
| `mapping.transform` | `{ Config 路径: 转换名 }` | 否 | 值类型的转换，见 2.3 |
| `mapping.preserveKeysUnder` | CloudControl 路径[] | 否 | 这些路径下的子键是用户数据（如环境变量名），**不改大小写** |
| `redact` | `[{ "path": Config 路径, "mode": "values" \| "drop" \| "summarize", "by": 字段 }]` | 否 | 脱敏，见第 3 节 |
| `volatile` | Config 路径[] | 否 | 会自己变化的字段：存储，但不参与内容哈希 |
| `columns` | 见 1.2 | 否 | 资源列表上的类型专属列 |

路径写法：用点号分隔，数组用 `[]`，例如 `vpcConfig.subnetIds[]`。Config 路径都相对于 `configuration`；以 `$.` 开头时相对于整条记录（例如 `$.tags`）。

### 1.2 列定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 列的键，API 里用 `attr.<key>` 筛选 |
| `label` | `{ "zh-CN", "en" }` | 列名 |
| `type` | `text` \| `enum` \| `number` \| `bool` \| `datetime` | 决定显示方式和自动生成的筛选控件：输入框 / 多选（选项由后端统计）/ 范围 / 是否 / 时间范围 |
| `path` | Config 路径 | 取值路径 |
| `exists` | bool | 为 `true` 时，列的值是「这个路径是否有值」，类型应为 `bool` |
| `count` | bool | 为 `true` 时，列的值是这个数组的长度，类型应为 `number` |
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
    "rename": { "Arn": "functionArn" },
    "preserveKeysUnder": ["Environment.Variables"]
  },
  "redact": [{ "path": "environment.variables", "mode": "values" }],
  "volatile": [],
  "columns": [
    { "key": "runtime", "label": { "zh-CN": "运行时", "en": "Runtime" }, "type": "enum", "path": "runtime" },
    { "key": "memorySize", "label": { "zh-CN": "内存（MB）", "en": "Memory (MB)" }, "type": "number", "path": "memorySize" },
    { "key": "vpcId", "label": { "zh-CN": "VPC", "en": "VPC" }, "type": "text", "path": "vpcConfig.vpcId", "mono": true }
  ]
}
```

示例中的字段名用来说明格式，实际取值以 M1 整理的、来自 AWS 官方资料的样本为准。

## 2. 映射规则（Cloud Control → Config 格式）

### 2.1 外层字段

| Config 字段 | 来源 |
|---|---|
| `accountId`、`awsRegion` | 调用时的账号和区域（全局资源为 `global`） |
| `resourceType` | `typeName` |
| `resourceId` | `identity.resourceIdFrom` |
| `arn` | `identity.arnFrom` 或 `identity.arnTemplate` |
| `resourceName` | `identity.nameFrom` |
| `tags` | Cloud Control 的 `Tags: [{Key, Value}]` 转为 `[{key, value}]`；所有类型通用 |
| `relationships` | Cloud Control 没有，保留库里现有的值 |
| `configurationItemCaptureTime` | 不修改；Cloud Control 的观察时间另存 `live_observed_at` |

### 2.2 `configuration` 的转换
1. **默认规则**：所有键转为 lowerCamelCase，递归处理嵌套对象和数组中的对象。开头连续的大写字母整体转为小写，最后一个大写字母后面紧跟小写字母时，那个大写字母保留给下一个单词：`VpcId → vpcId`、`DNSName → dnsName`、`VPCId → vpcId`、`Arn → arn`。
2. `preserveKeysUnder` 里路径下的子键保持原样。
3. 按 `rename` 改名，按 `exclude` 删除。
4. 按 `transform` 转换值的类型。
5. **叠加**：由 Writer 在锁住现有记录之后执行（见 [scanner.md](scanner.md) 第 7 节），不在转换时做。规则：
   - **映射覆盖到的字段**（转换结果里可能出现的路径，由默认规则、`rename` 和 `transform` 决定）以新数据为准；新数据里没有，就视为被清空，从结果里删掉。
   - **映射覆盖不到的字段**（Config 有、Cloud Control 没有，或者被 `exclude` 的）保留原值。
   - 库里没有这条资源时，直接使用转换结果。

   「哪些路径被映射覆盖」由类型配置算出来，转换函数和叠加用同一份结果，避免两边不一致。

### 2.3 内置转换

| 名称 | 作用 |
|---|---|
| `toString` | 转为字符串 |
| `toInt`、`toFloat` | 转为数字 |
| `toBool` | `"true"` / `"false"` 转为布尔 |
| `toIsoDatetime` | 时间戳或其他时间格式转为 ISO 8601 UTC |

需要新的转换时，在 `types/mapping.py` 的转换字典里显式加一项。

## 3. 脱敏

由 Writer 在叠加和补充数据之后、计算内容哈希之前执行，是唯一的脱敏关卡。对 `configuration` 和 `live_raw` 都执行（`live_raw` 用对应的 CloudControl 路径）。

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

TYPE_HOOKS: dict[str, Hooks] = {
    "AWS::ECS::Service": Hooks(list_live=list_ecs_services),
    "AWS::ECS::TaskDefinition": Hooks(filter=latest_active_revision),
    "AWS::EC2::Image": Hooks(list_live=list_amis, get_live=get_ami),
}
```

**约定**
- 钩子只负责读取和补充数据，不读写数据库，不做脱敏，不判断删除。
- `get_live` 的返回值必须区分三种结果：读到了、**明确不存在**、出错（抛异常）。没权限、被限流、不支持读取都必须抛异常，不能当作不存在。
- `list_live` 只有在所有分页、所有上级资源都成功时才算完整；中途出错就抛异常，这个范围本次不做删除判断。
- 以后出现新的特殊情况，在 `Hooks` 里加一个可选字段，并在编排层给出默认实现。

| 类型 | 钩子 | 作用 |
|---|---|---|
| `AWS::ECS::Service` | `list_live` | 先列出所有 ECS Cluster，再对每个 Cluster 调用 `ListResources(ResourceModel={"Cluster": …})` |
| `AWS::ECS::TaskDefinition` | `filter` | 每个 family 只保留最新的 ACTIVE 版本 |
| `AWS::EC2::Image` | `list_live`、`get_live` | 用 EC2 API 读取 AMI，见 4.1 |

### 4.1 AMI
依据 EC2 API 参考文档 [DescribeImages](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeImages.html)：
- **list**：用 boto3 分页器调用两次 `DescribeImages`，都带 `IncludeDeprecated=True`、`IncludeDisabled=True`：
  1. `Owners=["self"]`：账号自有的 AMI。
  2. `ExecutableUsers=["self"]`：共享给账号的 AMI，包括共享给账号所在组织或 OU 的。
  
  按 `ImageId` 合并去重。不使用 `Owners=["amazon"|"aws-marketplace"]`，也不使用 `ExecutableUsers=["all"]`，所以不会拿到公开的 AMI。
- 每条记录额外加两个字段：`ownership`（`OwnerId` 等于本账号时为 `self`，否则为 `shared`）和 `ownerId`。
- **get**：`DescribeImages(ImageIds=[id], IncludeDeprecated=True, IncludeDisabled=True)`。返回空、错误码 `InvalidAMIID.NotFound`，或 `State` 为 `deregistered`，都视为「不存在」，作为删除确认的第二条证据。文档说明刚注销的 AMI 会短暂地以 `deregistered` 状态出现。
- 共享 AMI 被所有者停止共享后，在这个账号里同样表现为「不存在」；界面上显示为「已删除或不再共享」。

## 5. v1 的资源类型

所有 Identifier、ARN 规则和 Config 支持情况都必须在 M1 对照 CloudFormation 资源类型 schema 和 Config 支持的资源类型列表核对，核对后把「核对」一栏改为「已核对」，并在样本的 `SOURCE.md` 里注明出处。

共 15 个类型，分两批完成。另外，每个账号开启的区域存在 `t_inv_aws_account_region`，不是资源类型，但和第一批一起完成。

**第一批（重点）**

| 类型 | Config | Cloud Control Identifier | ARN | 名称 | 特殊处理 | 核对 |
|---|---|---|---|---|---|---|
| `AWS::EC2::VPC` | 支持 | `VpcId` | 模板 `arn:{partition}:ec2:{region}:{account}:vpc/{id}` | `tag:Name` | — | 待核对 |
| `AWS::EC2::Subnet` | 支持 | `SubnetId` | 模板 `…:subnet/{id}` | `tag:Name` | — | 待核对 |
| `AWS::ElasticLoadBalancingV2::LoadBalancer` | 支持 | `LoadBalancerArn` | 取自 Identifier | `loadBalancerName` | ALB 和 NLB 都是这个类型，用「类型」列区分 | 待核对 |
| `AWS::ECS::Cluster` | 支持 | `ClusterName` | 取自 `Arn` | `clusterName` | — | 待核对 |
| `AWS::ECS::Service` | 支持 | `ServiceArn` + `Cluster`（复合） | 取自 `ServiceArn` | `serviceName` | 钩子 `list_live` | 待核对 |
| `AWS::ECS::TaskDefinition` | 支持 | `TaskDefinitionArn` | 取自 Identifier | `family` | 钩子 `filter`；脱敏容器环境变量 | 待核对 |
| `AWS::EC2::Instance` | 支持 | `InstanceId` | 模板 `…:instance/{id}` | `tag:Name` | 脱敏 `userData` | 待核对 |
| `AWS::EC2::Image`（AMI） | 不支持（待核对） | 不支持（待核对） | 模板 `arn:{partition}:ec2:{region}::image/{id}`（不带账号） | `Name` | 钩子 `list_live`、`get_live`，见 4.1；只包括自有和共享的 | 待核对 |
| `AWS::Lambda::Function` | 支持 | `FunctionName` | 取自 `Arn` | `functionName` | 脱敏环境变量 | 待核对 |
| `AWS::SecretsManager::Secret` | 支持 | `Id`（ARN） | 取自 Identifier | `name` | 不读取 secret 值 | 待核对 |
| `AWS::SNS::Topic` | 支持 | `TopicArn` | 取自 Identifier | `TopicName` 或 ARN 末段 | 订阅只保留数量 | 待核对 |
| `AWS::SQS::Queue` | 支持 | `QueueUrl` | 取自 `Arn` | `QueueName` | — | 待核对 |

**第二批**

| 类型 | Config | Cloud Control Identifier | ARN | 名称 | 特殊处理 | 核对 |
|---|---|---|---|---|---|---|
| `AWS::EC2::SecurityGroup` | 支持 | `Id`（GroupId） | 模板 `…:security-group/{id}` | `groupName` | — | 待核对 |
| `AWS::ElasticLoadBalancingV2::TargetGroup` | 待核对 | `TargetGroupArn` | 取自 Identifier | `targetGroupName` | — | 待核对 |
| `AWS::Route53::HostedZone` | 支持（全局，记录在 us-east-1） | `Id` | 模板 `arn:{partition}:route53:::hostedzone/{id}` | `name` | 全局资源 | 待核对 |

`AWS::EC2::Image` 不是 CloudFormation 的资源类型名，这里沿用同样的命名风格，作为本系统内部的类型名。

### 5.1 初始的类型专属列（建议）

| 类型 | 列 |
|---|---|
| VPC | CIDR、是否默认 VPC、状态 |
| Subnet | VPC、CIDR、可用区、自动分配公网 IP |
| Security Group | VPC、入站规则数、出站规则数 |
| EC2 Instance | 实例类型、运行状态、私有 IP、公网 IP、VPC、AMI |
| AMI | 名称、来源（自有 / 共享）、所有者账号、状态、架构、平台、创建时间、弃用时间、是否公开（只对自有的有意义） |
| ALB / NLB | 类型（应用 / 网络）、访问方式（公网 / 内网）、DNS 名称、VPC |
| Target Group | 目标类型、协议、端口、VPC |
| Route 53 Hosted Zone | 是否私有、记录数 |
| ECS Cluster | 状态、运行中的任务数（会自己变化）、服务数 |
| ECS Service | 集群、启动类型、期望任务数、运行中的任务数（会自己变化） |
| ECS Task Definition | family、版本、CPU、内存、网络模式 |
| Lambda | 运行时、内存、超时、VPC |
| SNS Topic | 是否 FIFO、是否加密 |
| SQS Queue | 是否 FIFO、是否加密、死信队列 |
| Secret | 是否开启轮换、最近轮换时间、最近访问时间（会自己变化） |

具体路径在 M1 按样本填写。
