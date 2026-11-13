/**
 * The Inventory menu: groups of services, and each service's resource types.
 * The menu is filtered by the types the backend actually reports, so a new type
 * appears without a frontend change; a type the catalog does not know yet is
 * listed under "Other" using the backend's label.
 */
export type GroupKey =
  | "compute"
  | "serverless"
  | "network"
  | "messaging"
  | "security"
  | "other"

export type NavService = {
  key: string
  /** AWS service label: a product name, not translated. */
  label: string
  group: GroupKey
  types: string[]
}

export type NavGroup = {
  key: GroupKey
  services: NavService[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    key: "compute",
    services: [
      {
        key: "ec2",
        label: "EC2",
        group: "compute",
        types: ["AWS::EC2::Instance", "AWS::EC2::Image"],
      },
      {
        key: "ecs",
        label: "ECS",
        group: "compute",
        types: [
          "AWS::ECS::Cluster",
          "AWS::ECS::Service",
          "AWS::ECS::TaskDefinition",
        ],
      },
    ],
  },
  {
    key: "serverless",
    services: [
      {
        key: "lambda",
        label: "Lambda",
        group: "serverless",
        types: ["AWS::Lambda::Function"],
      },
    ],
  },
  {
    key: "network",
    services: [
      {
        key: "vpc",
        label: "VPC",
        group: "network",
        types: ["AWS::EC2::VPC", "AWS::EC2::Subnet", "AWS::EC2::SecurityGroup"],
      },
      {
        key: "elb",
        label: "ELB",
        group: "network",
        types: [
          "AWS::ElasticLoadBalancingV2::LoadBalancer",
          "AWS::ElasticLoadBalancingV2::TargetGroup",
        ],
      },
      {
        key: "route53",
        label: "Route 53",
        group: "network",
        types: ["AWS::Route53::HostedZone"],
      },
    ],
  },
  {
    key: "messaging",
    services: [
      {
        key: "sqs",
        label: "SQS",
        group: "messaging",
        types: ["AWS::SQS::Queue"],
      },
      {
        key: "sns",
        label: "SNS",
        group: "messaging",
        types: ["AWS::SNS::Topic"],
      },
    ],
  },
  {
    key: "security",
    services: [
      {
        key: "secretsmanager",
        label: "Secrets Manager",
        group: "security",
        types: ["AWS::SecretsManager::Secret"],
      },
    ],
  },
]

export type ResourceTypeInfo = { typeName: string; label: string }

/**
 * Groups and services that have at least one known type. A type the catalog
 * does not know yet goes to its own group so the menu never silently drops it.
 */
export function buildNav(types: ResourceTypeInfo[]): NavGroup[] {
  const known = new Set(types.map((t) => t.typeName))
  const groups: NavGroup[] = NAV_GROUPS.map((group) => ({
    key: group.key,
    services: group.services
      .map((s) => ({ ...s, types: s.types.filter((t) => known.has(t)) }))
      .filter((s) => s.types.length > 0),
  })).filter((g) => g.services.length > 0)

  const catalogued = new Set(
    NAV_GROUPS.flatMap((g) => g.services.flatMap((s) => s.types)),
  )
  const unknown = types.filter((t) => !catalogued.has(t.typeName))
  if (unknown.length > 0) {
    groups.push({
      key: "other",
      services: [
        {
          key: "other",
          label: "Other",
          group: "other",
          types: unknown.map((t) => t.typeName),
        },
      ],
    })
  }
  return groups
}
