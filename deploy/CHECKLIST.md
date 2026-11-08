# 部署清单

给平台团队用：部署 aws-inventory-discovery 需要创建或确认的资源。设计依据见 [docs/DESIGN.md](../docs/DESIGN.md) 第 12、13 节。

所有资源都打标签 `Project=aws-inventory-discovery`。以下 `<…>` 是需要按实际环境替换的值。

## 1. 已有资源（确认即可）

- [ ] Aurora PostgreSQL：本服务专用的 schema `<schema>`，以及一个只对这个 schema 有权限的数据库用户
- [ ] ElastiCache Redis：连接地址和 auth token
- [ ] 账号清单服务 ①、凭证服务 ② 的地址，能从 ap-east-1 的私有子网访问
- [ ] ap-east-1 VPC 与 us-east-1、eu-west-1、eu-west-2 VPC 之间的 peering 已建立
- [ ] 目标账号的 ADFS-readonly 角色附加了 AWS 托管策略 ReadOnlyAccess

## 2. 镜像和密钥

- [ ] ECR 仓库 `aws-inventory-discovery`（ap-east-1）
- [ ] Secrets Manager `aws-inventory-discovery/db`：数据库密码
- [ ] Secrets Manager `aws-inventory-discovery/redis`：Redis auth token

## 3. 日志组

| 日志组 | 保留时间 |
|---|---|
| `/aws-inventory-discovery/api` | 30 天 |
| `/aws-inventory-discovery/worker` | 30 天（至少 14 天） |
| `/aws-inventory-discovery/migrate` | 30 天 |

## 4. IAM 角色

### 4.1 `aws-inventory-discovery-task-execution`（ECS 任务执行角色）
附加 AWS 托管策略 `AmazonECSTaskExecutionRolePolicy`，再加：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-east-1:<hub-account-id>:secret:aws-inventory-discovery/*"
    }
  ]
}
```

### 4.2 `aws-inventory-discovery-api-task`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "logs:FilterLogEvents",
      "Resource": "arn:aws:logs:ap-east-1:<hub-account-id>:log-group:/aws-inventory-discovery/worker:*"
    }
  ]
}
```

### 4.3 `aws-inventory-discovery-worker-task`
不需要附加任何策略。worker 访问目标账号用的是凭证服务 ② 返回的凭证。

信任策略：三个角色都只信任 `ecs-tasks.amazonaws.com`。

## 5. 安全组

| 安全组 | 入站 | 出站 |
|---|---|---|
| `aws-inventory-discovery-alb` | 443（或 80）来自允许访问的内网网段和 peered VPC | 到 `aws-inventory-discovery-api` 的 8000 |
| `aws-inventory-discovery-api` | 8000 来自 `aws-inventory-discovery-alb` | Aurora、Redis、VPC endpoint 的 443 |
| `aws-inventory-discovery-worker` | 无 | Aurora、Redis、两个内部服务、VPC endpoint（本地和远端）的 443 |
| `aws-inventory-discovery-endpoints`（每个区域一个） | 443 来自 ap-east-1 VPC 网段 | 无 |

## 6. VPC endpoint

### 6.1 ap-east-1（hub VPC 的私有子网）

| endpoint | 类型 | 用途 |
|---|---|---|
| `com.amazonaws.ap-east-1.config` | Interface | 扫描 ap-east-1 |
| `com.amazonaws.ap-east-1.cloudcontrolapi` | Interface | 扫描 ap-east-1 |
| `com.amazonaws.ap-east-1.ec2` | Interface | 查询每个账号开启的区域；读取 AMI |
| `com.amazonaws.ap-east-1.logs` | Interface | 写日志；API 查询任务日志 |
| `com.amazonaws.ap-east-1.secretsmanager` | Interface | ECS 注入密钥 |
| `com.amazonaws.ap-east-1.ecr.api`、`com.amazonaws.ap-east-1.ecr.dkr` | Interface | 拉取镜像 |
| `com.amazonaws.ap-east-1.s3` | Gateway | 拉取镜像的数据层 |

已有的 endpoint 直接复用；为本项目新建的，`Name` 标签用 `aws-inventory-discovery-<服务>`。

### 6.2 us-east-1、eu-west-1、eu-west-2（各区域 VPC）

每个区域三个 Interface endpoint：`config`、`cloudcontrolapi`、`ec2`，例如 `com.amazonaws.us-east-1.config`。

### 6.3 endpoint 策略

调用这些 endpoint 用的是**目标账号**的凭证，所以策略不能只允许 hub 账号。扫描用的 endpoint（`config`、`cloudcontrolapi`、`ec2`）使用：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": [
        "config:Describe*",
        "config:Get*",
        "config:List*",
        "config:Select*",
        "config:BatchGet*",
        "cloudformation:ListResources",
        "cloudformation:GetResource",
        "ec2:DescribeRegions",
        "ec2:DescribeImages"
      ],
      "Resource": "*",
      "Condition": { "StringEquals": { "aws:PrincipalOrgID": "<org-id>" } }
    }
  ]
}
```

注意：Cloud Control 在 AWS 内部还会调用资源所属服务的读取接口，这些调用不经过 endpoint，不受这个策略影响。

## 7. 跨区域 DNS（Route 53 私有托管区）

endpoint 自带的私有 DNS 只在它所在的 VPC 里生效。为了让 ap-east-1 的 worker 访问远端区域，在 ap-east-1 VPC 里建以下 9 个私有托管区，每个托管区只关联 ap-east-1 VPC：

| 托管区 | 根记录（A，别名） |
|---|---|
| `config.us-east-1.amazonaws.com` | → us-east-1 `config` endpoint 的区域 DNS 名称 |
| `cloudcontrolapi.us-east-1.amazonaws.com` | → us-east-1 `cloudcontrolapi` endpoint |
| `ec2.us-east-1.amazonaws.com` | → us-east-1 `ec2` endpoint |
| `config.eu-west-1.amazonaws.com` | → eu-west-1 `config` endpoint |
| `cloudcontrolapi.eu-west-1.amazonaws.com` | → eu-west-1 `cloudcontrolapi` endpoint |
| `ec2.eu-west-1.amazonaws.com` | → eu-west-1 `ec2` endpoint |
| `config.eu-west-2.amazonaws.com` | → eu-west-2 `config` endpoint |
| `cloudcontrolapi.eu-west-2.amazonaws.com` | → eu-west-2 `cloudcontrolapi` endpoint |
| `ec2.eu-west-2.amazonaws.com` | → eu-west-2 `ec2` endpoint |

托管区的域名必须是 AWS 的服务域名，不能加前缀；在注释里写 `aws-inventory-discovery`，并打上项目标签。

## 8. 路由

- [ ] ap-east-1 私有子网的路由表：三个远端 VPC 的网段 → peering 连接
- [ ] 三个远端 VPC 中 endpoint 所在子网的路由表：ap-east-1 VPC 网段 → peering 连接

## 9. ECS

| 资源 | 配置 |
|---|---|
| 集群 | 平台已有的集群；如果新建，命名为 `aws-inventory-discovery` |
| 服务 `aws-inventory-discovery-api` | Fargate，1 vCPU / 2 GB，私有子网，挂到 ALB 目标组 `aws-inventory-discovery-api`（健康检查 `/api/v1/health`），按 CPU 和内存自动扩缩，`minimumHealthyPercent` 100% |
| 服务 `aws-inventory-discovery-worker` | Fargate，2 vCPU / 4 GB，私有子网，`stopTimeout` 120 秒，按 CPU 和内存自动扩缩，`minimumHealthyPercent` 100% |
| 任务定义 `aws-inventory-discovery-migrate` | Fargate，0.5 vCPU / 1 GB，命令 `alembic upgrade head`，每次发布前运行一次 |
| ALB `aws-inventory-discovery-alb` | 内网（internal），私有子网 |

任务定义示例在 [`deploy/ecs/`](ecs/)（M7 提供），环境变量清单见 [docs/DESIGN.md](../docs/DESIGN.md) 12.3。

## 10. 部署后检查

- [ ] 运行 `python -m app.cli doctor`（作为一次性 ECS 任务），确认数据库、Redis、两个内部服务，以及每个区域的 `config`、`cloudcontrolapi`、`ec2` endpoint 都能连通
- [ ] 打开 dashboard，确认第一轮扫描开始、扫描页没有大量 `endpoint_unreachable`
