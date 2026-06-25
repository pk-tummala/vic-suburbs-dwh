# tools/

Auxiliary developer scripts. These are **standalone** — run them directly with the
project's Python; they are not an importable package (no `__init__.py` by design).

| Script | What it does | Run |
|---|---|---|
| `build-er-diagram.py` | Regenerates `docs/data-model/er-fact-constellation.svg` (the fact-constellation ER diagram) from code, so the diagram is reproducible. Uses only the Python standard library. | `python3 tools/build-er-diagram.py` or `make er-diagram` |
| `dbsql.sh` | Runs a SQL statement on a Databricks SQL warehouse via the Statement Execution API (the CLI has no `sql query` command). Used by `deployment/bootstrap.sh` to `CREATE CATALOG` on Default-Storage workspaces, and handy for ad-hoc queries. Requires the Databricks CLI + jq; auto-picks a warehouse or honours `--warehouse-id` / `DATABRICKS_WAREHOUSE_ID`. | `./tools/dbsql.sh "SELECT ..."` or `make query SQL="SELECT ..."` |

The script resolves paths relative to itself, so it works from any directory.
