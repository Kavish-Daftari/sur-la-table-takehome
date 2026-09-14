"""
Analytics on top of the reconciled continent table. All numeric computation
lives here in deterministic code -- no LLM ever touches a sum, a rate, or a
division (see write-up: "where an LLM belongs vs. where deterministic code is
more reliable").

Every function returns not just the number, but the trace needed to answer
"how did you get that": which countries were included/excluded, which year
values actually came from (after any null walk-back), and which FX rate/date
was used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from . import clients
from .reconcile import ReconciledCountry, ReconciliationResult

Metric = Literal["gdp", "population"]

INDICATOR_CODES = {
    "gdp": "NY.GDP.MKTP.CD",  # current US$
    "population": "SP.POP.TOTL",
}


@dataclass
class CountryMetric:
    iso2: str
    name: str
    value: float | None
    year: int | None
    walked_back: bool
    currency: str | None


@dataclass
class RegionTotal:
    continent: str
    metric: Metric
    requested_year: int
    currency: str
    fx_date: str
    total_native_usd: float  # sum before FX conversion (World Bank figures are USD)
    total_converted: float
    included: list[CountryMetric]
    excluded: list[str]  # iso2/name + reason
    issues: list[str]


def get_country_metric(
    country: ReconciledCountry, metric: Metric, year: int
) -> CountryMetric:
    indicator = INDICATOR_CODES[metric]
    obs = clients.fetch_wb_indicator(country.iso3, indicator, year=year)
    return CountryMetric(
        iso2=country.iso2,
        name=country.graphql_name,
        value=obs["value"],
        year=obs["year"],
        walked_back=obs["walked_back"],
        currency=country.currency,
    )


def region_total(
    recon: ReconciliationResult,
    metric: Metric,
    year: int,
    currency: str = "USD",
    fx_date: str | None = None,
    include_iso2: list[str] | None = None,
) -> RegionTotal:
    """
    Sums `metric` across the reconciled continent for `year`, converts the
    total from USD (World Bank's native unit) into `currency` using the
    Frankfurter rate for `fx_date` (or latest if None).

    `include_iso2`: if given, restricts the sum to exactly these ISO2 codes
    (caller-controlled scope override -- e.g. to reproduce a World-Bank-region
    definition of "Europe" instead of the GraphQL continent definition, or to
    exclude a specific country). Defaults to the full reconciled set.
    """
    countries = recon.countries
    if include_iso2 is not None:
        wanted = set(c.upper() for c in include_iso2)
        countries = [c for c in countries if c.iso2 in wanted]

    included: list[CountryMetric] = []
    excluded: list[str] = []
    issues: list[str] = []
    total_usd = 0.0

    for c in countries:
        cm = get_country_metric(c, metric, year)
        if cm.value is None:
            excluded.append(f"{c.graphql_name} ({c.iso2}): no {metric} data found")
            continue
        if cm.walked_back:
            issues.append(
                f"{c.graphql_name} ({c.iso2}): {year} value was null, "
                f"used most recent available year ({cm.year}) instead"
            )
        included.append(cm)
        total_usd += cm.value

    if currency.upper() == "USD":
        rate, rate_date = 1.0, "n/a (already USD)"
    else:
        fx = clients.fetch_fx_rate("USD", currency.upper(), date=fx_date)
        rate, rate_date = fx["rate"], fx["date"]
        if fx_date and rate_date != fx_date:
            issues.append(
                f"Requested FX date {fx_date} had no rate (weekend/holiday); "
                f"Frankfurter returned the nearest prior rate, dated {rate_date}."
            )

    return RegionTotal(
        continent=recon.continent_name,
        metric=metric,
        requested_year=year,
        currency=currency.upper(),
        fx_date=rate_date,
        total_native_usd=total_usd,
        total_converted=total_usd * rate,
        included=included,
        excluded=excluded,
        issues=recon.issues + issues,
    )


@dataclass
class RankingRow:
    iso2: str
    name: str
    value: float
    year: int
    walked_back: bool


def rank_countries(
    recon: ReconciliationResult,
    metric: Metric,
    year: int,
    top_n: int = 10,
    per_capita: bool = False,
) -> tuple[list[RankingRow], list[str]]:
    """
    Ranks reconciled countries by `metric` (optionally per-capita) for `year`.
    Returns (ranking, excluded_notes). Countries missing GDP, population
    (if per_capita), or both are excluded and named, never scored as 0.
    """
    rows: list[RankingRow] = []
    excluded: list[str] = []

    for c in recon.countries:
        gdp_obs = clients.fetch_wb_indicator(c.iso3, INDICATOR_CODES["gdp"], year=year)
        if metric == "gdp" and not per_capita:
            if gdp_obs["value"] is None:
                excluded.append(f"{c.graphql_name} ({c.iso2}): no GDP data")
                continue
            rows.append(
                RankingRow(
                    c.iso2, c.graphql_name, gdp_obs["value"], gdp_obs["year"],
                    gdp_obs["walked_back"],
                )
            )
            continue

        pop_obs = clients.fetch_wb_indicator(
            c.iso3, INDICATOR_CODES["population"], year=year
        )
        if per_capita:
            if gdp_obs["value"] is None or pop_obs["value"] is None or not pop_obs["value"]:
                excluded.append(
                    f"{c.graphql_name} ({c.iso2}): missing GDP and/or population "
                    f"data needed for per-capita calculation"
                )
                continue
            value = gdp_obs["value"] / pop_obs["value"]
            yr = max(gdp_obs["year"], pop_obs["year"])
            wb = gdp_obs["walked_back"] or pop_obs["walked_back"]
            rows.append(RankingRow(c.iso2, c.graphql_name, value, yr, wb))
        else:  # metric == "population"
            if pop_obs["value"] is None:
                excluded.append(f"{c.graphql_name} ({c.iso2}): no population data")
                continue
            rows.append(
                RankingRow(
                    c.iso2, c.graphql_name, pop_obs["value"], pop_obs["year"],
                    pop_obs["walked_back"],
                )
            )

    rows.sort(key=lambda r: r.value, reverse=True)
    return rows[:top_n], excluded
