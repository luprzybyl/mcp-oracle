# mcp-oracle

An [MCP](https://modelcontextprotocol.io) server that gives LLM agents direct access to an
Oracle database over stdio. Ships up to five tools: read-only queries, DML/DDL execution
with auto-commit, anonymous PL/SQL blocks with `DBMS_OUTPUT` capture, and schema
introspection. Starts **read-only by default** — the write tools aren't merely blocked,
they're not registered at all until you opt in.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) and
[python-oracledb](https://python-oracledb.readthedocs.io/) (thin mode — no Oracle Instant
Client required).

## Tools

| Tool | Purpose |
|------|---------|
| `query` | Execute `SELECT` / `WITH` / `EXPLAIN`, returns JSON array of row objects (max 500 rows) |
| `execute` *(write mode only)* | Execute a single DML/DDL statement (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME`, `COMMENT`, `ANALYZE`, `PURGE`, `FLASHBACK`, `GRANT`, `REVOKE`, `CALL`) and commit |
| `execute_plsql` *(write mode only)* | Run an anonymous `BEGIN`/`DECLARE` block, commit, and return captured `DBMS_OUTPUT` lines |
| `list_tables` | List all tables in the configured schema |
| `describe_table` | Column name, type, length, and nullability for a table |

`execute` and `execute_plsql` only exist when `ORACLE_READONLY=false`; in the default
read-only mode they are never registered, so clients can't call what isn't there.

Statements are routed by their first keyword (leading SQL comments are skipped), so each
tool rejects work that belongs to a sibling tool with a hint about where it should go.

All statements run with `ALTER SESSION SET CURRENT_SCHEMA = <ORACLE_SCHEMA>`, so
unqualified object names resolve against the configured schema.

## Requirements

- Python 3.10+
- Reachable Oracle database (tested against Oracle 23ai Free / `freepdb1`)
- No Oracle Instant Client needed — python-oracledb thin mode is used by default

## Getting started

### Install into a venv

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Or run it as a package

`pyproject.toml` exposes a `mcp-oracle` console script, so `pipx`/`uvx` can install and
run it without maintaining a project-local venv:

```bash
pipx install .
mcp-oracle

# or run without installing at all:
uvx --from . mcp-oracle
```

### Configure

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ORACLE_USER` | `devin` | Database user |
| `ORACLE_PASSWORD` | `windsurf` | Password |
| `ORACLE_DSN` | `localhost:1521/freepdb1` | DSN (`host:port/service`, TNS alias, or connect string) |
| `ORACLE_SCHEMA` | `tartak` | Schema set via `CURRENT_SCHEMA` for every session |
| `ORACLE_READONLY` | `true` | On by default — `execute`/`execute_plsql` are not registered. Set to `false` to enable write tools (true values: `1`/`true`/`yes`/`on`, case-insensitive) |
| `ORACLE_CALL_TIMEOUT_MS` | `30000` | Per-statement call timeout in milliseconds, applied to all cursors; `0` disables |
| `ORACLE_LOG_LEVEL` | *(unset — off)* | `DEBUG`/`INFO`/`WARNING`/`ERROR`; logs connections, tool calls, timings and errors to **stderr** only (never the password) |

Real environment variables take precedence over `.env` values, so one-off overrides like
`ORACLE_READONLY=false mcp-oracle` work without editing the file.

### First run

Out of the box the server starts in **read-only mode**: only `query`, `list_tables` and
`describe_table` are exposed. To get `execute` and `execute_plsql`, set
`ORACLE_READONLY=false` in `.env` (or in the process environment — it wins over `.env`).

## Running

Stdio transport (what MCP clients spawn):

```bash
python server.py
# or, if installed as a package:
mcp-oracle
```

MCP Inspector for interactive poking:

```bash
mcp dev server.py
```

### Client configuration

Example for `mcp.json`-style configs (Claude Desktop, Windsurf, Cursor, Devin, …):

```json
{
  "mcpServers": {
    "oracle": {
      "command": "/path/to/mcp-oracle/.venv/bin/python",
      "args": ["/path/to/mcp-oracle/server.py"]
    }
  }
}
```

Point `command` at the venv interpreter so dependencies resolve without activating
anything. If you installed via `pipx`, `"command": "mcp-oracle"` works too.

## Security notes

- **Read-only by default.** `execute`/`execute_plsql` are not registered at all unless
  `ORACLE_READONLY=false`. The strongest write protection is a client that can't see
  the tools.
- The keyword gating in `query`/`execute`/`execute_plsql` is a routing convenience, **not**
  a security boundary. Real enforcement must come from grants on `ORACLE_USER` — run the
  server with a least-privilege account.
- `execute` and `execute_plsql` **commit unconditionally**. There is no rollback, no dry
  run, no undo. Point this at production only if you enjoy adrenaline.
- `ORACLE_CALL_TIMEOUT_MS` caps every statement at 30 s by default — raise it for
  legitimately long calls rather than disabling it.
- `ORACLE_LOG_LEVEL` writes diagnostics to **stderr** only; stdout is reserved for the
  stdio MCP transport. The password is never logged.
- Never commit `.env` — it's in `.gitignore`, keep it that way.

## License

MIT
