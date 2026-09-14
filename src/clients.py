"""
Thin, defensive clients for the three public data sources.

Each client:
  - has a short timeout and retries with backoff (sources are free/public and
    can be slow or flaky -- this is called out explicitly in the write-up),
  - raises a typed SourceError with enough context to report *which* source
    and *what* call failed, so the MCP layer can surface a clear error
    instead of a stack trace,
  - does NOT hide partial failures: if a source is down, callers get an
    explicit exception, not an empty/zero result that could be mistaken for
    a real answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

GRAPHQL_URL = "https://countries.trevorblades.com/"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"
FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"

DEFAULT_TIMEOUT = 15.0
MAX_RETRIES = 3
BACKOFF_SECONDS = 1.5


class SourceError(RuntimeError):
    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"[{source}] {detail}")


def _retry(fn, source: str, *args, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * attempt)
    raise SourceError(source, f"failed after {MAX_RETRIES} attempts: {last_exc}")


# --------------------------------------------------------------------------
# 1. Countries GraphQL (continent membership + ISO2 + declared currency)
# --------------------------------------------------------------------------

CONTINENT_QUERY = """
query Continent($code: ID!) {
  continent(code: $code) {
    code
    name
    countries {
      code
      name
      currency
      capital
    }
  }
}
"""


def fetch_continent_countries(continent_code: str) -> dict[str, Any]:
    """Returns {code, name, countries:[{code, name, currency, capital}]}."""

    def _do():
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.post(
                GRAPHQL_URL,
                json={"query": CONTINENT_QUERY, "variables": {"code": continent_code}},
            )
            resp.raise_for_status()
            payload = resp.json()
            if "errors" in payload and payload["errors"]:
                raise SourceError("graphql", str(payload["errors"]))
            continent = payload.get("data", {}).get("continent")
            if continent is None:
                raise SourceError(
                    "graphql", f"unknown continent code {continent_code!r}"
                )
            return continent

    return _retry(_do, "graphql")


# --------------------------------------------------------------------------
# 2. World Bank (country metadata, GDP, population)
# --------------------------------------------------------------------------


def fetch_wb_country_metadata() -> list[dict[str, Any]]:
    """
    Returns the full World Bank country list, INCLUDING aggregate rows
    ("World", "Euro area", income-group buckets, etc). Callers must filter
    on region.value == "Aggregates" to exclude them -- we do that filtering
    in reconcile.py, not here, so this layer stays a faithful mirror of the
    source. Paginates until all pages are collected.
    """

    def _do():
        results: list[dict[str, Any]] = []
        page = 1
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            while True:
                resp = client.get(
                    f"{WORLD_BANK_BASE}/country",
                    params={"format": "json", "per_page": 400, "page": page},
                )
                resp.raise_for_status()
                body = resp.json()
                # World Bank convention: [metadata, data]
                if not isinstance(body, list) or len(body) != 2:
                    raise SourceError(
                        "world_bank", f"unexpected response shape: {type(body)}"
                    )
                meta, data = body
                if data is None:
                    break
                results.extend(data)
                total_pages = meta.get("pages", 1)
                if page >= total_pages:
                    break
                page += 1
        return results

    return _retry(_do, "world_bank")


def fetch_wb_indicator(
    iso3_or_iso2: str, indicator: str, year: int | None = None, lookback_years: int = 6
) -> dict[str, Any]:
    """
    Fetches a single indicator series for one country and returns the most
    recent NON-NULL observation at or before `year` (or the latest available
    if year is None), walking back up to `lookback_years`. Returns:
      {"value": float|None, "year": int|None, "walked_back": bool,
       "requested_year": int|None}
    `walked_back=True` means the requested year's value was null and an
    earlier year was used instead -- callers must surface this, not hide it.
    """

    def _do():
        end_year = year or 2024
        start_year = end_year - lookback_years
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.get(
                f"{WORLD_BANK_BASE}/country/{iso3_or_iso2}/indicator/{indicator}",
                params={
                    "format": "json",
                    "date": f"{start_year}:{end_year}",
                    "per_page": 100,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, list) or len(body) != 2 or body[1] is None:
                return {
                    "value": None,
                    "year": None,
                    "walked_back": False,
                    "requested_year": year,
                }
            series = body[1]
            # Series is time-ordered descending by year already, but don't
            # assume -- sort explicitly.
            series = sorted(
                series, key=lambda r: int(r["date"]), reverse=True
            )
            for row in series:
                if row.get("value") is not None:
                    obs_year = int(row["date"])
                    return {
                        "value": float(row["value"]),
                        "year": obs_year,
                        "walked_back": (year is not None and obs_year != year),
                        "requested_year": year,
                    }
            return {
                "value": None,
                "year": None,
                "walked_back": False,
                "requested_year": year,
            }

    return _retry(_do, "world_bank")


# --------------------------------------------------------------------------
# 3. Frankfurter FX
# --------------------------------------------------------------------------


def fetch_fx_rate(
    base: str, symbol: str, date: str | None = None
) -> dict[str, Any]:
    """
    Returns {"rate": float, "date": str} for 1 unit of `base` in `symbol`.
    `date` is an ISO date (YYYY-MM-DD) for a pinned historical rate, or None
    for latest. Frankfurter has no data for weekends/ECB holidays -- if the
    requested date has no rate, it returns the most recent prior business
    day's rate and we surface that via the returned "date" field (which may
    differ from the requested date). We never silently substitute a rate for
    an unsupported currency; that raises SourceError instead.
    """

    def _do():
        path = date if date else "latest"
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.get(
                f"{FRANKFURTER_BASE}/{path}",
                params={"base": base, "symbols": symbol},
            )
            if resp.status_code == 404:
                raise SourceError(
                    "frankfurter", f"no rate for {base}->{symbol} on {path}"
                )
            resp.raise_for_status()
            body = resp.json()
            rates = body.get("rates", {})
            if symbol not in rates:
                raise SourceError(
                    "frankfurter",
                    f"{symbol!r} not covered by Frankfurter "
                    f"(base={base}, requested date={path})",
                )
            return {"rate": float(rates[symbol]), "date": body.get("date", path)}

    return _retry(_do, "frankfurter")


def fetch_fx_rates_multi(
    base: str, symbols: list[str], date: str | None = None
) -> dict[str, Any]:
    """Batch version of fetch_fx_rate -- one HTTP call for many symbols."""

    def _do():
        path = date if date else "latest"
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            resp = client.get(
                f"{FRANKFURTER_BASE}/{path}",
                params={"base": base, "symbols": ",".join(symbols)},
            )
            resp.raise_for_status()
            body = resp.json()
            return {
                "date": body.get("date", path),
                "rates": body.get("rates", {}),
                "missing": [s for s in symbols if s not in body.get("rates", {})],
            }

    return _retry(_do, "frankfurter")
