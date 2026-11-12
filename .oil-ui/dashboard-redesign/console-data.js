const SCOPES = [
  { kind: "app", id: "10809007", name: "payments", sub: "3 个账号：dev、preprod、prod", n: 207, accts: ["payments-dev", "payments-preprod", "payments-prod"] },
  { kind: "app", id: "10809233", name: "risk", sub: "3 个账号", n: 204, accts: ["risk-dev", "risk-preprod", "risk-prod"] },
  { kind: "app", id: "10809346", name: "treasury", sub: "3 个账号", n: 204, accts: ["treasury-dev", "treasury-preprod", "treasury-prod"] },
  { kind: "app", id: "10809459", name: "markets", sub: "3 个账号", n: 204, accts: ["markets-dev", "markets-preprod", "markets-prod"] },
  { kind: "acct", id: "111122220003", name: "payments-prod", sub: "生产，payments", n: 68, accts: ["payments-prod"] },
  { kind: "acct", id: "111122220001", name: "payments-dev", sub: "开发，payments", n: 71, accts: ["payments-dev"] },
  { kind: "acct", id: "111122220018", name: "onboarding-prod", sub: "生产，onboarding", n: 68, accts: ["onboarding-prod"] },
  { kind: "all", id: "", name: "全部账号", sub: "20 个账号，7 个应用", n: 1362, accts: ["payments-prod", "risk-dev", "markets-preprod", "ledger-prod", "onboarding-prod", "treasury-dev"] },
]
const ENV = (a) => (a.endsWith("-prod") ? "生产" : a.endsWith("-preprod") ? "预发布" : "开发")

// Each type owns its columns. Counts are per account and scale with the scope.
const GROUPS = [
  ["计算", ["ec2", "ami", "lambda"]], ["网络", ["vpc", "subnet", "sg", "r53"]], ["容器", ["ecsc", "ecss", "ecst"]],
  ["负载均衡", ["lb", "tg"]], ["消息", ["sqs", "sns"]], ["安全", ["secret"]],
]
const hex = (seed, n) => { let x = (seed + 1) * 2654435761 % 4294967296, out = ""; for (let i = 0; i < n; i++) { x = (x * 1664525 + 1013904223) % 4294967296; out += "0123456789abcdef"[Math.floor(x / 268435456)] } return out }
const TYPES = {
  ec2: { label: "EC2 实例", per: 3, idCol: "实例 ID", cols: ["状态", "实例类型", "可用区", "私有 IP", "VPC"],
    row: (i) => ({ name: `Server_${i + 1}`, id: `i-0${hex(i + 3, 16)}`, v: [i % 4 === 3 ? ["off", "stopped"] : ["ok", "running"], ["t2.micro", "t3.medium", "m5.large"][i % 3], ["us-east-1c", "eu-west-1a", "eu-west-2b"][i % 3], `10.0.${i % 4}.${17 + i * 9}`, `vpc-${hex(i % 3 + 1, 17)}`] }) },
  ami: { label: "AMI", per: 4, idCol: "镜像 ID", cols: ["状态", "架构", "根设备", "可见性", "创建时间"],
    row: (i) => ({ name: "RHEL-8.0.0_HVM-20190618-x86_64-1-Hourly2-GP2", id: `ami-${hex(i + 5, 17)}`, v: [["ok", "available"], "x86_64", "ebs", i % 2 ? "私有" : "共享给本账号", "2019-06-18"] }) },
  lambda: { label: "Lambda 函数", per: 3, idCol: "函数名", cols: ["运行时", "内存", "超时", "代码大小", "最近修改"],
    row: (i) => ({ name: i ? `my-function-${i + 1}` : "my-function", id: i ? `my-function-${i + 1}` : "my-function", v: [["nodejs20.x", "python3.12"][i % 2], "256 MB", "15 秒", "5,797 字节", "昨天 16:12"] }) },
  vpc: { label: "VPC", per: 6, idCol: "VPC ID", cols: ["状态", "IPv4 CIDR", "默认 VPC", "租期", "DHCP 选项集"],
    row: (i) => ({ name: i % 3 ? `vpc-${hex(i + 1, 17)}` : "main", id: `vpc-${hex(i + 1, 17)}`, v: [["ok", "available"], `10.${i}.0.0/16`, i % 6 ? "否" : "是", "default", `dopt-${hex(i + 2, 8)}`] }) },
  subnet: { label: "子网", per: 6, idCol: "子网 ID", cols: ["状态", "VPC", "IPv4 CIDR", "可用 IP", "可用区"],
    row: (i) => ({ name: `subnet-${hex(i + 2, 17)}`, id: `subnet-${hex(i + 2, 17)}`, v: [["ok", "available"], `vpc-${hex(i % 3 + 1, 17)}`, `10.0.${i}.0/24`, String(251 - i * 3), ["us-east-1c", "eu-west-1a", "eu-west-2b"][i % 3]] }) },
  sg: { label: "安全组", per: 6, idCol: "安全组 ID", cols: ["VPC", "入站规则", "出站规则", "描述"],
    row: (i) => ({ name: i % 2 ? "web-tier" : "default", id: `sg-${hex(i + 4, 8)}`, v: [`vpc-${hex(i % 3 + 1, 17)}`, `${1 + (i % 4)} 条`, "1 条", i % 2 ? "Allow HTTPS from the load balancer" : "default VPC security group"] }) },
  r53: { label: "Route 53 托管区域", per: 2, idCol: "托管区域 ID", cols: ["类型", "记录数", "备注"],
    row: (i) => ({ name: i % 2 ? "internal.example.com" : "example.com", id: `Z${hex(i + 6, 13).toUpperCase()}`, v: [i % 2 ? "私有" : "公有", String(2 + i * 5), ""] }) },
  ecsc: { label: "ECS 集群", per: 6, idCol: "集群名", cols: ["状态", "服务", "运行中任务", "等待中任务", "容器实例"],
    row: (i) => ({ name: i ? `MyCluster-${i + 1}` : "MyCluster", id: i ? `MyCluster-${i + 1}` : "MyCluster", v: [["ok", "ACTIVE"], String(1 + (i % 3)), String(2 + i), "0", String(i % 2)] }) },
  ecss: { label: "ECS 服务", per: 3, idCol: "服务名", cols: ["状态", "集群", "期望 / 运行", "启动类型", "任务定义"],
    row: (i) => ({ name: `sample-webapp-${i + 1}`, id: `sample-webapp-${i + 1}`, v: [["ok", "ACTIVE"], "MyCluster", i % 5 === 4 ? "2 / 1" : "2 / 2", "FARGATE", `webapp:${3 + i}`] }) },
  ecst: { label: "ECS 任务定义", per: 6, idCol: "任务定义", cols: ["状态", "CPU", "内存", "网络模式", "兼容性"],
    row: (i) => ({ name: `webapp:${i + 1}`, id: `webapp:${i + 1}`, v: [["ok", "ACTIVE"], "256", "512", "awsvpc", "FARGATE"] }) },
  lb: { label: "负载均衡器", per: 6, idCol: "DNS 名称", cols: ["状态", "类型", "模式", "VPC", "可用区"],
    row: (i) => ({ name: i ? `my-load-balancer-${i + 1}` : "my-load-balancer", id: `my-load-balancer-${1234567890 + i * 7919}.us-east-1.elb.amazonaws.com`, v: [["ok", "active"], i % 3 ? "application" : "network", i % 2 ? "internal" : "internet-facing", `vpc-${hex(i % 3 + 1, 17)}`, "2 个"] }) },
  tg: { label: "目标组", per: 6, idCol: "目标组", cols: ["协议 : 端口", "目标类型", "负载均衡器", "VPC", "健康检查"],
    row: (i) => ({ name: i ? `my-targets-${i + 1}` : "my-targets", id: `targetgroup/my-targets/${hex(i + 7, 16)}`, v: ["HTTP : 80", ["instance", "ip"][i % 2], "my-load-balancer", `vpc-${hex(i % 3 + 1, 17)}`, "HTTP /"] }) },
  sqs: { label: "SQS 队列", per: 6, idCol: "队列名", cols: ["类型", "可见性超时", "消息保留", "加密", "死信队列"],
    row: (i) => ({ name: i % 3 === 2 ? `MyQueue-${i}.fifo` : i ? `MyQueue-${i}` : "MyQueue", id: i % 3 === 2 ? `MyQueue-${i}.fifo` : i ? `MyQueue-${i}` : "MyQueue", v: [i % 3 === 2 ? "FIFO" : "标准", "30 秒", "4 天", i % 2 ? "SSE-KMS" : "SSE-SQS", i % 4 ? "" : "MyQueue-dlq"] }) },
  sns: { label: "SNS 主题", per: 3, idCol: "主题名", cols: ["类型", "订阅", "加密", "显示名称"],
    row: (i) => ({ name: i ? `MyTopic-${i + 1}` : "MyTopic", id: i ? `MyTopic-${i + 1}` : "MyTopic", v: ["标准", String(1 + (i % 3)), i % 2 ? "已加密" : "未加密", ""] }) },
  secret: { label: "密钥", per: 3, idCol: "密钥名", cols: ["轮换", "上次轮换", "上次访问", "KMS 密钥"],
    row: (i) => ({ name: i ? `MySecret-${i + 1}` : "MySecret", id: `MySecret-${hex(i + 9, 6)}`, v: [i % 2 ? ["ok", "已开启"] : ["off", "未开启"], i % 2 ? "3 月 14 日" : "", "昨天", "aws/secretsmanager"] }) },
}

