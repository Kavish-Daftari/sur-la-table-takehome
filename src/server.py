"""
MCP server exposing cross-source country analytics (Countries GraphQL +
World Bank + Frankfurter FX) as tools an AI client can drive.

Run with:
    python -m src.server


Design notes:
  - Every tool takes `continent` as a parameter (ISO continent code:
    AF AN AS EU NA OC SA) -- nothing is hardcoded to Europe, per the brief's
    requirement that tools work "for any continent."
  - `year`, `currency`, and `top_n` are always caller-controlled parameters,
    never hardcoded.
  - Reconciliation results are cached per continent for CACHE_TTL_SECONDS to
    avoid re-pulling the ~300-row World Bank country list on every call in a
    single session; a caller can force a refresh via `force_refresh=True`.
  - Every tool that returns a number also returns (or has a companion tool
    that returns) the trace behind it: which countries were included, which
    were excluded and why, which year was actually used if a null forced a
    walk-back, and which FX rate/date was applied.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import analytics, clients
from .reconcile import ReconciliationResult, reconcile_continent

mcp = FastMCP("country-analytics")

CACHE_TTL_SECONDS = 900  # 15 min -- country/currency metadata is near-static
_cache: dict[str, tuple[float, ReconciliationResult]] = {}

VALID_CONTINENTS = {"AF", "AN", "AS", "EU", "NA", "OC", "SA"}


def _get_reconciled(continent: str, force_refresh: bool = False) -> ReconciliationResult:
    code = continent.strip().upper()
    if code not in VALID_CONTINENTS:
        raise ValueError(
            f"Unknown continent code {continent!r}. Valid codes: "
            f"{sorted(VALID_CONTINENTS)} (AF=Africa, AN=Antarctica, AS=Asia, "
            f"EU=Europe, NA=North America, OC=Oceania, SA=South America)."
        )
    now = time.time()
    cached = _cache.get(code)
    if not force_refresh and cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]
    result = reconcile_continent(code)
    _cache[code] = (now, result)
    return result


def _find_country(recon: ReconciliationResult, identifier: str):
    ident = identifier.strip().lower()
    for c in recon.countries:
        if (
            c.iso2.lower() == ident
            or c.iso3.lower() == ident
            or c.graphql_name.lower() == ident
            or c.wb_name.lower() == ident
        ):
            return c
    return None


@mcp.tool()
def list_continents() -> dict[str, Any]:
    """List valid continent codes this server accepts (source: GraphQL continent enum)."""
    return {
        "AF": "Africa",
        "AN": "Antarctica",
        "AS": "Asia",
        "EU": "Europe",
        "NA": "North America",
        "OC": "Oceania",
        "SA": "South America",
    }


@mcp.tool()
def reconcile_continent_tool(continent: str, force_refresh: bool = False) -> dict[str, Any]:
    """
    Pull and reconcile a continent's country list across GraphQL (membership)
    and World Bank (metadata), joined on ISO2 code. Returns the reconciled
    country table and the count of countries. Use list_reconciliation_issues
    for the detailed flagged-issues list.

    Args:
        continent: ISO continent code (AF, AN, AS, EU, NA, OC, SA).
        force_refresh: bypass the 15-minute cache and re-pull from source.
    """
    recon = _get_reconciled(continent, force_refresh)
    return {
        "continent_code": recon.continent_code,
        "continent_name": recon.continent_name,
        "country_count": len(recon.countries),
        "countries": [
            {
                "iso2": c.iso2,
                "iso3": c.iso3,
                "name": c.graphql_name,
                "currency": c.currency,
                "capital": c.capital,
            }
            for c in recon.countries
        ],
        "issue_count": len(recon.issues),
    }


@mcp.tool()
def list_reconciliation_issues(continent: str, force_refresh: bool = False) -> dict[str, Any]:
    """
    List everything this pipeline could not confidently resolve for a
    continent: excluded World Bank aggregate rows, countries present in one
    source but missing from another, currency-field oddities, and name
    mismatches across sources (which were still joined correctly on ISO2).

    Args:
        continent: ISO continent code (AF, AN, AS, EU, NA, OC, SA).
        force_refresh: bypass the 15-minute cache and re-pull from source.
    """
    recon = _get_reconciled(continent, force_refresh)
    return {
        "continent": recon.continent_name,
        "issues": recon.issues,
        "note": (
            "World Bank's `region` taxonomy (e.g. 'Europe & Central Asia') is "
            "NOT used to decide continent membership here -- membership comes "
            "from the GraphQL continent list, which is a geographic partition. "
            "Use `region_total`'s `include_iso2` parameter if you want to "
            "reproduce a different definition of the region's membership."
        ),
    }


@mcp.tool()
def country_metric(
    continent: str, country: str, metric: str, year: int
) -> dict[str, Any]:
    """
    Get a single country's GDP or population for a given year.

    Args:
        continent: ISO continent code the country belongs to (e.g. "EU").
        country: country name or ISO2/ISO3 code (e.g. "France", "FR", "FRA").
        metric: "gdp" (current US$) or "population".
        year: the year requested. If that year's value is null in World Bank
              (common for the most recent 1-2 years), the most recent prior
              non-null year is used instead and flagged in `walked_back`.
    """
    if metric not in ("gdp", "population"):
        raise ValueError("metric must be 'gdp' or 'population'")
    recon = _get_reconciled(continent)
    c = _find_country(recon, country)
    if c is None:
        return {
            "error": f"{country!r} not found in reconciled {recon.continent_name} "
            f"list. Use reconcile_continent_tool to see valid names/codes."
        }
    cm = analytics.get_country_metric(c, metric, year)  
    return {
        "country": cm.name,
        "iso2": cm.iso2,
        "metric": metric,
        "requested_year": year,
        "value_year": cm.year,
        "value": cm.value,
        "unit": "current US$" if metric == "gdp" else "people",
        "walked_back": cm.walked_back,
        "note": (
            f"{year} was null in World Bank; used {cm.year} instead."
            if cm.walked_back
            else None
        ),
    }


@mcp.tool()
def convert_currency(amount: float, from_currency: str, to_currency: str, date: str | None = None) -> dict[str, Any]:
    """
    Convert an amount between currencies using Frankfurter.

    Args:
        amount: amount in `from_currency`.
        from_currency: ISO 4217 code, e.g. "USD".
        to_currency: ISO 4217 code, e.g. "EUR".
        date: ISO date (YYYY-MM-DD) for a pinned historical rate, or omit for
              the latest available rate. If the date falls on a
              weekend/holiday with no published rate, Frankfurter returns the
              nearest prior business day's rate, and the actual date used is
              returned in `rate_date`.
    """
    fx = clients.fetch_fx_rate(from_currency.upper(), to_currency.upper(), date=date)
    return {
        "amount": amount,
        "from_currency": from_currency.upper(),
        "to_currency": to_currency.upper(),
        "rate": fx["rate"],
        "rate_date": fx["date"],
        "requested_date": date,
        "converted_amount": amount * fx["rate"],
    }


@mcp.tool()
def region_total(
    continent: str,
    metric: str,
    year: int,
    currency: str = "USD",
    fx_date: str | None = None,
    include_iso2: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sum GDP or population across a continent for a given year, converted to
    the caller's chosen currency. This is a judgment-call analytic -- see
    list_reconciliation_issues for what "the continent" means here.

    Args:
        continent: ISO continent code (e.g. "EU" for Europe).
        metric: "gdp" or "population" (population sums are unitless/native,
                currency conversion is only meaningful for "gdp").
        year: target year (subject to null walk-back per country; see
              `issues` in the response for any that occurred).
        currency: ISO 4217 code to convert the USD total into. Default "USD"
                  (no conversion). World Bank GDP figures are natively in
                  current US$, so this is always a USD->currency conversion.
        fx_date: ISO date (YYYY-MM-DD) to pin the FX rate to (e.g. the last
                 trading day of `year`). Defaults to the latest available
                 rate if omitted -- for a judgment question like "Europe's
                 total GDP in euros," pinning `fx_date` to Dec 31 of `year`
                 is the more defensible choice and is left to the caller.
        include_iso2: optional list of ISO2 codes to restrict the sum to
                 (overrides the default GraphQL-continent membership -- e.g.
                 pass a World-Bank-style member list if that's the caller's
                 preferred definition of the region).
    """
    if metric not in ("gdp", "population"):
        raise ValueError("metric must be 'gdp' or 'population'")
    recon = _get_reconciled(continent)
    result = analytics.region_total(
        recon, metric, year, currency=currency, fx_date=fx_date, include_iso2=include_iso2  # type: ignore[arg-type]
    )
    return {
        "continent": result.continent,
        "metric": result.metric,
        "requested_year": result.requested_year,
        "currency": result.currency,
        "fx_rate_date": result.fx_date,
        "total_usd": result.total_native_usd,
        "total_converted": result.total_converted,
        "countries_included": len(result.included),
        "countries_excluded": result.excluded,
        "included_detail": [asdict(c) for c in result.included],
        "issues": result.issues,
    }


@mcp.tool()
def rank_countries(
    continent: str,
    metric: str,
    year: int,
    top_n: int = 10,
    per_capita: bool = False,
) -> dict[str, Any]:
    """
    Rank countries in a continent by GDP or population (optionally GDP per
    capita) for a given year.

    Args:
        continent: ISO continent code (e.g. "EU").
        metric: "gdp" or "population". Ignored (treated as "gdp") if
                per_capita=True, since per-capita ranking is always GDP/pop.
        year: target year (subject to null walk-back; see `walked_back` per row).
        top_n: how many rows to return.
        per_capita: if True, rank by GDP per capita instead of raw GDP/population.
    """
    if metric not in ("gdp", "population"):
        raise ValueError("metric must be 'gdp' or 'population'")
    recon = _get_reconciled(continent)
    rows, excluded = analytics.rank_countries(
        recon, metric, year, top_n=top_n, per_capita=per_capita  # type: ignore[arg-type]
    )
    return {
        "continent": recon.continent_name,
        "metric": "gdp_per_capita" if per_capita else metric,
        "requested_year": year,
        "ranking": [
            {
                "rank": i + 1,
                "iso2": r.iso2,
                "name": r.name,
                "value": r.value,
                "value_year": r.year,
                "walked_back": r.walked_back,
            }
            for i, r in enumerate(rows)
        ],
        "excluded": excluded,
    }


if __name__ == "__main__":
    mcp.run()
