# PostgreSQL agent recovery integration test

`tests/e2e/test_postgres_agent_recovery.py` exercises real `create_deep_agent`
main-agent delegation through `task` to a procurement subagent, real DeepAgents
HITL middleware, and the application's `PostgresPersistence` with
`AsyncPostgresSaver` and `AsyncPostgresStore`.

From `ERP_OPENCLAW`, in PowerShell:

```powershell
$env:ERP_TEST_POSTGRES = '1'
.venv/Scripts/python.exe -m pytest tests/e2e/test_postgres_agent_recovery.py -q
Remove-Item Env:ERP_TEST_POSTGRES
```

Without `ERP_TEST_POSTGRES=1`, all four cases skip without loading dotenv,
compiling agents, or connecting to PostgreSQL. An opted-in run requires the
existing `.env` / environment `POSTGRES_URI` to point to localhost, 127.0.0.1,
or ::1. PostgreSQL must be available; memory fallback is an error. Normal
persistence initialization runs its idempotent schema setup, so the configured
user needs the application's usual schema permissions. No credential is placed
in command arguments or test output. Child failures report exception type and
stack locations only; arbitrary child output and exception text are withheld.

Each case starts a subprocess that opens persistence, compiles a fresh graph,
and emits `order_create` and `inventory_adjust` in one subagent model response.
The subagent reads the actual procurement YAML `interrupt_on` mapping at runtime
and checks that both tools require approve/reject decisions. Both tool sinks
must remain empty before approval. The test checks the actual action arguments,
review configuration, pending parent task, and persisted child checkpoint
namespace, then saves interrupt IDs and the process ID in a unique Store
namespace and exits, closing its persistence pool.

A second subprocess opens a new pool and compiles another graph. It loads the
pending state and Store marker, checks the same interrupt IDs and a different
process ID, then resumes by interrupt ID. Four cases cover approve/approve,
reject/reject, approve/reject, and reject/approve. Approved fake sinks must run
exactly once during that resume; rejected sinks must never run. The main agent
must finish with no pending work or interrupts.

The scripted `BaseChatModel` validates the tool names actually bound by the
framework and chooses responses from checkpointed messages. It has no external
model client or hidden invocation counter. The two LangChain `@tool` sinks only
append synthetic markers to a process-local list; they do not import or call ERP
or MCP tools. Tracing is disabled and Python socket connections outside loopback
are blocked. PostgreSQL connection parameters are separately restricted to
loopback because psycopg uses libpq.

Cleanup runs in a final subprocess even after a failed assertion. It uses
`adelete_thread` for only that case's generated UUID thread and deletes only the
single Store marker in that thread's namespace. Other sessions and business data
are never selected for deletion. A hard kill of the pytest process can prevent
this finalizer, leaving rows under the `e2e-recovery-` test thread prefix.

Scope: this proves orderly process replacement while a real nested agent is
paused for approval. It complements the API session recovery tests; it does not
exercise HTTP/SSE, the production main-agent/MCP loader, actual ERP writes, or
crash recovery between a business write and checkpoint commit. It does not claim
transactional exactly-once business effects or test replayed approval requests.
