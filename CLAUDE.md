# CLAUDE.md

Guidance for AI coding agents working in this repository. `AGENTS.md` is a symlink to this file. Human-facing docs are in `README.md` and `docs/`.

## What this is

aws-inventory-discovery discovers the **current real state** of AWS resources across 1,000+ accounts in one AWS Organization, stores it in Aurora PostgreSQL, and serves it through a FastAPI REST API and a React dashboard. Two ECS services share one image: `aws-inventory-discovery-worker` (the only component that calls target-account AWS APIs) and `aws-inventory-discovery-api` (API + dashboard; reads the DB, creates scan requests, never calls target accounts).

Status: design complete, code not started. Check `docs/PROGRESS.md` before starting work.

## Source of truth

- `docs/DESIGN.md` is authoritative. `docs/specs/*.md` add implementation detail; if a spec and the design disagree, the design wins: fix the spec.
- `docs/DECISIONS.md` records why things are the way they are. Don't reopen a decision there without the user.
- **Never change `docs/DESIGN.md`, `docs/DECISIONS.md` or a spec on your own.** Propose the change in chat, get the user's approval, then edit. The same applies to README and this file.
- When you finish a task, tick it in `docs/PROGRESS.md` and update its "最后更新" date.

## Invariants (never violate)

1. **Read-only.** Only call `Describe*`, `List*`, `Get*`, `Select*`, `BatchGet*` style APIs. Never call `secretsmanager:GetSecretValue` or anything that reads secret values.
2. **Redact before storing.** Apply the type's `redact` rules to both `configuration` and `live_raw` before any DB write. No plaintext secrets, env-var values, SNS endpoints or EC2 `userData` anywhere in the DB or logs. Never log credentials.
3. **Older observations never overwrite newer ones** (DESIGN 7.2 R4): every resource write is conditional on `observed_at`. Config data uses `configurationItemCaptureTime`; Cloud Control data uses the time before the call.
4. **Deletion needs two independent pieces of evidence** (R5): absent from a *fully successful* listing **and** a by-ID read confirms it is gone: Cloud Control `GetResource` returns `ResourceNotFoundException`, Config explicitly says `ResourceDeleted`, or for native-API types (AMI) `DescribeImages(ImageIds=…)` returns nothing / `InvalidAMIID.NotFound` / `deregistered`. Anything else keeps the resource `missing` forever. Never mark a resource deleted from a partial or failed scan.
4a. **A resource is identified by `(account_id, aws_region, resource_type, resource_id)`**, not by ARN. ARNs are not unique (a shared AMI has the same ARN in every account that can see it).
5. **Mass-disappearance guard** (R6): if more than `MISSING_GUARD_RATIO` and more than `MISSING_GUARD_MIN` resources in one scope would go missing, change nothing and flag the scope `abnormal`.
6. **One running task per scope** via the lease in `t_inv_aws_scan_shard` (R1–R3).
7. The API process never calls target-account AWS APIs; its only AWS call is `logs:FilterLogEvents` on the hub account's worker log group.
8. No change history. Store only current state (D-07).

## Code style

Python (follow Frost Ming's posts: https://frostming.com/posts/2022/friendly-python-oop/ and https://frostming.com/posts/2024/friendly-python-reuse/):
- Alternate constructors are `@classmethod`s (`from_file`, `from_directory`, `from_env`); never build a half-initialised object and call `load_*()` on it.
- Registries are plain explicit dicts or a small explicit register function. No metaclasses, no `__init_subclass__` auto-registration.
- A class with only static methods should be functions. Use classes only for shared state.
- Don't wrap library objects in new objects; use the library's own extension points (boto3 paginators, `botocore.config.Config` retries, botocore credential providers / `RefreshableCredentials`). Keep the surface you override small.
- Explicit over implicit: no `__getattr__` magic. No singletons. No `except Exception` that returns a normal-looking result.

FastAPI (follow the skill bundled with FastAPI and https://github.com/fastapi/full-stack-fastapi-template):
- `Annotated[..., Depends(...)]` with reusable type aliases (`SessionDep`, `ResourceFilterDep`).
- Group query params into parameter models: `Annotated[ResourceFilter, Query()]`.
- Declare return types; use `response_model` only when the public schema differs. No `ORJSONResponse`/`UJSONResponse`, no `RootModel`, no `...` defaults.
- Router `prefix`/`tags` on the `APIRouter`, not in `include_router()`. One HTTP operation per function.
- Plain `def` endpoints by default (boto3 and SQLModel are sync).
- Serve the built frontend with `app.frontend()`.
- JSON is camelCase on the wire (Pydantic alias generator); snake_case in Python.

Tooling: Python 3.14, uv, Ruff, ty, SQLModel, Alembic, psycopg 3. Frontend: React 19, Vite, TanStack Router/Query/Table, shadcn/ui, Tailwind v4, Biome, Playwright.

## Resource types and worker layering

- One JSON file per type in `backend/app/types/specs/`, validated by `ResourceTypeSpec` at startup. Format: `docs/specs/resource-types.md`.
- Logic JSON can't express goes into that type's single `Hooks` object (optional `list_live`, `get_live`, `filter`, `enrich`; plain functions), registered in the explicit dict `TYPE_HOOKS` in `backend/app/types/hooks.py`. No subclassing, no auto-registration. Hooks only read/enrich data: they never touch the DB, never redact, never decide deletion.
- `get_live` must distinguish three outcomes: found, **definitely gone**, error (raise). Access denied, throttling and unsupported-read are errors, never "gone".
- Layering (DESIGN 10.1): handler takes the scope lease → `Discovery` (`worker/discovery.py`) picks the source and yields `Observation`s, flagging whether the read was complete → `ResourceWriter` (`worker/writer.py`) locks existing rows, overlays, redacts (the only redaction point), hashes, writes conditionally, and runs deletion logic. Consume generators inside the lease; only a complete read may drive deletion.
- Overlay happens in the Writer after the row lock. Mapped fields follow the new data (absent = cleared); unmapped fields keep their old value.
- Refresh: a single-resource "gone" only moves `active` → `missing`; deletion needs `absent_in_scan_at` (a complete scan didn't see it) plus a "gone" by-ID read.
- Adding a type = spec file + fixtures (+ a `Hooks` entry if unavoidable). The parametrised tests pick it up automatically.

## Tests

- **Test data must come from real files or real API responses**: the fixtures in `backend/tests/fixtures/aws/<type>/` (sourced only from official AWS docs, each with a `SOURCE.md` citing URLs) or responses from moto / `devtools/fake_aws`. **Never hand-build or inline test data in test code.** Never invent AWS field shapes.
- Write functional tests only: per-type mapping/redaction/columns against fixtures, the end-to-end scan → write → query → export flow, every concurrency/deletion case A–H in DESIGN 7.1 on a real Postgres, and the main API behaviours.
- No redundant tests: no trivial getters, no re-testing library behaviour, no duplicate cases.

## Local environment

- `podman compose up -d` starts postgres, redis, moto, fake-aws, api, worker. `podman compose run --rm seed` loads demo data (20 accounts × 4 regions, copied from fixtures changing only account, region and IDs).
- AWS is simulated with moto (Config recorder state + preset `SelectResourceConfig` results, EC2, STS, Logs). moto has no Cloud Control, so `devtools/fake_aws` serves Cloud Control `ListResources`/`GetResource` plus the internal account registry (①) and credential (②) services.
- Switch endpoints only with boto3's native `AWS_ENDPOINT_URL` / `AWS_ENDPOINT_URL_CLOUDCONTROL`. **Never add local-only branches to app code.**
- The real formats of services ① and ② are not known yet; keep their clients configurable (see DESIGN §15).

## Naming

- Every AWS resource name starts with `aws-inventory-discovery` and is tagged `Project=aws-inventory-discovery`.
- Redis keys start with `aws-inventory-discovery:`. DB tables are `t_inv_aws_*` in the schema given by `DATABASE_SCHEMA`. The scheduler advisory-lock key is derived from `aws-inventory-discovery:scheduler`.

## Generated and do-not-edit files

- `frontend/src/client/` is generated by `@hey-api/openapi-ts`; regenerate, never hand-edit.
- Alembic migration files are generated, then reviewed; never edit an applied migration.

## Dashboard UI

Follow `docs/specs/dashboard.md` §5–6: Chinese default with English, native-sounding copy, one verb per action, one accent color per page, warm grays only (no orange/terracotta/bright admin blue), at most four font sizes, flat surfaces, concentric radii, confirm only irreversible actions.

## Commands (available after M0)

```bash
podman compose up -d
cd backend && uv run pytest
cd backend && uv run ruff check . && uv run ruff format --check . && uv run ty check
cd backend && uv run alembic upgrade head
cd frontend && npm run dev
cd frontend && npm run generate-client
```
