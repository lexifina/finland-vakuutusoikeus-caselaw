# Reproducing this repository

Input: the decision pages of finlex.fi listed in its sitemaps (court types under /fi/oikeuskaytanto/vakuutusoikeus/), fetched on
2026-11-07 by `tools/fetch.py` (at most 4 requests a second) and read by `tools/flight.py`; finland-law `main` for
the overlay (`tools/fi_index.py`).

```
python tools/fetch.py <fetched>
python tools/build.py vakuutusoikeus <fetched> <empty folder> --today 2026-11-07 --retrieved 2026-11-07
```

Overlay rows for the Constitution cited in the genitive ("perustuslain 21 §") were appended by
`tools/constitution_rows.py` for decisions built before `tools/fi_index.py` knew that form; later builds read it themselves.

```
python tools/constitution_rows.py <this repo> <court path segment>
```
