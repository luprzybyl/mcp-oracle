# AGENTS.md

Guidance for AI agents (and humans who behave like them) working on this repo.

## What this is

A single-file MCP server (`server.py`) exposing an Oracle database to LLM clients over
stdio. Python 3.10+, `mcp[cli]` (FastMCP), `python-oracledb` thin mode, `python-dotenv`.

## Layout

```
server.py          # the entire application — three always-on @mcp.tool() functions,
                   # plus execute/execute_plsql registered only when ORACLE_READONLY=false
requirements.txt   # three deps, no lockfile
pyproject.toml     # packaging — console entry point: mcp-oracle = server:main
LICENSE            # MIT
.env               # local credentials — NEVER commit
.env.example       # committed template
```

There is no real package structure beyond the entry point, no tests, no CI. Deliberately.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Credentials come from `.env` or the process env — **process env wins**
(`load_dotenv` is called with `override=False`). Connection vars: `ORACLE_USER`,
`ORACLE_PASSWORD`, `ORACLE_DSN`, `ORACLE_SCHEMA`. Behavior vars:

- `ORACLE_READONLY` — default `true` (values `1`/`true`/`yes`/`on`, case-insensitive).
  When true, `execute`/`execute_plsql` are **not registered at all** — not "registered
  but erroring". Code that inspects the tool list must handle both modes. `false`
  enables them.
- `ORACLE_CALL_TIMEOUT_MS` — default `30000`; per-statement call timeout applied to all
  cursors; `0` disables.
- `ORACLE_LOG_LEVEL` — unset means logging is fully off. When set
  (`DEBUG`/`INFO`/`WARNING`/`ERROR`), logs go to **stderr only, never stdout** — stdout
  is the stdio MCP transport and a single stray print corrupts it. Never log the
  password.

Every new connection runs `ALTER SESSION SET CURRENT_SCHEMA = <ORACLE_SCHEMA>` — keep
this in mind when writing tools that touch other schemas (use fully qualified names).

## Verify changes

No test suite. Verification is:

1. `python -c "import server"` — import check (read-only path). Repeat with
   `ORACLE_READONLY=false python -c "import server"` to cover the write-tool
   registration path.
2. `mcp dev server.py` — MCP Inspector, call tools by hand.
3. Exercise the tool against a real DB; `list_tables` is the cheapest smoke test
   (needs valid `.env`).

## Conventions

- **Tools return JSON strings, always** — including errors as `{"error": "..."}`.
  Never raise through the tool boundary; MCP clients see return values, not tracebacks.
- **Statement routing by first keyword.** `_first_keyword()` strips leading `--` and
  `/* */` comments. If you add a statement class, update `READONLY_KEYWORDS`,
  `WRITE_KEYWORDS`, or `PLSQL_KEYWORDS` and the docstrings of all three exec tools —
  the error messages cross-reference each other.
- **Write tools are conditionally registered.** `execute`/`execute_plsql` only exist
  when `ORACLE_READONLY=false`; in read-only mode they are absent, not disabled.
  Docstrings, README and any tool-listing checks must stay consistent with both modes.
- **Row cap: 500** (`_fetch_rows`). Bump deliberately, not accidentally.
- `execute`/`execute_plsql` auto-commit after every call. That's the contract; don't
  silently add transaction machinery without changing the docstrings and README.
- Results serialize with `default=str, ensure_ascii=False` — preserves dates/LOBs as
  strings and non-ASCII text.
- Keep it stdlib-thin and single-file. If the file grows past ~400 lines, split by tool
  domain, not prematurely.

## Safety rules for agents

- Do not commit `.env` or any file containing credentials.
- Do not weaken the keyword gates "to make things work" — the DB user's grants are the
  only real sandbox, and the gates exist to keep agents from firing DML through `query()`.
- `execute` commits. Do not run speculative DDL/DML against a live schema to "test" it;
  use a disposable schema or ask the human.
