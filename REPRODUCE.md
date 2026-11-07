# Reproducing this repository

Input: the decision pages of finlex.fi listed in its sitemaps (court types under /fi/oikeuskaytanto/vakuutusoikeus/), fetched on
2026-11-07 by `tools/fetch.py` (at most 4 requests a second) and read by `tools/flight.py`; finland-law `main` for
the overlay (`tools/fi_index.py`).

```
python tools/fetch.py <fetched>
python tools/build.py vakuutusoikeus <fetched> <empty folder> --today 2026-11-07 --retrieved 2026-11-07
```
