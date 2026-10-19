# Inventory adjustment

## Contract

`POST /api/inventory/adjust` accepts a typed JSON object:

```json
{"partId": 1, "delta": -3, "reason": "synthetic correction", "requestId": "adjustment-unique-key"}
```

- `partId`: strict positive integer, at most signed BIGINT maximum.
- `delta`: strict nonzero signed INT. Booleans, strings and fractions are rejected.
- `reason`: nonblank string, at most 1000 characters; preserved exactly.
- `requestId`: required, nonblank, at most 128 characters, no surrounding whitespace.
  Keys are globally unique and case sensitive. Use a UUID or equally unique identifier
  per intended adjustment. Keep it unchanged across retries and approval resume.
- Unknown fields are rejected. Inventory quantities must remain within 0..2147483647.

Success is HTTP 200, including retries:

```json
{"code":200,"data":{"partId":1,"delta":-3,"reason":"synthetic correction","requestId":"adjustment-unique-key","beforeQuantity":12,"afterQuantity":9}}
```

These values are illustrative synthetic data. An identical retry returns the original
result, even after later adjustments. Any changed part, delta or reason under an
already committed key returns HTTP 409 (`IDEMPOTENCY_CONFLICT`). Reasons are compared
exactly, including whitespace. Failed transactions do not consume their keys.

Other errors: 422 invalid body, 404 `INVENTORY_NOT_FOUND` (including deleted inventory),
409 `INSUFFICIENT_INVENTORY` or `INVENTORY_QUANTITY_OVERFLOW`, 500
`INVENTORY_ADJUST_FAILED`. Backend errors use FastAPI's `detail` envelope.

## Transactions and ledger

`backend/inventory.py` starts a transaction and inserts the request into
`inventory_adjustment` before taking `SELECT ... FOR UPDATE` on the active inventory
row. This serializes identical keys via the unique primary key and distinct keys
for the same part via the row lock. Stock and ledger before/after quantities commit
together. Any failure rolls both back, including a ledger write failure.

A duplicate insert waits for the first transaction to finish. On duplicate key 1062,
the losing transaction rolls back and reads the committed result using a fresh
transaction snapshot. No stock mutation occurs on that path. No in-process locks
or caches are needed; separate backend workers use the same database guarantees.
Ledger before/after columns are nullable only while an uncommitted request is being
processed; successful application transactions always populate both before commit.

## Migration and Docker integration

Run from the project root after the base ERP schema exists, before serving requests:

```sh
python -m backend.migrate
```

This is the entrypoint expected by the separate Docker schema bootstrap worker.
It uses `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, and the
backend's existing EVAL_MODE database guard. Container environment values override
`.env` (`load_dotenv(override=False)`). Successful execution prints a short completion
message and exits zero; connection/DDL failures exit nonzero.

The migration executes only `CREATE TABLE IF NOT EXISTS` for:

- `inventory_adjustment`: InnoDB ledger with request primary key, exact reason,
  part/delta, before/after quantities, timestamp and part index.
- `purchase_order_inventory_receipt`: existing receipt schema with `order_id`
  primary key, `posted_at` and foreign key to `purchase_order(id)`.

Repeat runs preserve existing tables and records. No DROP, ALTER, seed data,
backfill or inventory writes occur. Base tables (including `purchase_order`) must
already exist. MySQL DDL commits independently, so a partial migration is repaired
by rerunning it. This command does not reconcile incompatible existing schemas.
Existing installations with historical receipts but no receipt ledger need a
separately reviewed history reconciliation; this migration never infers receipts
from business rows.

## MCP and approval

`inventory_adjust(part_id, delta, reason, request_id)` exposes four mandatory typed
arguments through FastMCP and maps them to the REST fields. It returns
`{ok: true, data: ...}` or `{ok: false, error: {code, status?, retryable}}`.
HTTP 4xx errors are not retryable; transport and server errors may be retried with
the identical key and payload. The wrapper does not generate a new key on retry.

`load_mcp_tools` gives this mutation only to `procurement-order`; inventory access
for `procurement-analyst` is the exact read-only `inventory_warning` tool rather
than a wildcard inventory prefix. The order YAML explicitly lists the mutation and
requires `interrupt_on.inventory_adjust.allowed_decisions: [approve, reject]`.
Approval happens in the agent runtime before execution; the REST route is an
internal backend endpoint and does not independently implement user authorization.
The main prompt routes inventory adjustments to the order agent. The touched order
prompt also reflects Python/local execution and distinct create/update schemas.

## Implementation and verification

Completed sequence: write failing contract tests; implement transaction and
migration; integrate typed MCP tool and order-only approval; run targeted regressions
and isolated real MySQL concurrency tests. Initial contract run: 27 failures, 3 passes.

Offline tests use in-memory SQLite transactions with a small MySQL cursor adapter
and recorded locking SQL. They never connect to the business database. Real InnoDB
tests are opt-in and create fresh random `inventory_test_<uuid>` databases, using
only synthetic rows, then delete those exact generated databases. They require
CREATE/DROP DATABASE privileges on the configured MySQL server. No model or external
service is called by these tests.

```powershell
$env:INVENTORY_TEST_MYSQL='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_inventory_adjustment.py tests/integration/test_inventory_mysql.py tests/integration/test_mcp_tools.py tests/integration/test_order_transactions.py tests/integration/test_rest_contracts.py tests/unit/test_backend_models.py tests/unit/test_order_tools.py -q
```

Coverage includes validation without connection, identical retries after later
adjustments, changed-payload conflicts, missing/deleted inventory, zero balance,
negative/overflow rejection, atomic rollback, case-sensitive keys, tool schema and
error mapping, agent filters, approval configuration, environment precedence,
concurrent duplicate requests, cross-part conflicts, serialized concurrent debits,
and repeated migrations preserving populated ledger and receipt tables.

Final scoped validation: **60 passed**, including all four real InnoDB tests.
One existing Starlette/AnyIO deprecation warning remains. No business inventory was
written during verification. The migration was executed only against disposable
synthetic databases; deploying it to the target environment is the bootstrap step.
