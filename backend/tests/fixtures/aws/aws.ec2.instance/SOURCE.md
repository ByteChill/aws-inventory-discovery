# SOURCE: AWS::EC2::Instance

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::EC2::Instance.properties.json
CloudFormation schema: `aws-ec2-instance.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstances.html (Example 2, instance `i-1234567890abcdef0`)

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

## config_item.json
- Base: DescribeInstances Example 2, converted from XML to lowerCamel keys that exist in the Config schema
  (`instanceId`, `imageId`, `state.code|name`, `privateDnsName`, `publicDnsName`, `keyName`, `amiLaunchIndex`, `productCodes`,
  `instanceType`, `launchTime`, `placement.*`, `monitoring.state`, `subnetId`, `vpcId`, `privateIpAddress`, `publicIpAddress`
  (XML `ipAddress`), `sourceDestCheck`, `securityGroups[{groupId,groupName}]`, `architecture`, `rootDeviceType|Name`,
  `blockDeviceMappings`, `virtualizationType`, `clientToken`, `hypervisor`, `networkInterfaces`, `iamInstanceProfile`, `ebsOptimized`,
  `cpuOptions`; `stateTransitionReason ""` from the empty `<reason/>`).
- `state` is `{"code": 16, "name": "running"}` (integer code as in the EC2 sample; note the CloudFormation schema types `State.Code` as a **string**).
- Edits so the instance fits the fixture set: `imageId` `ami-bff32ccc` -> `ami-1234567890EXAMPLE` (the owned AMI fixture);
  `subnetId subnet-56f5f633` -> `subnet-0bb1c79de301436ee`; `vpcId vpc-11112222` -> `vpc-0ee975135dEXAMPLE`; AZ `eu-west-1c` ->
  `us-east-1c`; private IP `192.168.1.88` -> `10.0.2.88` (inside the subnet's `10.0.2.0/24`); DNS names follow the sample's
  pattern `ip-<ip>.<region>.compute.internal` / `ec2-<ip>.<region>.compute.amazonaws.com` with `us-east-1` (the real
  us-east-1 internal domain differs; the sample pattern was kept to stay traceable). Public IP `54.194.252.215`,
  `eni-551ba033`, `vol-1234567890abcdef0`, `sg-e4076980`/`SecurityGroup1`, `my_keypair`, `t2.micro`, tag `Name=Server_1`,
  IPv6 address and the IAM profile (`AdminRole`, account part changed) are verbatim.
- `arn`: from the spec template `arn:{partition}:ec2:{region}:{account}:instance/{id}`.
- **No `userData`**: the Config schema has no such path, so none is included.
- `relationships`: network interface, security group, EBS volume, subnet, VPC (table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html).

## cloudcontrol_get.json
- Identifier: `InstanceId`. Properties (all exist in the CloudFormation schema): `InstanceId`, `ImageId`, `InstanceType`, `KeyName`,
  `SubnetId`, `VpcId`, `AvailabilityZone`, `PrivateIp`, `PrivateIpAddress`, `PrivateDnsName`, `PublicIp`, `PublicDnsName`,
  `State {Code "16", Name "running"}` (strings per schema), `Tenancy`, `Monitoring false`, `SecurityGroupIds`, `SourceDestCheck`,
  `EbsOptimized`, `IamInstanceProfile "AdminRole"` (string in the schema), `Tags`. Values come from the same sample.
- **`UserData` is present on purpose**: in the CloudFormation schema `UserData` is a normal property (not in `writeOnlyProperties`),
  so Cloud Control can return it. The value `ZWNobyBkdW1teQ==` is base64 of `echo dummy` (no real data); it is for redaction tests.
- Omitted: writeOnly keys (`AdditionalInfo`, `LaunchTemplate`, `Ipv6AddressCount`, `Ipv6Addresses`, `LicenseSpecification`,
  `PropagateTagsToVolumeOnCreation`, `BlockDeviceMappings/*/NoDevice|VirtualName`), `BlockDeviceMappings`, `NetworkInterfaces`
  (no documented property-level sample).

## cloudcontrol_list.json
- The same instance only.
