# SOURCE: AWS::ElasticLoadBalancingV2::LoadBalancer (one ALB, one NLB)

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::ElasticLoadBalancingV2::LoadBalancer.properties.json
CloudFormation schema: `aws-elasticloadbalancingv2-loadbalancer.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/elasticloadbalancing/latest/APIReference/API_DescribeLoadBalancers.html (the only sample is an application LB)
Relationship table (ALB: security group, subnet, VPC; NLB: none): https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html

Extra files in this folder because two LBs are required: `config_item_nlb.json`, `cloudcontrol_get_nlb.json`
(`config_item.json` / `cloudcontrol_get.json` are the ALB).

## Common to every file in this folder

Org used in all fixtures: main account `111122223333`, second account `444455556666`, region `us-east-1`.
Where an AWS documentation example used another account (`123456789012`, `012345678910`, `555555555555`, `177715257436`)
or region (`us-east-2`, `us-west-2`, `eu-west-1`), only the account/region part was replaced; this is the only kind
of edit applied to otherwise-verbatim example values unless a bullet below says otherwise.
Every file was checked by a script against the Config schema (every `configuration.*` path exists in it) and the
CloudFormation schema (every Properties key exists, no `writeOnlyProperties` key present). Fixtures were built by a
helper script kept outside the repo; nothing was downloaded into the repo.

**`config_item.json`** (one parsed element of `SelectResourceConfig` `Results`)
- Envelope: `Results` is "Array of strings" (https://docs.aws.amazon.com/config/latest/APIReference/API_SelectResourceConfig.html); each string is one JSON object, see the example result strings in
  https://docs.aws.amazon.com/config/latest/developerguide/example-query.html (keys `accountId`, `resourceId`, `awsRegion`, `configuration`, `tags` as an array, `resourceType`).
  The key set is the select list from the task (`arn, resourceId, resourceName, resourceType, awsRegion, accountId,
  configuration, supplementaryConfiguration, tags, relationships, configurationItemCaptureTime, configurationItemStatus`).
  Every one of these keys exists in the Config schema file for the type (`https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/<TypeName>.properties.json`).
- `tags`: array of `{key, value}` (Config schema paths `tags.key`, `tags.value`; example-query shows `"tags":[]`).
- `relationships`: array of `{resourceType, resourceId}` (Config schema paths `relationships.resourceType|resourceId|resourceName`).
  `relationshipName` (documented in https://docs.aws.amazon.com/config/latest/APIReference/API_Relationship.html) is **not** in the Config query
  schema, so it is omitted. Which related types appear follows the relationship table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html.
- `configurationItemStatus`: `OK` is one of the documented values (https://docs.aws.amazon.com/config/latest/APIReference/API_ConfigurationItem.html).
- `configurationItemCaptureTime`: `2026-11-06T09:15:22.401Z`, a made-up instant; the string format follows the
  timestamps printed in https://docs.aws.amazon.com/config/latest/developerguide/example-query.html.
- `supplementaryConfiguration`: `{}` unless the Config schema defines `supplementaryConfiguration.*` for the type.
- `resourceId`, `resourceName` and `arn` values: the Config docs do not define their value rules for each type, so they are
  assumptions that follow `docs/specs/resource-types.md` (identity block). `resourceName` is left out for types whose
  name is only a tag (`tag:Name`).

**`cloudcontrol_get.json`** (`GetResource` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_GetResource.html: `{"ResourceDescription": {"Identifier": str, "Properties": str}, "TypeName": str}`.
  The XML sample on that page names the element `ResourceModel`, but its JSON Response Syntax says `Properties`; the
  fixture follows the Response Syntax (and the user guide list sample https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/resource-operations-list.html which also uses `Properties`).
  `Properties` is a JSON **string** (compact, as in the doc samples).
- Identifier: the `primaryIdentifier` of the type from the CloudFormation schema (`https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip`, file `<type>.json`);
  compound identifiers are joined with `|` (https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/resource-identifier.html).
- Properties: only keys that exist in `properties` of the CloudFormation schema and are not in `writeOnlyProperties`.

**`cloudcontrol_list.json`** (`ListResources` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_ListResources.html: `{"NextToken"?, "ResourceDescriptions": [{"Identifier","Properties"}], "TypeName"}`.
  `NextToken` is omitted (single page).
- https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/resource-operations-list.html says list results may contain "part or all" of the properties (its Kinesis sample returns only `Name`).
  No official source says which of our types return partial properties, so the listed resources carry the same full
  `Properties` as `GetResource`. Test authors should not rely on this for `listReturnsFullProperties`.

## config_item.json (ALB `my-load-balancer`)
- Base: DescribeLoadBalancers sample: `LoadBalancerArn` (`.../loadbalancer/app/my-load-balancer/50dc6c495c0c9188`),
  `Scheme internet-facing`, `LoadBalancerName`, `CanonicalHostedZoneId Z2P70J7EXAMPLE`, `CreatedTime 2016-03-25T21:26:12.920Z`,
  `AvailabilityZones[{SubnetId, ZoneName}]`, `SecurityGroups [sg-5943793c]`, `DNSName`, `State.Code active`, `Type application`.
  Only the region part (`us-west-2` -> `us-east-1`, also in the DNS name) and account were changed. VPC `vpc-3ac0fb5f` and
  subnets `subnet-8360a9e7` / `subnet-b7d581c0` were replaced by this fixture set's `vpc-0ee975135dEXAMPLE`,
  `subnet-0bb1c79de301436ee` (`us-east-1c`) and `subnet-02bf4c428bf44ebce` (`us-east-1b`) for cross-reference.
- Key names are Config schema paths: lowerCamel, including the odd `dNSName` (not `dnsName`) and `canonicalHostedZoneId`.
- `securityGroups`: the Config schema path is `configuration.securityGroups.value`. It was written literally as
  `[{"value": "sg-5943793c"}]`. **Uncertain**: it may be an artefact of how the schema flattens string arrays
  (other types such as ECS `CapacityProviders` or Lambda `subnetIds` have no `.value`). No official sample settles it.
- `state` is `{"code": "active"}` (schema path `configuration.state.code`).
- `ipAddressType: "ipv4"`: a schema-defined key whose value is not in the sample; `ipv4` is an enum value from the ELBv2 API.
- `resourceId`/`arn` = the LB ARN, `resourceName` = LB name (assumption, see Common).
- `relationships`: security group, both subnets, VPC (table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html).

## config_item_nlb.json (NLB `my-nlb`)
- Built from the schema; no official NLB sample exists. `type: "network"` (documented enum), `scheme: "internal"`,
  one availability zone (subnet `subnet-0bb1c79de301436ee`), no `securityGroups` (network LBs have none in the sample set),
  `relationships: []` (the table lists "NA" for Network Load Balancer).
- Invented values: name `my-nlb`, ARN suffix `6d0ecf831eec9f09`, DNS name `my-nlb-6d0ecf831eec9f09.elb.us-east-1.amazonaws.com`
  (the ALB DNS pattern from the sample is `name-id.region.elb.amazonaws.com`; the NLB form with `elb.<region>` is not in an
  allowed source). `canonicalHostedZoneId` and `createdTime` are reused from the ALB sample.

## cloudcontrol_get.json / cloudcontrol_get_nlb.json
- Identifier: `LoadBalancerArn` (primaryIdentifier).
- Properties: `LoadBalancerArn`, `LoadBalancerName`, `Name`, `LoadBalancerFullName` (`app/my-load-balancer/50dc6c495c0c9188`,
  the ARN suffix; the schema documents this property), `Scheme`, `Type`, `IpAddressType`, `Subnets` (IDs), `SecurityGroups`
  (ALB only), `DNSName`, `CanonicalHostedZoneID`, `Tags` (`Owner=TeamA`). Same values as the Config items.
  `LoadBalancerAttributes`, `SubnetMappings`, `MinimumLoadBalancerCapacity` are omitted (no documented example values).
  `EnableCapacityReservationProvisionStabilize` is writeOnly and never included.
- `Tags`: `Owner=TeamA` is a key/value borrowed from the DescribeInstances filter examples; there are no ELB tag samples.

## cloudcontrol_list.json
- Both LBs (ALB first, NLB second).
