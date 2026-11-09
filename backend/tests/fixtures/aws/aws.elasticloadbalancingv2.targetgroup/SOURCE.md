# SOURCE: AWS::ElasticLoadBalancingV2::TargetGroup (Cloud Control only, no `config_item.json`)

**Why there is no `config_item.json`:** AWS Config lists the type as supported
(https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html, "Application Load Balancer Target Group"; relationships "NA"),
but the Config resource schema repository, the schema of what `SelectResourceConfig` can return, has **no file** for it
(https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::ElasticLoadBalancingV2::TargetGroup.properties.json returns 404).
No official source shows a queryable Config item, so the spec sets `sources.config = false` and the type is read through Cloud Control only.

CloudFormation schema: `aws-elasticloadbalancingv2-targetgroup.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/elasticloadbalancing/latest/APIReference/API_DescribeTargetGroups.html (field list: https://docs.aws.amazon.com/elasticloadbalancing/latest/APIReference/API_TargetGroup.html)
Identifier meaning (`Ref` returns the target group ARN): https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-elasticloadbalancingv2-targetgroup.html

## Common to every file in this folder

Org used in all fixtures: main account `111122223333`, second account `444455556666`, region `us-east-1`.
Where an AWS documentation example used another account or region, only that part was replaced; this is the only
kind of edit applied to otherwise-verbatim example values unless a bullet below says otherwise.
Every file was checked by a script (kept outside the repo) against the CloudFormation schema (every Properties key exists,
no `writeOnlyProperties` key present). Sources were downloaded into a new empty directory outside the
repo and parsed with `python -I`; nothing downloaded was executed or copied into the repo.

**`cloudcontrol_get.json`** (`GetResource` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_GetResource.html:
  `{"ResourceDescription": {"Identifier": str, "Properties": str}, "TypeName": str}`; `Properties` is a compact JSON **string**
  (same as the user guide sample https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/resource-operations-list.html).
- Identifier: the `primaryIdentifier` of the type in the CloudFormation schema (`aws-elasticloadbalancingv2-targetgroup.json` in
  https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip).
- Properties: only keys that exist in `properties` of that schema and are not in `writeOnlyProperties`.

**`cloudcontrol_list.json`** (`ListResources` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_ListResources.html (`NextToken` omitted, single page).
- No official source says which types return partial properties from `ListResources`, so the items carry the same full
  `Properties` as `GetResource` (see VERIFICATION.md note 11).

## cloudcontrol_get.json (`my-targets`)
- Identifier `TargetGroupArn` (primaryIdentifier).
- Base: DescribeTargetGroups "Describe a target group" sample: `TargetGroupArn` (`.../targetgroup/my-targets/73e2d6bc24d8a067`), `TargetGroupName`, `Protocol HTTP`, `Port 80`,
  `HealthCheckProtocol HTTP`, `HealthCheckPort traffic-port`, `HealthCheckPath /`, `HealthCheckIntervalSeconds 30`, `HealthCheckTimeoutSeconds 5`, `HealthyThresholdCount 5`,
  `UnhealthyThresholdCount 2`, `Matcher.HttpCode 200`, `VpcId`, `LoadBalancerArns`. Edits: region `us-west-2` -> `us-east-1`, account -> `111122223333`,
  VPC `vpc-3ac0fb5f` -> `vpc-0ee975135dEXAMPLE` and the load balancer ARN is the ALB of the load balancer fixture (same ARN suffix as in the sample).
- Schema-only properties (no value in the sample): `Name` and `TargetGroupName` (same value), `TargetGroupFullName` (`targetgroup/my-targets/73e2d6bc24d8a067`, the ARN suffix; documented as the target group's full name),
  `TargetType: instance` and `IpAddressType: ipv4` (documented enum values), `HealthCheckEnabled: true`. `Targets` and `TargetGroupAttributes` are omitted (no documented example values).
- `Tags`: none (no sample).

## cloudcontrol_list.json
- Item 1: the same target group. Item 2: `my-nlb-targets`, built from the schema (no official sample for a network target group): `Protocol TCP`, `Port 80`, health check `TCP`/`traffic-port`,
  attached to the NLB `my-nlb` of the load balancer fixture. Invented values: the name, the ARN suffix `9f1c2d3e4b5a6978`, interval 30 s, timeout 10 s, thresholds 3/3.
