"""
Manual alias table for reconciling country names across sources.

Why this exists: the GraphQL "countries" API and the World Bank API both key
countries primarily by name in places, and the World Bank's official short
names differ from the ISO/common names GraphQL uses. We join on ISO2 code
wherever possible (see reconcile.py) -- this table is a *fallback/documentation*
of the known name divergences, and is also used to explain flagged mismatches
in plain English.

Format: ISO2 code -> (graphql_name_seen, world_bank_name_seen)
This is seeded with the mismatches named explicitly in the assignment brief
plus other well-known ISO2/World-Bank naming divergences for Europe. It is not
exhaustive; anything not listed here that still fails to join is surfaced by
list_reconciliation_issues() rather than silently dropped or silently guessed.
"""

KNOWN_NAME_DIVERGENCES = {
    "CZ": ("Czechia", "Czech Republic"),
    "RU": ("Russia", "Russian Federation"),
    "SK": ("Slovakia", "Slovak Republic"),
    "GB": ("United Kingdom", "United Kingdom"),
    "MK": ("Macedonia", "North Macedonia"),
    "MD": ("Moldova", "Moldova"),
    "VA": ("Vatican City", "Vatican City"),
    "XK": ("Kosovo", "Kosovo"),
}

