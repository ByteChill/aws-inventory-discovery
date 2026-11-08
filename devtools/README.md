# devtools: local AWS simulation

Local-only tooling. Nothing under `backend/app/` knows about it: the app reaches it purely through
the standard boto3 endpoint variables and its own settings.

| Piece | Serves |
|---|---|
| moto (`motoserver/moto`, port 5000, host 5001) | EC2 (`DescribeRegions`, `DescribeImages`), STS, CloudWatch Logs |
| `fake_aws` (port 4567) | Cloud Control, AWS Config, account registry (1), credential service (2), `/_admin` |
| `seed.py` | Builds the demo dataset from `backend/tests/fixtures/aws/` and loads it into both |

moto's server mode has no HTTP endpoint to preset `SelectResourceConfig` results, so AWS Config is
served by `fake_aws` too.

## boto3 endpoint variables

Taken from botocore's service models (`serviceId` upper-cased, spaces and hyphens to underscores,
prefixed `AWS_ENDPOINT_URL_`):

| Service | `serviceId` | Variable |
|---|---|---|
| Cloud Control | `CloudControl` | `AWS_ENDPOINT_URL_CLOUDCONTROL` |
| AWS Config | `Config Service` | `AWS_ENDPOINT_URL_CONFIG_SERVICE` |
| everything else (EC2, STS, Logs) | | `AWS_ENDPOINT_URL` (moto) |

From the host (compose publishes moto on 5001 because macOS AirPlay holds 5000): `AWS_ENDPOINT_URL=http://localhost:5001 AWS_ENDPOINT_URL_CLOUDCONTROL=http://localhost:4567 AWS_ENDPOINT_URL_CONFIG_SERVICE=http://localhost:4567`.

## Run

```
podman compose up -d moto fake-aws
podman compose run --rm seed          # seed.py --reset; safe to repeat
```

Or without containers: `cd devtools && uv run uvicorn fake_aws.app:app --port 4567` (set
`MOTO_ENDPOINT_URL`, default `http://localhost:5000`) and `uv run python seed.py --reset`.
Tests: `uv run pytest -q` (moto and fake run in-process).

## Wire behaviour

* `POST /` with `X-Amz-Target`: `CloudApiService.<Op>` (AWS JSON 1.0) or `StarlingDoveService.<Op>`
  (AWS JSON 1.1). Errors are `{"__type", "message"}` with the HTTP status botocore expects.
* Cloud Control: `ListResources` (`MaxResults` 1-100, `NextToken`), `GetResource`
  (`ResourceNotFoundException`, 404). `Properties` is a JSON string.
* Config: `SelectResourceConfig` (only `SELECT <fields> WHERE resourceType IN ('..', '..')` or
  `resourceType = '..'`; `Limit` <= 100; deleted items are excluded), `DescribeConfigurationRecorders`,
  `DescribeConfigurationRecorderStatus`, `BatchGetResourceConfig` (returns `ResourceDeleted` /
  `ResourceDeletedNotRecorded` items; never-discovered keys are absent).
* The caller's account comes from the SigV4 access key id (issued by `/credentials`), the region from
  the credential scope. Unknown keys get `UnrecognizedClientException`.
* `GET /registry/accounts`: `[{account_id, account_name, account_type, eim_id, eim_app, ba_id}]`
  (format assumed, defined once in `fake_aws/store.py`).
* `GET /credentials?account_id=`: `{AccessKeyId, SecretAccessKey, SessionToken, Expiration}` from
  moto STS `AssumeRole` on `arn:aws:iam::<account>:role/ADFS-readonly`.

## Admin API (`/_admin`)

* `PUT /_admin/dataset[?reset=true]`: upsert `{registry, recorders, config_items, resources}`
  (see `seed.build_dataset` for the exact shapes); `GET` returns counts.
* `POST /_admin/reset[?forget_credentials=true]`.
* `PUT /_admin/resources`: body `{account_id, region, type_name, identifier?, properties?, config_item?}`.
* `DELETE /_admin/resources?account_id&region&type_name&identifier=&config_resource_id=&config_status=`:
  removes the Cloud Control resource and marks the Config item `ResourceDeleted` (default),
  `ResourceDeletedNotRecorded`, or drops it (`remove`).
* `POST /_admin/errors`: `{error: AccessDenied|Throttling|ResourceNotFound, account_id?, region?,
  type_name?, operation?, count?}`; `DELETE /_admin/errors` clears. Unset fields match anything.
  Operations: `ListResources`, `GetResource`, `SelectResourceConfig`, `BatchGetResourceConfig`,
  `DescribeConfigurationRecorders`, `DescribeConfigurationRecorderStatus`.

## Seed notes

20 accounts x `ap-east-1, us-east-1, eu-west-1, eu-west-2`. Most regions record all supported types
continuously; two have Config off, one has a stopped recorder, one records only VPC, Subnet and
Instance (the rest is reachable through Cloud Control only). Cloud Control always lists everything.
Config items exist only for what the fixtures contain, so a Config region and a Cloud Control region
can differ (for example two VPCs in Cloud Control, one in Config).
AMIs are registered in moto through its EC2 API (moto assigns the image ids). moto keeps AMIs per
account, so launch permissions are visible to the owning account only.
