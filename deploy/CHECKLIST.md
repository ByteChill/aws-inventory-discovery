# 部署清单

给平台团队用：部署 aws-inventory-discovery 需要创建或确认的资源。设计依据见 [docs/DESIGN.md](../docs/DESIGN.md) 第 12、13 节。

所有资源都打标签 `Project=aws-inventory-discovery`。以下 `<…>` 是需要按实际环境替换的值。

## 1. 已有资源（确认即可）

- [ ] Aurora PostgreSQL：本服务专用的 schema `<schema>`，以及一个只对这个 schema 有权限的数据库用户
- [ ] 账号清单服务 ①、凭证服务 ② 的地址，能从 ap-east-1 的私有子网访问
- [ ] ap-east-1 VPC 与 us-east-1、eu-west-1、eu-west-2 VPC 之间的 peering 已建立
- [ ] 目标账号的 ADFS-readonly 角色附加了 AWS 托管策略 ReadOnlyAccess
- [ ] 平台已有的内网 ALB：监听器（443 或 80）和它的安全组 `<alb-sg>`；路径 `/aws-inventory` 没有被别的规则占用

缓存不再共用已有实例，按第 2 节新建。

## 2. ElastiCache for Valkey（新建）

- [ ] 引擎 Valkey 8.x / 9.0（按 ap-east-1 支持选最新稳定版）；cluster mode **disabled**；1 主 1 副本；生产 `cache.t4g.small`，非生产 `cache.t4g.micro`
- [ ] Multi-AZ + 自动故障转移；与 ECS 服务在同一个 ap-east-1 VPC 的私有子网（跨至少两个 AZ）；不暴露公网
- [ ] 自定义端口 **16379**；安全组 `aws-inventory-discovery-redis` 入站放行 16379，出站不需要规则
- [ ] 传输中 TLS + 静态加密（创建时开启）
- [ ] AUTH token（至少 32 位随机字符）；`aws-inventory-discovery/redis` 的 `REDIS_URL` 写成 `rediss://:<token>@<primary-endpoint>:16379/0`；token 中的特殊字符做 URL 编码
- [ ] 参数组 `aws-inventory-discovery-redis`：`maxmemory-policy=allkeys-lru`、`timeout=0`、`tcp-keepalive=300`
- [ ] 关闭备份（snapshot retention 0）；开启自动小版本升级，维护窗口放在低峰
- [ ] 监控和告警：Evictions、内存使用率、CPU、连接数、复制延迟、命中率
- [ ] 标签 `Project=aws-inventory-discovery`

## 3. 镜像和密钥

- [ ] ECR 仓库 `aws-inventory-discovery`（ap-east-1）
- [ ] Secrets Manager `aws-inventory-discovery/db`：**JSON 对象**，不是单独的密码。键：`DATABASE_URL`（主库连接串，api、worker、migrate 都用）和 `DATABASE_READ_URL`（只读副本的连接串，api 用；没有只读副本时可以和 `DATABASE_URL` 相同，或者不放这个键并去掉 `api.json` 里对应的 secret）
- [ ] Secrets Manager `aws-inventory-discovery/redis`：**JSON 对象**，键 `REDIS_URL`（含 auth token 的连接串，api 和 worker 用）

任务定义里用 `arn:aws:secretsmanager:ap-east-1:<hub-account-id>:secret:aws-inventory-discovery/db:DATABASE_URL::` 这种带 JSON 键的 `valueFrom` 引用（见 `deploy/ecs/*.json`），由 ECS 注入成同名的环境变量。

## 4. 日志组

| 日志组 | 保留时间 |
|---|---|
| `/aws-inventory-discovery/api` | 30 天 |
| `/aws-inventory-discovery/worker` | 30 天（至少 14 天） |
| `/aws-inventory-discovery/migrate` | 30 天 |

## 5. IAM 角色

### 5.1 `aws-inventory-discovery-task-execution`（ECS 任务执行角色）
附加 AWS 托管策略 `AmazonECSTaskExecutionRolePolicy`，再加（ECS 用它读取密钥并把 JSON 里的键注入成环境变量；本服务自己的代码从不读取 secret 的值，也不需要这个权限）：

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

### 5.2 `aws-inventory-discovery-api-task`

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

### 5.3 `aws-inventory-discovery-worker-task`
不需要附加任何策略。worker 访问目标账号用的是凭证服务 ② 返回的凭证。

信任策略：三个角色都只信任 `ecs-tasks.amazonaws.com`。

## 6. 安全组

| 安全组 | 入站 | 出站 |
|---|---|---|
| `aws-inventory-discovery-api` | 8000 来自已有 ALB 的安全组 `<alb-sg>` | Aurora (5432)、Redis (16379)、VPC endpoint (443) |
| `aws-inventory-discovery-worker` | 无 | Aurora (5432)、Redis (16379)、两个内部服务 (443)、VPC endpoint（本地和远端）(443) |
| `aws-inventory-discovery-redis` | 16379 来自 `aws-inventory-discovery-api`、`aws-inventory-discovery-worker` | 无 |
| `aws-inventory-discovery-endpoints`（每个区域一个） | 443 来自 ap-east-1 VPC 网段 | 无 |

已有 ALB 的安全组如果限制出站，要放行到 `aws-inventory-discovery-api` 的 8000。

## 7. VPC endpoint

### 7.1 ap-east-1（hub VPC 的私有子网）

| endpoint | 类型 | 用途 |
|---|---|---|
| `com.amazonaws.ap-east-1.config` | Interface | 扫描 ap-east-1 |
| `com.amazonaws.ap-east-1.cloudcontrolapi` | Interface | 扫描 ap-east-1 |
| `com.amazonaws.ap-east-1.ec2` | Interface | 扫描 ap-east-1 时读取 AMI。注意：查询每个账号开启的区域（`ec2:DescribeRegions`）**不在这里**，而是在 `REACHABLE_REGIONS` 里第一个默认开启的区域（默认配置下是 us-east-1，因为 ap-east-1 是 opt-in 区域），见 6.2 |
| `com.amazonaws.ap-east-1.logs` | Interface | 写日志；API 查询任务日志 |
| `com.amazonaws.ap-east-1.secretsmanager` | Interface | ECS 注入密钥 |
| `com.amazonaws.ap-east-1.ecr.api`、`com.amazonaws.ap-east-1.ecr.dkr` | Interface | 拉取镜像 |
| `com.amazonaws.ap-east-1.s3` | Gateway | 拉取镜像的数据层 |

已有的 endpoint 直接复用；为本项目新建的，`Name` 标签用 `aws-inventory-discovery-<服务>`。

### 7.2 us-east-1、eu-west-1、eu-west-2（各区域 VPC）

每个区域三个 Interface endpoint：`config`、`cloudcontrolapi`、`ec2`，例如 `com.amazonaws.us-east-1.config`。us-east-1 的 `ec2` endpoint 还承担所有账号的 `DescribeRegions` 调用（`REACHABLE_REGIONS` 里没有默认开启的区域时，扫描会明确失败，要求加一个），所以必不可少。

### 7.3 endpoint 策略

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

## 8. 跨区域 DNS（Route 53 私有托管区）

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

## 9. 路由

- [ ] ap-east-1 私有子网的路由表：三个远端 VPC 的网段 → peering 连接
- [ ] 三个远端 VPC 中 endpoint 所在子网的路由表：ap-east-1 VPC 网段 → peering 连接

## 10. ECS

| 资源 | 配置 |
|---|---|
| 集群 | 平台已有的集群；如果新建，命名为 `aws-inventory-discovery` |
| 服务 `aws-inventory-discovery-api` | Fargate，1 vCPU / 2 GB，私有子网，挂到 ALB 目标组 `aws-inventory-discovery-api`（健康检查 `/aws-inventory/api/v1/health`），按 CPU 和内存自动扩缩，`minimumHealthyPercent` 100% |
| 服务 `aws-inventory-discovery-worker` | Fargate，2 vCPU / 4 GB，私有子网，`stopTimeout` 120 秒，按 CPU 和内存自动扩缩，`minimumHealthyPercent` 100%。容器健康检查是 `python -m app.cli healthcheck`（看心跳文件 `WORKER_HEARTBEAT_FILE`，默认 `/tmp/aws-inventory-discovery-worker.heartbeat`，不访问数据库）。**容器里 `/tmp`（或这个变量指向的路径）必须可写**：如果平台把根文件系统设成只读，要给它挂一个可写的卷 |
| 任务定义 `aws-inventory-discovery-migrate` | Fargate，0.5 vCPU / 1 GB，命令 `alembic upgrade head`，每次发布前运行一次。它是一次性任务，不监听端口，所以镜像自带的 HTTP 健康检查对它没有意义，任务定义里的检查改成 `exit 0` |
| 目标组 `aws-inventory-discovery-api` | HTTP 8000，目标类型 ip，健康检查 `/aws-inventory/api/v1/health` |
| 已有 ALB 的监听器规则 | 路径 `/aws-inventory`、`/aws-inventory/*` → 转发到目标组 `aws-inventory-discovery-api`；不配 URL 重写 |

任务定义示例在 [`deploy/ecs/`](ecs/)（`api.json`、`worker.json`、`migrate.json`，`<…>` 要按环境替换），环境变量清单见 [docs/DESIGN.md](../docs/DESIGN.md) 12.3。注意不要设置 `AWS_ENDPOINT_URL`、`AWS_ENDPOINT_URL_CLOUDCONTROL`、`AWS_ENDPOINT_URL_CONFIG_SERVICE`，它们只用于本地环境。

## 11. 部署后检查

- [ ] 运行 `python -m app.cli doctor`（作为一次性 ECS 任务），确认数据库（并且迁移已经到最新）、Redis、两个内部服务，以及 `REACHABLE_REGIONS` 里每个区域的 `config`、`cloudcontrolapi`、`ec2` endpoint 都能连通；任何一项失败，命令退出码是 1
- [ ] worker 服务的任务显示健康（容器健康检查 `python -m app.cli healthcheck` 通过）
- [ ] 打开 `https://<alb-dns>/aws-inventory/`，dashboard 正常显示；直接打开深链接（如 `/aws-inventory/resources`）并刷新，页面也正常
- [ ] 打开 dashboard，确认第一轮扫描开始、扫描页没有大量 `endpoint_unreachable`
