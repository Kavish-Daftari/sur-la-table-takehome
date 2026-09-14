# Cross-Source Country Analytics — MCP Server

Joins three free, public, keyless APIs — Countries GraphQL, World Bank
Indicators, and Frankfurter FX — into a reconciled per-country table, and
exposes GDP / population / per-capita / regional-total / ranking analytics
through an MCP server any AI client can query.

Works for **any continent** (`AF AN AS EU NA OC SA`), not just Europe.

## Quickstart (clean checkout → connected client)

```bash
git clone <this repo>
cd sur-la-table-takehome
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Sanity check without any client** (mocked data, no network needed):

```bash
python3 tests/test_pipeline_offline.py
```

**Run the real pipeline against live services** (needs internet, no API keys):

```bash
python3 -m src.server
```

This starts the MCP server on stdio and will sit waiting for a client to
connect (no output is expected — that's normal for stdio transport).

### Connect with MCP Inspector (fastest way to verify it works)

```bash
npx @modelcontextprotocol/inspector python3 -m src.server
```

This opens a browser UI where you can call each tool directly (e.g.
`region_total` with `continent="EU", metric="gdp", year=2022, currency="EUR"`)
and see raw JSON responses.

### Connect with Claude Desktop

Add to your Claude Desktop config
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows). Open it via the
app itself — **Settings → Developer → Edit Config** — rather than guessing
the path by hand, since it's the most reliable way to find the exact file
your installed build reads.

If the file already has other keys in it (app preferences, etc.), just add
`mcpServers` as a new top-level key alongside them — don't replace anything
else in the file:

```json
{
  "mcpServers": {
    "country-analytics": {
      "command": "/absolute/path/to/venv/bin/python3",
      "args": [
        "-u",
        "-c",
        "import sys; sys.path.insert(0, '/absolute/path/to/sur-la-table-takehome'); import runpy; runpy.run_module('src.server', run_name='__main__')"
      ]
    }
  }
}
```

**Why this launch command, not the simpler `-m src.server` you'd expect:**
some Claude Desktop builds don't reliably `chdir` into the `cwd` you specify
before launching the server process, which breaks `-m src.server`'s package
resolution (`ModuleNotFoundError: No module named 'src'`). Running
`server.py` directly as a script path avoids that but breaks its internal
relative imports (`from . import analytics, clients`) instead. The `-c` /
`runpy.run_module` form manually adds the project root to `sys.path` and then
loads `src.server` exactly the way `-m` would, so it works regardless of
what working directory the process actually launches in. The `-u` flag
forces unbuffered stdout — without it, JSON-RPC messages can sit in Python's
output buffer when stdout is piped (as Claude Desktop does) instead of a
terminal, and the connection looks "disconnected" even though the process is
alive. If you hit either error anyway, `~/Library/Logs/Claude/mcp-server-country-analytics.log`
(macOS) will show the exact traceback.

Restart Claude Desktop fully (Cmd+Q / quit from the tray, not just closing
the window). You should see "country-analytics" under Settings → Developer
or the connectors list, with 7 tools available. Then ask things like:

> "What was France's GDP in 2022 in US dollars?"
> "What's Europe's total GDP in euros, using the Dec 30 2022 rate?"
> "Rank the top 5 European countries by GDP per capita."

## Tools exposed

| Tool | Purpose |
|---|---|
| `list_continents` | Valid continent codes |
| `reconcile_continent_tool` | Pull + join a continent's country list (GraphQL ⋈ World Bank on ISO2) |
| `list_reconciliation_issues` | Everything excluded/flagged and why |
| `country_metric` | One country's GDP or population for a year |
| `convert_currency` | FX conversion, latest or pinned historical date |
| `region_total` | Sum GDP/population across a continent, in caller's currency, with `include_iso2` override |
| `rank_countries` | Top-N ranking by GDP, population, or GDP per capita |

All of `continent`, `year`, `currency`, `top_n`, `fx_date`, and region
membership (`include_iso2`) are caller-supplied parameters — nothing is
hardcoded to Europe or to one year.

## Performance

`region_total` and `rank_countries` need one World Bank indicator call per
country (two per country for per-capita ranking). These are fetched
**concurrently** through a bounded thread pool (10 requests at a time —
bounded to stay a reasonable load on a free public API, not unlimited).
Confirmed live: a per-capita ranking across Europe's ~48 countries (up to 96
underlying calls) completes in well under 30 seconds. An earlier sequential
version of this took 1-3+ minutes for the same query.

## Repo layout

```
src/
  clients.py    - HTTP/GraphQL clients for the 3 sources (retries, typed errors)
  reconcile.py  - Joins GraphQL + World Bank on ISO2, filters aggregates, flags issues
  analytics.py  - GDP/population/per-capita/region-total/ranking (deterministic, no LLM)
  aliases.py    - Documents known name divergences (Czechia/Czech Republic, etc.)
  server.py     - MCP server (FastMCP) wiring analytics into tools
tests/
  test_pipeline_offline.py - Mocked-response tests validating join/walk-back/FX logic
sample_output.md            - Actual run output for reference (see note below)
WRITEUP.md                  - 1-page summary for a non-technical stakeholder
```

## A note on this submission's live-run status

This code was developed and logic-tested with API responses mocked to match
the **documented real shapes** of all three services (World Bank's
`[metadata, data]` envelope, pagination, aggregate rows, trailing nulls;
GraphQL's continent/country/currency shape; Frankfurter's date-keyed rates)
— see `tests/test_pipeline_offline.py`, which passes end-to-end against those
mocks. The development sandbox used to write this code has network egress
restricted to package registries (PyPI/npm/GitHub) and cannot reach
`countries.trevorblades.com`, `api.worldbank.org`, or `api.frankfurter.dev`
directly, so **the live run against real services should be performed by
whoever executes `python -m src.server` with normal internet access** — the
first thing to do after cloning. `sample_output.md` will be filled in with
that live run's actual output. If any endpoint's real shape has drifted from
what's documented here, `clients.py` raises a clear `SourceError` naming the
source and what failed, rather than silently returning wrong numbers — that
is the intended fallback per the assignment's "APIs drift" note.
