# Sample Output

Live run against the three real services (Countries GraphQL, World Bank
Indicators API, Frankfurter FX), via Claude Desktop connected to this MCP
server. Independently spot-checked GDP figures below against World-Bank-
sourced data (FRED, macrotrends, tradingeconomics) and they match exactly.

---

## 1. Reconciliation — Europe

Prompt: *"Using country-analytics, reconcile Europe and tell me how many
countries and what issues you found."*

Reconciled Europe against both sources (GraphQL continent membership joined
with World Bank metadata on ISO2). Result: **48 countries**, with **10
flagged issues**:

**Missing World Bank data (5)** — in the GraphQL continent list but no
matching World Bank row, so GDP/population show as missing rather than zero:
- Åland (AX), Guernsey (GG), Jersey (JE), Svalbard and Jan Mayen (SJ),
  Vatican City (VA)

**Name mismatches, cosmetic only (3)** — joined correctly on ISO2 despite
different names across sources:
- Czech Republic vs. Czechia (CZ)
- Slovakia vs. Slovak Republic (SK)
- Russia vs. Russian Federation (RU)

**Currency ambiguity (1)** — Switzerland (CH) reports multiple currency
codes (CHE, CHF, CHW); the tool resolved to **CHF** (the actual Swiss Franc)
because it's the only one of the three that Frankfurter publishes tradeable
rates for — CHE and CHW are niche WIR Bank interbank settlement codes, not
general-use currency. Flagged rather than guessed.

**Excluded aggregates (1 issue, 78 rows)** — World Bank aggregate rows like
"World," "Euro area," and income groupings were excluded since they aren't
actual countries.

Note: membership is based on the GraphQL geographic partition, not World
Bank's "Europe & Central Asia" region classification, so this list may
differ from other regional definitions.

---

## 2. Single-country metric — France GDP 2022

Prompt: *"What was France's GDP in 2022 in US dollars?"*

> France's GDP in 2022 was approximately $2.79 trillion (current US$) —
> specifically **$2,794,788,137,066.94**.

Independently verified: matches the World-Bank-sourced FRED series
(MKTGDPFRA646NWDB) exactly — `2022: 2,794,788,137,066.9400`.

---

## 3. Ranking — Top 10 European countries by GDP per capita, 2022

Prompt: *"Using country-analytics, rank the top 10 European countries by
GDP per capita for 2022."*

| Rank | Country | GDP per capita |
|---|---|---|
| 1 | Monaco | $226,052 |
| 2 | Liechtenstein | $188,055 |
| 3 | Luxembourg | $123,720 |
| 4 | Norway | $113,122 |
| 5 | Ireland | $105,191 |
| 6 | Switzerland | $97,809 |
| 7 | Isle of Man | $88,329 |
| 8 | Iceland | $76,377 |
| 9 | Denmark | $67,781 |
| 10 | Faroe Islands | $67,069 |

Gibraltar was excluded from this ranking due to missing GDP/population data
needed for the per-capita calculation — flagged rather than silently
dropped or zero-filled.

Independently verified: Monaco's $226,052 matches macrotrends' World-Bank-
sourced figure exactly. Liechtenstein's $188,055 matches FRED's World-Bank-
sourced series (PCAGDPLIA646NWDB: `2022: 188,055.00324`) exactly.

---

## Performance note

The first version of `rank_countries`/`region_total` issued one World Bank
API call per country **sequentially** — for per-capita ranking across
Europe's 48 countries, that's up to 96 back-to-back network round-trips,
which took 1-3+ minutes in practice. Fixed by fetching all per-country
indicator calls concurrently through a bounded thread pool (10 concurrent
requests — bounded to stay a reasonable load on a free public API, not
unlimited). The ranking above came back in well under 30 seconds after the
fix, confirmed live.