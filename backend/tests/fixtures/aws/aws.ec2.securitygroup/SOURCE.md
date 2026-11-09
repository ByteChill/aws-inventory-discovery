# SOURCE: AWS::EC2::SecurityGroup

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::EC2::SecurityGroup.properties.json
CloudFormation schema: `aws-ec2-securitygroup.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSecurityGroups.html (Examples 3 and 4)
Identifier meaning (`Ref` returns the group ID when `VpcId` is set): https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-securitygroup.html

## Common to every file in this folder

Org used in all fixtures: main account `111122223333`, second account `444455556666`, region `us-east-1`.
Where an AWS documentation example used another account or region, only that part was replaced; this is the only
kind of edit applied to otherwise-verbatim example values unless a bullet below says otherwise.
Every file was checked by a script (kept outside the repo) against the CloudFormation schema (every Properties key exists,
no `writeOnlyProperties` key present) and the Config schema (every `configuration.*` path exists in it). Sources were downloaded into a new empty directory outside the
repo and parsed with `python -I`; nothing downloaded was executed or copied into the repo.

**`cloudcontrol_get.json`** (`GetResource` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_GetResource.html:
  `{"ResourceDescription": {"Identifier": str, "Properties": str}, "TypeName": str}`; `Properties` is a compact JSON **string**
  (same as the user guide sample https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/resource-operations-list.html).
- Identifier: the `primaryIdentifier` of the type in the CloudFormation schema (`aws-ec2-securitygroup.json` in
  https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip).
- Properties: only keys that exist in `properties` of that schema and are not in `writeOnlyProperties`.

**`cloudcontrol_list.json`** (`ListResources` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_ListResources.html (`NextToken` omitted, single page).
- No official source says which types return partial properties from `ListResources`, so the items carry the same full
  `Properties` as `GetResource` (see VERIFICATION.md note 11).


**`config_item.json`** (one parsed element of `SelectResourceConfig` `Results`)
- Envelope: `Results` is "Array of strings" (https://docs.aws.amazon.com/config/latest/APIReference/API_SelectResourceConfig.html), each
  string one JSON object with the select list of scanner.md 6.2 (keys exist in the Config schema file
  https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::EC2::SecurityGroup.properties.json); example strings:
  https://docs.aws.amazon.com/config/latest/developerguide/example-query.html.
- `tags` is an array of `{key, value}`; `relationships` an array of `{resourceType, resourceId}` (`relationshipName` is not in the query schema, omitted);
  which related types appear follows the table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html.
- `configurationItemStatus: OK` is a documented value (https://docs.aws.amazon.com/config/latest/APIReference/API_ConfigurationItem.html);
  `configurationItemCaptureTime` `2026-11-06T09:15:22.401Z` is a made-up instant in the documented format.
- `supplementaryConfiguration` is `{}` (the Config schema defines no `supplementaryConfiguration.*` for the type).
- `resourceId`, `resourceName` and `arn` values: the Config docs do not define their value rules per type; they are assumptions that follow
  `docs/specs/resource-types.md` (identity block).

## config_item.json (`sg-5943793c`, `SSHAccess`)
- Base: DescribeSecurityGroups Example 3 (`SSHAccess`, "Security group for SSH access", one inbound rule tcp/22 from `0.0.0.0/0` and `::/0`,
  outbound rule all protocols to `0.0.0.0/0` and `::/0`). Edits: group ID `sg-9bf6ceff` -> `sg-5943793c` (the security group the load balancer fixture
  already references, for cross-reference), VPC `vpc-31896b55` -> `vpc-0ee975135dEXAMPLE`, owner -> `111122223333`.
- Keys are Config schema paths: the API's `groupDescription` is `description` in Config; `ipPermissions[].ipRanges` is a plain list of CIDR strings
  (schema type `cidr_block`) next to `ipv4Ranges[{cidrIp}]` and `ipv6Ranges[{cidrIpv6}]`; the all-protocol egress rule has no ports. The API sample's
  `groups` (empty) is `userIdGroupPairs` in the schema. Empty lists are written for `prefixListIds` and `userIdGroupPairs` (as in the sample).
- `arn`: template `arn:aws:ec2:<region>:<account>:security-group/<id>` (assumption; no Config doc prints it). `resourceName` = group name (spec `nameFrom`).
- `tags`: `[]` (the sample has no tags). `relationships`: the VPC only; the table says a security group "is associated with" EC2 instance, EC2 network interface and VPC
  (instances and ENIs are not part of this fixture's group).

## cloudcontrol_get.json
- Identifier `Id` (primaryIdentifier; readOnly `Id` and `GroupId` both hold the group ID for a VPC security group).
- Properties: `Id`, `GroupId`, `GroupName`, `GroupDescription`, `VpcId`, `SecurityGroupIngress`, `SecurityGroupEgress` (no tags in the sample).
  The schema's rule shape is one entry per source (`IpProtocol`, `FromPort`, `ToPort`, `CidrIp` | `CidrIpv6` | `SourceSecurityGroupId` ...), so the one API rule with an IPv4 and an
  IPv6 range becomes two entries; this is a mechanical derivation from the Example 3 values, not a documented sample.
  `SourceSecurityGroupName` is writeOnly and never included.

## cloudcontrol_list.json
- Item 1: the same group. Item 2: DescribeSecurityGroups Example 4 (`default`, "default VPC security group", rules: all protocols from the group itself, tcp/3389 from `203.0.113.0/24` with
  description "RDP access from B network", egress all). Edits: group ID `sg-bcc24bcd` -> `sg-e4076980` (the group the instance fixture uses; the self-reference follows),
  VPC `vpc-a33cbfda` -> `vpc-0ee975135dEXAMPLE`, owner/user ID -> `111122223333`. Rules derived as above (`SourceSecurityGroupId` + `SourceSecurityGroupOwnerId` for the self-reference).
