import os
import re
import sys
import json
import logging
import oracledb
from pathlib import Path
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

DB_USER = os.environ.get("ORACLE_USER", "devin")
DB_PASSWORD = os.environ.get("ORACLE_PASSWORD", "windsurf")
DB_DSN = os.environ.get("ORACLE_DSN", "localhost:1521/freepdb1")
DB_SCHEMA = os.environ.get("ORACLE_SCHEMA", "tartak")

_ro = os.environ.get("ORACLE_READONLY", "").strip().lower()
READONLY = True if not _ro else _ro in ("1", "true", "yes", "on")

try:
    CALL_TIMEOUT_MS = max(0, int(os.environ.get("ORACLE_CALL_TIMEOUT_MS", "30000") or "0"))
except ValueError:
    CALL_TIMEOUT_MS = 0

log = logging.getLogger("mcp-oracle")
_level = os.environ.get("ORACLE_LOG_LEVEL", "").strip().upper()
if _level:
    log.setLevel({"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                  "WARNING": logging.WARNING, "ERROR": logging.ERROR}.get(_level, logging.INFO))
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
log.info("startup: readonly=%s dsn=%s user=%s schema=%s call_timeout_ms=%s",
         READONLY, DB_DSN, DB_USER, DB_SCHEMA, CALL_TIMEOUT_MS)

READONLY_KEYWORDS = {"SELECT", "WITH", "EXPLAIN"}
WRITE_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "COMMENT",
    "ANALYZE", "PURGE", "FLASHBACK", "GRANT", "REVOKE", "CALL",
}
PLSQL_KEYWORDS = {"BEGIN", "DECLARE"}

mcp = FastMCP("oracle")


def _first_keyword(sql: str) -> str:
    """Return the first SQL keyword, skipping leading comments."""
    s = sql.strip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            if nl == -1:
                return ""
            s = s[nl + 1:].lstrip()
        elif s.startswith("/*"):
            end = s.find("*/")
            if end == -1:
                return ""
            s = s[end + 2:].lstrip()
        else:
            break
    m = re.match(r"[A-Za-z]+", s)
    return m.group(0).upper() if m else ""


def _connect():
    log.debug("connect: dsn=%s user=%s", DB_DSN, DB_USER)
    conn = oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)
    with conn.cursor() as cur:
        cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {DB_SCHEMA}")
    return conn


def _fetch_rows(cur, limit: int = 500):
    columns = [col[0] for col in cur.description]
    rows = cur.fetchmany(limit)
    return [dict(zip(columns, row)) for row in rows]


def _drain_dbms_output(cur) -> list:
    lines = []
    line = cur.var(str)
    status = cur.var(int)
    while True:
        cur.callproc("dbms_output.get_line", (line, status))
        if status.getvalue() != 0:
            break
        lines.append(line.getvalue())
    return lines


@mcp.tool()
def query(sql: str) -> str:
    """Execute a read-only SQL query (SELECT / WITH / EXPLAIN).
    Access covers the TARTAK schema plus APEX metadata views
    (APEX_APPLICATIONS, APEX_APPLICATION_PAGES, APEX_WORKSPACES, ...).
    Returns results as JSON array of objects (max 500 rows)."""
    if _first_keyword(sql) not in READONLY_KEYWORDS:
        if READONLY:
            msg = "query() accepts only SELECT / WITH / EXPLAIN. Statement not allowed: server is in read-only mode."
        else:
            msg = "query() accepts only SELECT / WITH / EXPLAIN. Use 'execute' for DML/DDL or 'execute_plsql' for PL/SQL blocks."
        return json.dumps({"error": msg})
    log.debug("query: %s", sql[:200])
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                if CALL_TIMEOUT_MS:
                    cur.call_timeout = CALL_TIMEOUT_MS
                cur.execute(sql.strip().rstrip(";"))
                return json.dumps(_fetch_rows(cur), default=str, ensure_ascii=False)
    except Exception as e:
        log.error("query error: %s", e)
        return json.dumps({"error": str(e)})


def execute(sql: str) -> str:
    """Execute a single SQL statement and commit it.
    Allowed: INSERT, UPDATE, DELETE, MERGE, CREATE, ALTER, DROP, TRUNCATE,
    RENAME, COMMENT, ANALYZE, PURGE, FLASHBACK, GRANT, REVOKE, CALL.
    Statements are resolved against the TARTAK schema. Privileges are
    enforced by the database user — DDL on TARTAK objects is not granted.
    Returns rowcount (or result rows if the statement produces them)."""
    kw = _first_keyword(sql)
    if kw in READONLY_KEYWORDS:
        return json.dumps({"error": f"'{kw}' statements belong in query(), not execute()."})
    if kw in PLSQL_KEYWORDS:
        return json.dumps({"error": f"'{kw}' blocks belong in execute_plsql(), not execute()."})
    if kw not in WRITE_KEYWORDS:
        return json.dumps({"error": f"Unsupported statement '{kw or '?'}'. Allowed: {', '.join(sorted(WRITE_KEYWORDS))}."})
    log.debug("execute: %s", sql[:200])
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                if CALL_TIMEOUT_MS:
                    cur.call_timeout = CALL_TIMEOUT_MS
                cur.execute(sql.strip().rstrip(";"))
                result = {"status": "committed", "rowcount": cur.rowcount}
                if cur.description:
                    result["rows"] = _fetch_rows(cur)
                conn.commit()
                return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        log.error("execute error: %s", e)
        return json.dumps({"error": str(e)})


def execute_plsql(plsql: str) -> str:
    """Execute an anonymous PL/SQL block (BEGIN ... END; / DECLARE ... END;)
    and commit it. Captures and returns DBMS_OUTPUT lines.
    Use for calling TARTAK procedures/functions or APEX APIs
    (e.g. apex_util.set_security_group_id, apex_collection)."""
    kw = _first_keyword(plsql)
    if kw not in PLSQL_KEYWORDS:
        return json.dumps({"error": "execute_plsql() requires an anonymous block starting with BEGIN or DECLARE."})
    log.debug("execute_plsql: %s", plsql[:200])
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                if CALL_TIMEOUT_MS:
                    cur.call_timeout = CALL_TIMEOUT_MS
                cur.execute("BEGIN dbms_output.enable(); END;")
                cur.execute(plsql)
                result = {"status": "committed", "rowcount": cur.rowcount}
                output = _drain_dbms_output(cur)
                if output:
                    result["dbms_output"] = output
                conn.commit()
                return json.dumps(result, default=str, ensure_ascii=False)
    except Exception as e:
        log.error("execute_plsql error: %s", e)
        return json.dumps({"error": str(e)})


if not READONLY:
    mcp.tool()(execute)
    mcp.tool()(execute_plsql)


@mcp.tool()
def list_tables() -> str:
    """List all tables in the TARTAK schema."""
    sql = f"SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = '{DB_SCHEMA.upper()}' ORDER BY TABLE_NAME"
    log.debug("list_tables: %s", sql[:200])
    return query(sql)


@mcp.tool()
def describe_table(table_name: str) -> str:
    """Describe columns of a given TARTAK table (name, type, nullable)."""
    sql = f"""
        SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE
        FROM ALL_TAB_COLUMNS
        WHERE OWNER = '{DB_SCHEMA.upper()}' AND TABLE_NAME = UPPER('{table_name}')
        ORDER BY COLUMN_ID
    """
    log.debug("describe_table: %s", sql[:200])
    return query(sql)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
