# SOURCE: AWS::Route53::HostedZone (global)

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::Route53::HostedZone.properties.json
CloudFormation schema: `aws-route53-hostedzone.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API samples: https://docs.aws.amazon.com/Route53/latest/APIReference/API_GetHostedZone.html, https://docs.aws.amazon.com/Route53/latest/APIReference/API_ListHostedZones.html
Identifier meaning (`Ref` returns the hosted zone ID such as `Z23ABC4XYZL05B`, i.e. without `/hostedzone/`): https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-route53-hostedzone.html

Route 53 is a global service: the item is filed under the region `global` and is read from us-east-1 (scanner.md 6.1 step 5). The Config schema and
the Config docs do not say in which region the recorder holds global resources or which `awsRegion` value they carry; `global` and us-east-1 follow the design.

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
- Identifier: the `primaryIdentifier` of the type in the CloudFormation schema (`aws-route53-hostedzone.json` in
  https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip).
- Properties: only keys that exist in `properties` of that schema and are not in `writeOnlyProperties`.

**`cloudcontrol_list.json`** (`ListResources` response)
- Envelope: Response Syntax in https://docs.aws.amazon.com/cloudcontrolapi/latest/APIReference/API_ListResources.html (`NextToken` omitted, single page).
- No official source says which types return partial properties from `ListResources`, so the items carry the same full
  `Properties` as `GetResource` (see VERIFICATION.md note 11).


**`config_item.json`** (one parsed element of `SelectResourceConfig` `Results`)
- Envelope: `Results` is "Array of strings" (https://docs.aws.amazon.com/config/latest/APIReference/API_SelectResourceConfig.html), each
  string one JSON object with the select list of scanner.md 6.2 (keys exist in the Config schema file
  https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::Route53::HostedZone.properties.json); example strings:
  https://docs.aws.amazon.com/config/latest/developerguide/example-query.html.
- `tags` is an array of `{key, value}`; `relationships` an array of `{resourceType, resourceId}` (`relationshipName` is not in the query schema, omitted);
  which related types appear follows the table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html.
- `configurationItemStatus: OK` is a documented value (https://docs.aws.amazon.com/config/latest/APIReference/API_ConfigurationItem.html);
  `configurationItemCaptureTime` `2026-11-06T09:15:22.401Z` is a made-up instant in the documented format.
- `supplementaryConfiguration` is `{}` (the Config schema defines no `supplementaryConfiguration.*` for the type).
- `resourceId`, `resourceName` and `arn` values: the Config docs do not define their value rules per type; they are assumptions that follow
  `docs/specs/resource-types.md` (identity block).

## config_item.json (`Z1PA6795UKMFR9`, public zone `example.com.`)
- Base: GetHostedZone "Public Hosted Zone, Default Delegation Set" example: `Id` (the API's `/hostedzone/` prefix removed: CloudFormation and Cloud Control use the bare ID), `Name example.com.`,
  the four `NameServers`. `awsRegion` is `global`.
- Config keys are PascalCase (`Id`, `Name`, `NameServers`, `VPCs.VPCId|VPCRegion`, `QueryLoggingConfig.*`); the Config schema has **no** private-zone flag, no comment and no record count,
  so none of those are in the item. `VPCs` is absent for a public zone.
- `arn`: template `arn:aws:route53:::hostedzone/<id>` (no account, no region; assumption). `resourceId` = the bare zone ID, `resourceName` = zone name (assumptions).
- `tags`: `[]`; `relationships`: `[]` (the Config table says "NA" for Route 53 hosted zones).

## cloudcontrol_get.json
- Identifier `Id` (primaryIdentifier, readOnly). Properties: `Id`, `Name`, `NameServers` (readOnly), `HostedZoneConfig.Comment` (`This is my first hosted zone.`, from the sample).
  Tags are `HostedZoneTags` in this schema (not `Tags`); the sample has none, so there are none here.

## cloudcontrol_list.json
- Item 1: the same public zone. Item 2: a private zone: ID `Z111111QQQQQQQ` and name/comment from the ListHostedZones / GetHostedZone "Private Hosted Zone" examples;
  its VPC `vpc-1a2b3c4d` / `us-east-2` is replaced by this fixture set's `vpc-0ee975135dEXAMPLE` / `us-east-1`. A private zone has no `NameServers` in the GetHostedZone sample, so the property is omitted.
