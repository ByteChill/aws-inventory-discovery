# SOURCE: AWS::SNS::Topic

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::SNS::Topic.properties.json
CloudFormation schema: `aws-sns-topic.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/sns/latest/api/API_GetTopicAttributes.html (topic `My-Topic`)

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
- **Config keys are PascalCase and mirror the `GetTopicAttributes` attribute names** (`TopicArn`, `Owner`, `FifoTopic`, `KmsMasterKeyId`, ...).
- Values from the sample: `TopicArn arn:aws:sns:us-east-2:123456789012:My-Topic` (-> `us-east-1`/`111122223333`) and `Owner` = the account.
  The sample also returns a `Policy` attribute; it is not in the Config schema, so omitted.
- `FifoTopic "false"`: a string, as the schema types it (`FifoTopic: string`). The sample does not show this attribute, so the value is schema-built.
- The Config schema has **no subscription data and no topic-name key**; the name only exists in the ARN suffix.
- `tags`: `Owner=TeamA`. `relationships: []`.

## cloudcontrol_get.json
- Identifier: `TopicArn`. Properties: `TopicArn` (readOnly), `TopicName My-Topic`, `FifoTopic false` (boolean in CloudFormation, string in Config),
  `Subscription` with **two entries** for redaction tests: `{Endpoint "user@example.com", Protocol "email"}` and `{Endpoint <MyQueue ARN>, Protocol "sqs"}`
  (shape from the CloudFormation `Subscription` definition; both values are invented; `example.com` is a reserved domain), `Tags`.
- The schema has no writeOnly properties for SNS topics.

## cloudcontrol_list.json
- The same topic only.
