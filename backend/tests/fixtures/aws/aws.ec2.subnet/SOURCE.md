# SOURCE: AWS::EC2::Subnet

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::EC2::Subnet.properties.json
CloudFormation schema: `aws-ec2-subnet.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSubnets.html (Example 1)

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
- Base item: DescribeSubnets Example 1, `subnet-0bb1c79de301436ee` (VPC `vpc-0ee975135dEXAMPLE`, `10.0.2.0/24`,
  `availableIpAddressCount 251`, `defaultForAz false`, `mapPublicIpOnLaunch false`, `assignIpv6AddressOnCreation false`,
  empty `ipv6CidrBlockAssociationSet`, owner `111122223333`, state `available`). Keys are Config schema paths.
- Region change: the sample is in `us-east-2` (`us-east-2c`, `use2-az3`). Replaced by `us-east-1c` and `use1-az6`.
  The zone-id for `us-east-1c` is a guess that only follows the documented `use1-azN` pattern (zone-id mapping differs per account).
- `subnetArn`: the sample has the form `arn:aws:ec2:<region>:<account>:subnet/<id>` (its ID part is inconsistent in the
  doc, so the ARN was rebuilt with the real ID). The top-level `arn` uses the same string.
- `tags`: `Name=MySubnet` is invented (value only; the sample item has no tag).
- `relationships`: per https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html, Subnet "is contained in" VPC and "contains" EC2 instance / network interface. IDs are the other fixtures' IDs.

## cloudcontrol_get.json
- Properties: `SubnetId` (primaryIdentifier), `VpcId`, `CidrBlock`, `AvailabilityZone`, `AvailabilityZoneId`,
  `MapPublicIpOnLaunch`, `AssignIpv6AddressOnCreation`, `Ipv6CidrBlocks` (`[]`), `Tags`. Same values as the Config item.
- The CloudFormation Subnet schema has no `State`, `AvailableIpAddressCount`, `DefaultForAz`, `OwnerId` or `SubnetArn`
  property. writeOnly keys are never included.

## cloudcontrol_list.json
- Item 2: `subnet-02bf4c428bf44ebce` from the same DescribeSubnets sample (`10.0.0.0/24`, `use2-az2`, owner `111122223333`).
  Edits to make it usable: VPC changed from `vpc-07e8ffd50fEXAMPLE` to `vpc-0ee975135dEXAMPLE` (so an ALB can span both subnets
  in one VPC), AZ `us-east-1b` / `use1-az1` (same region caveat as above), the IPv6 association dropped, tag `Name=MySubnet-b` invented.
