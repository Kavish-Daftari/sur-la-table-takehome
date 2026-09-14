# Write-up: Cross-Source Country Analytics

## What this does, in plain terms

I pulled data from three free public sources — a country directory, the
World Bank's economic database, and a currency-exchange service — and
stitched them into one clean table per continent, so questions like "what's
Europe's total GDP in euros?" can be answered on demand, with the reasoning
behind the number shown, not just the number itself.

## The three sources and what I joined them on

- **Countries GraphQL** tells me which countries belong to which continent,
  and each country's currency.
- **World Bank** has the actual GDP and population figures.
- **Frankfurter** converts between currencies, including on a specific past date.

I joined the first two **on each country's 2-letter ISO code** (e.g. `FR` for
France), not on country name. Names disagree between sources — one calls a
country "Czechia," the other "Czech Republic"; one says "Russia," the other
"Russian Federation" — and trying to match on name would silently misjoin or
drop countries. Codes don't have that problem.

## Real disagreements I found, and how I handled each

- **World Bank's country list includes non-countries** — "World," "Euro
  area," and income-group buckets like "Low income" are mixed in with real
  countries. I filter these out explicitly and report how many were removed,
  rather than letting them pollute a sum.
- **A country can appear in one source and not the other.** When that
  happens, I flag it by name and exclude it from that specific calculation —
  I never fill in a zero, which would silently understate a total.
- **"Europe" isn't one agreed-upon list.** The country directory's
  geographic continent and the World Bank's own "Europe & Central Asia"
  region are different sets (the World Bank one reaches into Central Asia).
  I use the geographic continent as the default definition, but every tool
  lets the caller override which countries count — this is a judgment call,
  not a fact, and I've made it visible and changeable rather than baking in
  one silent answer.
- **Some currency fields are messy** (a country listing two currency codes).
  I flag these and pick the first one deterministically, rather than guessing
  which was intended.
- **The most recent 1–2 years of GDP/population are often not yet published**
  (shows as blank). Rather than treating a blank as zero or failing outright,
  I walk back to the most recent year that does have data and say so plainly
  — "you asked for 2024, here's 2023, because 2024 isn't published yet."

## Every assumption, listed

- **Continent membership**: geographic continent list, not the World Bank's
  economic region — overridable per query.
- **Currency**: for multi-currency countries, the first listed code is used.
- **Missing year**: walk back to the most recent year with real data; always
  labeled when this happens.
- **FX rate**: defaults to the latest rate; a specific historical date can be
  pinned instead (this matters — "Europe's GDP in euros using the Dec 2022
  rate" vs. "using today's rate" are legitimately different answers).
- **Currency of GDP figures**: World Bank reports GDP in US dollars natively;
  all conversions start from that USD figure.

## Production concerns

- **Rate limits / reliability**: all three services are free and can be slow
  or briefly unavailable. Calls retry a few times with a short delay before
  giving up, and a failure is reported clearly (which source, what failed)
  rather than silently returning a wrong number.
- **Caching**: the country/currency directory rarely changes, so it's cached
  for 15 minutes per continent to avoid re-pulling ~300 country records on
  every question; a fresh pull can be forced if needed.
- **Pagination**: the World Bank's country list spans multiple pages; all
  pages are collected before anything is used.
- **Monitoring on a schedule**: I'd track how often each source fails or
  times out, how often a "missing data" flag fires (a sudden jump could mean
  a source changed its format), and alert if a nightly run's totals swing
  more than a small percentage from the prior run without an explained cause.

## Pointing this at real internal systems instead

The join-on-a-stable-code pattern, the "flag it, don't guess it" rule for
messy or missing data, and the "every judgment call is a parameter, not a
hardcoded choice" design all carry over directly to messier internal data — a
product catalog, a CRM, an ad platform. What would change is the specifics:
internal systems usually need authentication and have their own rate limits
and outage patterns to design around, and the "which record in system A
matches which record in system B" problem is usually harder than matching on
an ISO code — often there's no single clean key, and part of the job would be
finding or building one (or falling back to fuzzy matching with a
human-reviewable flag for anything below a confidence threshold).
