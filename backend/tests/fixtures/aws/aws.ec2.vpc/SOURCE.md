# SOURCE: AWS::EC2::VPC

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::EC2::VPC.properties.json
CloudFormation schema: `aws-ec2-vpc.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeVpcs.html (Example 1)
Config supports the type: listed in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html.

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
- `configuration`: every key is a Config schema path (`configuration.vpcId`, `ownerId`, `state.value`, `cidrBlock`,
  `cidrBlockAssociationSet.{associationId,cidrBlock,cidrBlockState.state}`, `dhcpOptionsId`, `instanceTenancy`, `isDefault`).
  Values are copied from DescribeVpcs Example 1, item `vpc-0ee975135dEXAMPLE` (owner `111122223333`, state `available`,
  `10.0.0.0/16`, association `vpc-cidr-assoc-067c3a0a1fEXAMPLE` / `associated`, `dopt-f30d649a`, `default`, `false`).
  XML names were converted to the lowerCamel SDK shape. `state` is written as `{"value": "available"}` because the
  Config schema path is `configuration.state.value` and https://docs.aws.amazon.com/config/latest/developerguide/example-query.html prints an enum state as `{"state":{"value":"available"}}`.
- `tags`: `Name=MyVPC-Main`. The tag shape is from the DescribeVpcs sample (`tagSet`/`key`/`value`); the doc item
  `vpc-0ee975135dEXAMPLE` has no tag, so the value is invented (the doc's tagged item uses `MyVPC`, used in the list fixture).
- `arn`: built from the spec template `arn:{partition}:ec2:{region}:{account}:vpc/{id}`; not taken from an AWS example.
- `relationships`: the table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html says VPC "contains" EC2 instance and Subnet. IDs are the other fixtures' IDs.

## cloudcontrol_get.json
- Properties: `VpcId` (primaryIdentifier), `CidrBlock`, `CidrBlockAssociations` (readOnly, array of association IDs),
  `InstanceTenancy`, `Tags`. Values reuse the Config item (same source as above).
- Not included, no documented example value: `DefaultNetworkAcl`, `DefaultSecurityGroup`, `Ipv6CidrBlocks`,
  `EnableDnsSupport`, `EnableDnsHostnames`. writeOnly keys (`Ipv4IpamPoolId`, `Ipv4NetmaskLength`, `VpcEncryptionControl.*`) are never included.
- The CloudFormation VPC schema has no `State`, `IsDefault`, `OwnerId` or `DhcpOptionsId` property.

## cloudcontrol_list.json
- Item 1: the same VPC. Item 2: `vpc-081ec835f3EXAMPLE` copied from DescribeVpcs Example 1 (`10.0.1.0/24`,
  `vpc-cidr-assoc-043f572c17EXAMPLE`, tag `Name=MyVPC`), converted to the CloudFormation property names.
