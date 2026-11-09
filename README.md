# aws-inventory-discovery

发现组织内 1000 多个 AWS 账号的资源，获取它们**当前的真实状态**，在 dashboard 上查询，并通过 API 提供给其他服务。

> **当前状态**：MVP 已实现：第一批 12 个资源类型、扫描和写入、API、dashboard、本地环境、部署文件，后端测试和前端 Playwright 测试通过，CI 已配置。还没做：第二批类型（Security Group、Target Group、Route 53 Hosted Zone）；账号清单服务 ①、凭证服务 ② 的真实接口格式和真实账号数据的核对要等上线前确认。进度见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 它做什么

- 每 6 小时扫描一遍所有账号、所有可达区域的资源；也可以按账号、区域、类型随时扫描，或者实时刷新单个资源。
- 资源数据统一使用 AWS Config 的格式，可以按账号、EIM、BA、环境、区域、类型、标签以及各类型的专属字段筛选，并导出为 CSV 或 JSON Lines。
- 定时扫描和按需扫描同时进行时，数据不会出错；没删除的资源绝不会被标为删除。
- 只读：不修改任何 AWS 资源，不读取 secret 的值。

## 技术架构

完整的架构图是可交互的 HTML，在浏览器里打开 [docs/diagrams/architecture.html](docs/diagrams/architecture.html)。下面是简化版：

```mermaid
flowchart LR
  U[Dashboard 用户 / 内部服务] --> ALB[Internal ALB]
  ALB --> API[aws-inventory-discovery-api<br/>FastAPI + Dashboard]
  API --> PG[(Aurora PostgreSQL)]
  API --> R[(Redis 缓存)]
  W[aws-inventory-discovery-worker<br/>扫描 + 定时调度] --> PG
  W --> SVC[账号清单服务 ① / 凭证服务 ②]
  W --> EP[VPC endpoints] --> T[目标账号<br/>AWS Config / Cloud Control]
```

| 组件 | 说明 |
|---|---|
| `aws-inventory-discovery-worker` | 唯一调用目标账号 AWS API 的服务：扫描、写入、确认删除、定时调度、清理 |
| `aws-inventory-discovery-api` | REST API 和 dashboard；只读数据库、创建扫描请求，不调用目标账号 |
| Aurora PostgreSQL | 资产数据、账号、扫描任务（任务队列也在这里） |
| Redis | 只做缓存 |
| 数据来源 | 定时扫描用 AWS Config，Config 覆盖不到的用 Cloud Control；按需扫描和单资源刷新用 Cloud Control；两者都不支持的类型（AMI）用服务自己的 API |

**技术栈**

| 部分 | 选型 |
|---|---|
| 后端 | Python 3.14、FastAPI、SQLModel、Alembic、psycopg 3、boto3、uv、Ruff、ty |
| 前端 | React 19、Vite、TypeScript、TanStack Router / Query / Table、shadcn/ui、Tailwind v4、Biome、Playwright |
| 部署 | 一个镜像，两个 ECS Fargate 服务，ap-east-1 |
| 本地 | podman compose、moto、自带的 Cloud Control 模拟服务 |

## 代码结构

```
aws-inventory-discovery/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 应用入口
│   │   ├── cli.py           # worker、doctor、healthcheck 命令
│   │   ├── core/            # 配置、数据库、日志
│   │   ├── models/          # 数据库表和 API 模型
│   │   ├── api/             # 路由和依赖
│   │   ├── query/           # 筛选、分类计数、导出
│   │   ├── types/           # 资源类型：注册表、映射、脱敏、每个类型的 JSON 配置、特殊处理（TYPE_HOOKS）
│   │   ├── aws/             # 与 AWS 和内部服务通信
│   │   └── worker/          # 任务队列、租约、Discovery（读取）、Writer（写入和删除判断）、调度、清理
│   ├── scripts/             # export_openapi.py：导出 OpenAPI，给前端生成客户端
│   ├── tests/               # 测试和样本数据（样本只来自 AWS 官方资料）
│   └── Dockerfile
├── frontend/                # Dashboard
├── devtools/                # 本地模拟服务（fake_aws）和演示数据（seed.py）
├── deploy/                  # ECS 任务定义示例（ecs/）、部署清单（CHECKLIST.md）
├── docs/                    # 设计、规格、决策、进度、架构图
└── compose.yaml
```

## 本地运行

需要：podman（含 `podman compose`）、uv、Node.js。

```bash
podman compose up -d
```

```bash
podman compose run --rm seed
```

然后打开 http://localhost:8000 ，在「扫描」页选择全部账号，点「扫描」开始一轮扫描（本地关闭了定时扫描：`SWEEP_ENABLED=false`，因为定时扫描把账号错开在约 5.5 小时里；按需扫描立即执行）。

本地环境里：

| 服务 | 作用 | 宿主机端口 |
|---|---|---|
| postgres、redis | 数据库和缓存 | 55432、56379 |
| moto | 模拟 EC2（`DescribeRegions`、`DescribeImages`）、STS、CloudWatch Logs | 5001（macOS 的 AirPlay 占着 5000） |
| fake-aws | 模拟 Cloud Control、AWS Config（moto 的服务器模式预设不了 `SelectResourceConfig` 的结果）、账号清单服务 ①、凭证服务 ② | 4567 |
| migrate | 一次性，`alembic upgrade head` | — |
| api、worker | 本项目；api 同时托管 dashboard | 8000 |
| seed | 一次性，灌入演示数据 | — |

程序通过 boto3 原生的环境变量连接模拟服务：`AWS_ENDPOINT_URL`（EC2、STS、Logs → moto）、`AWS_ENDPOINT_URL_CLOUDCONTROL` 和 `AWS_ENDPOINT_URL_CONFIG_SERVICE`（→ fake-aws），代码里没有只在本地生效的逻辑。演示数据是 20 个账号 × 4 个区域，由 AWS 官方资料整理的样本复制而来。

已知限制：moto 的 AMI 按账号隔离，共享 AMI 在别的账号里看不到；moto 里没有 worker 的日志组，任务详情页的日志显示「日志暂时无法查看」。模拟服务的接口和管理接口见 [devtools/README.md](devtools/README.md)。

## 开发指导

### 后端

```bash
cd backend
uv sync
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check
```

测试需要真实的 Postgres 和 Redis（默认连 `localhost:55432`，可以用 `TEST_DATABASE_URL` 改）：

```bash
podman compose up -d postgres redis
uv run pytest
```

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

模拟服务自己的测试：`cd devtools && uv run pytest -q`。

部署后检查连通性（作为一次性任务运行）和 worker 的容器健康检查：

```bash
python -m app.cli doctor
python -m app.cli healthcheck
```

`doctor` 检查数据库（含迁移版本）、Redis、两个内部服务和每个可达区域的 `config`、`cloudcontrolapi`、`ec2` endpoint，任何一项失败退出码是 1；`healthcheck` 只看 worker 的心跳文件，不访问数据库。

- 写法参考 Frost Ming 的 [友好的 Python：面向对象接口](https://frostming.com/posts/2022/friendly-python-oop/) 和 [封装和复用](https://frostming.com/posts/2024/friendly-python-reuse/)，以及 FastAPI 自带的 skill 和 [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)。
- 测试数据只来自样本文件或 API 返回，不在测试代码里构造数据；只写功能测试。

### 前端

```bash
cd frontend
npm install
npm run dev
```

后端接口有变化时重新生成 API 客户端（不要手改 `src/client/`）：先导出 OpenAPI，再生成：

```bash
cd backend && DATABASE_URL=postgresql+psycopg://inventory:inventory@localhost:55432/inventory DATABASE_SCHEMA=inventory uv run python scripts/export_openapi.py
cd frontend && npm run generate-client
```

其他：`npm run build`（类型检查加构建）、`npm run lint`（Biome）。开发时 Vite 把 `/api` 代理到 `http://localhost:8000`（`API_URL` 可改）。配色用 `?palette=dai-lv|dai-lan|yan-zhi` 切换（黛绿、黛蓝、胭脂），选择记在浏览器本地。

### 浏览器端到端测试

Playwright 测试在 `frontend/e2e/`，针对一套正在运行的环境（FastAPI 在 8000 端口托管构建好的 dashboard 和 `/api`），用中文界面走主要流程：概览、资源筛选和导出链接、资源详情和刷新、发起扫描、账号覆盖表、语言和主题切换。测试前的全局准备会通过 API 发起一次全部账号的扫描并等它成功，所以页面上的数据都是环境自己产生的。

```bash
podman compose up -d --build
podman compose run --rm seed
cd frontend
npm ci
npx playwright install chromium
npm run e2e
```

`E2E_BASE_URL` 可以指向别的地址（默认 `http://localhost:8000`）。dashboard 代码改了以后要重新构建 api 镜像（`podman compose up -d --build api`），测试跑的是镜像里的构建结果。当前本地数据只有 Cloud Control 来源，没有关联资源，所以资源详情的关联资源只检查「没有关联资源」的文案；测试期间不要同时跑会写同一个 Redis 的后端测试，否则概览缓存会被覆盖。

### CI

`.github/workflows/ci.yml`（GitHub Actions，用 docker）：

| 任务 | 内容 |
|---|---|
| `backend` | Python 3.14，postgres:17 和 redis:7，`uv sync`、ruff check、ruff format --check、ty check、pytest |
| `devtools` | ruff 和 pytest |
| `frontend` | Node 26，`npm ci`、`npx tsc -b`、`npx biome check .`、`npm run build` |
| `e2e` | 前三个都通过后：`docker compose up -d --build`、seed、Playwright（chromium）；失败时上传 Playwright 报告 |

### 加一个资源类型

1. 在 `backend/app/types/specs/` 下加一个 JSON 配置文件（格式见 [docs/specs/resource-types.md](docs/specs/resource-types.md)）。
2. 在 `backend/tests/fixtures/aws/<类型>/` 下放 Config 和 Cloud Control 的样本，并在 `SOURCE.md` 里写明出自哪篇 AWS 文档。
3. 只有配置文件表达不了的逻辑，才写成函数，放进这个类型的 `Hooks` 对象，并登记到 `backend/app/types/hooks.py` 的 `TYPE_HOOKS`。
4. 运行测试：新类型会被自动覆盖。

## 部署

- 一个镜像（`backend/Dockerfile`），api 和 worker 用不同的启动命令。
- 每次发布：构建镜像 → 推送到 ECR `aws-inventory-discovery` → 运行一次性任务 `aws-inventory-discovery-migrate` → 滚动更新两个服务。任务定义示例在 [deploy/ecs/](deploy/ecs/)。
- 平台需要创建的资源（endpoint、私有托管区、IAM、安全组等）见 [deploy/CHECKLIST.md](deploy/CHECKLIST.md)。Secrets Manager 里的密钥是 JSON 对象（`DATABASE_URL`、`DATABASE_READ_URL`、`REDIS_URL` 几个键），worker 容器里 `/tmp` 要可写（心跳文件）。
- 环境变量见 [docs/DESIGN.md](docs/DESIGN.md) 12.3。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 完整设计 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 关键决策及原因 |
| [docs/PROGRESS.md](docs/PROGRESS.md) | 里程碑和任务进度 |
| [docs/specs/scanner.md](docs/specs/scanner.md) | 扫描的详细规格 |
| [docs/specs/resource-types.md](docs/specs/resource-types.md) | 资源类型配置格式和 v1 类型清单 |
| [docs/specs/api.md](docs/specs/api.md) | API 规格 |
| [docs/specs/dashboard.md](docs/specs/dashboard.md) | Dashboard 规格 |
| [docs/diagrams/](docs/diagrams/) | 架构图（部署架构、扫描流程、资源状态、数据库） |
| [deploy/CHECKLIST.md](deploy/CHECKLIST.md) | 部署清单 |
| [devtools/README.md](devtools/README.md) | 本地模拟服务的接口和管理接口 |
| [backend/tests/fixtures/aws/VERIFICATION.md](backend/tests/fixtures/aws/VERIFICATION.md) | 资源类型 Identifier 和 Config 支持情况对照官方资料的核对结果 |
| [CLAUDE.md](CLAUDE.md) | 给 AI 编程助手的项目说明（`AGENTS.md` 是它的软链接） |
