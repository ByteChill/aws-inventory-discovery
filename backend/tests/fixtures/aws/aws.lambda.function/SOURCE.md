# SOURCE: AWS::Lambda::Function

Config schema: https://raw.githubusercontent.com/awslabs/aws-config-resource-schema/master/config/properties/resource-types/AWS::Lambda::Function.properties.json
CloudFormation schema: `aws-lambda-function.json` in https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip
Native API pages (shape only): https://docs.aws.amazon.com/lambda/latest/api/API_GetFunction.html, https://docs.aws.amazon.com/lambda/latest/api/API_GetFunctionConfiguration.html,
https://docs.aws.amazon.com/lambda/latest/api/API_FunctionConfiguration.html. **None of the Lambda pages has an example response**, so everything is built from the schemas.

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
- Function name `MyTestRotationLambda` and ARN form `arn:aws:lambda:<region>:<account>:function:MyTestRotationLambda` are the documented example from the
  Secrets Manager DescribeSecret sample (field `RotationLambdaARN`), so the secret fixture can reference it.
- Keys are Config schema paths (lowerCamel): `functionName`, `functionArn`, `runtime`, `role`, `handler`, `codeSize`, `description`, `timeout`,
  `memorySize`, `lastModified`, `codeSha256`, `version`, `vpcConfig.subnetIds|securityGroupIds`, `tracingConfig.mode`, `revisionId`, `state.value`,
  `lastUpdateStatus`, `packageType`, `architectures`, `ephemeralStorage.size`.
- Values: `runtime python3.12` (listed in the Runtime valid values), `state {"value":"Active"}` and `lastUpdateStatus "Successful"` (valid values on
  FunctionConfiguration; `state` uses the `.value` wrapper because that is the schema path, `lastUpdateStatus` is a plain string per the schema),
  `version $LATEST` (pattern on that page), `packageType Zip`, `architectures ["x86_64"]` (documented default), `memorySize 128`, `ephemeralStorage.size 512`
  (schema minimum), `tracingConfig.mode PassThrough` (enum). `lastModified` follows the documented ISO-8601 pattern `YYYY-MM-DDThh:mm:ss.sTZD`.
  Invented: role ARN name `MyTestRotationLambdaRole`, `handler`, `description`, `timeout 30`, `codeSize 1024`, `revisionId` (UUID with `EXAMPLE` tail),
  `codeSha256` (base64 SHA-256 of the string `dummy`).
- `vpcConfig.subnetIds = [subnet-0bb1c79de301436ee]`, `securityGroupIds = [sg-e4076980]` (other fixtures' IDs). The Config schema has **no `vpcConfig.vpcId`**.
- `supplementaryConfiguration.Concurrency.reservedConcurrentExecutions` = `10.0` (schema path, type float).
- **No `environment`**: the Config schema has no `configuration.environment.*` paths, so no environment variables are put in the Config item.
  (Whether real configuration items carry them is not documented in the allowed sources.)
- `relationships`: security group, subnet (table in https://docs.aws.amazon.com/config/latest/developerguide/resource-config-reference.html; the IAM role relationship is omitted because no role ID is documented).

## cloudcontrol_get.json
- Identifier: `FunctionName` (primaryIdentifier). Properties: `FunctionName`, `Arn` (readOnly), `Runtime`, `Role`, `Handler`, `Description`, `Timeout`, `MemorySize`,
  `PackageType`, `Architectures`, `EphemeralStorage`, `TracingConfig`, `ReservedConcurrentExecutions` (`10`), `VpcConfig {SubnetIds, SecurityGroupIds}`, `Tags`, and
  **`Environment.Variables` with two variables** (`DB_PASSWORD`, `LOG_LEVEL`; dummy values) for redaction tests. The schema defines
  `Environment.Variables` as a free-form object and does not list it as writeOnly.
- Omitted: `Code` (required by the schema, but all its sub-properties are writeOnly), `SnapStart`, `PublishToLatestPublished` and other writeOnly keys.
- The CloudFormation `VpcConfig` has no `VpcId`.

## cloudcontrol_list.json
- The same function only.
