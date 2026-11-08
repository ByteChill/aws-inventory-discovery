# SOURCE: AWS::SQS::Queue

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::SQS::Queue.properties.json
CloudFormation schema: `aws-sqs-queue.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API sample: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_GetQueueAttributes.html (queue `MyQueue`)

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
- **Config keys are PascalCase** and follow the `GetQueueAttributes` names: `QueueUrl`, `QueueArn`, `QueueName`, `CreatedTimestamp`,
  `LastModifiedTimestamp`, `VisibilityTimeout`, `MaximumMessageSize`, `MessageRetentionPeriod`, `DelaySeconds`, `ReceiveMessageWaitTimeSeconds`,
  `SqsManagedSseEnabled`. Note `QueueArn`, whereas Cloud Control uses `Arn`.
- Values from the "query protocol" sample: `MaximumMessageSize 262144`, `MessageRetentionPeriod 345600`, `DelaySeconds 0`, `ReceiveMessageWaitTimeSeconds 0`,
  `VisibilityTimeout 40`, `SqsManagedSseEnabled true`, ARN `arn:aws:sqs:us-east-1:555555555555:MyQueue` (account replaced). Schema types are used (integers
  as numbers; `SqsManagedSseEnabled` as the **string** `"true"`, per the schema).
- Queue URL `https://sqs.us-east-1.amazonaws.com/111122223333/MyQueue` (pattern from the sample's `QueueUrl`, whose account part is `177715257436`; the sample's
  trailing slash is dropped). `CreatedTimestamp 1677110910`/`LastModifiedTimestamp` in the sample are Unix seconds; the schema type is `date`, and ISO-8601
  UTC strings are used (same format as the Config example timestamps). Representation is unconfirmed.
- No dead-letter/redrive key: the Config schema has no `RedrivePolicy` path.
- `resourceId` = queue URL, `arn` = queue ARN, `resourceName` = queue name (assumptions). `tags`: `Owner=TeamA`. `relationships: []`.

## cloudcontrol_get.json
- Identifier: `QueueUrl` (primaryIdentifier). Properties: `QueueUrl`, `Arn` (both readOnly), `QueueName`, `VisibilityTimeout`, `MaximumMessageSize`,
  `MessageRetentionPeriod`, `DelaySeconds`, `ReceiveMessageWaitTimeSeconds`, `SqsManagedSseEnabled` (boolean), `RedrivePolicy`
  (`{"deadLetterTargetArn": <DLQ ARN>, "maxReceiveCount": 5}`; the field names are in the CloudFormation schema description, the value `5` is invented;
  the schema allows object or string), `Tags`.

## cloudcontrol_list.json
- Item 2: `MyQueue-dlq`, the dead-letter queue referenced by `RedrivePolicy`, built from the schema with the same numeric values and
  `RedriveAllowPolicy {"redrivePermission":"allowAll"}` (value copied from the GetQueueAttributes sample). The name `MyQueue-dlq` is invented.
