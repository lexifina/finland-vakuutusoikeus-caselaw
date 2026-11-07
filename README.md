# Finnish case law: Vakuutusoikeus

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the case law of the Insurance Court (vakuutusoikeus), Finland, which global-law reads at `finland/vakuutusoikeus/caselaw/`.

Every decision of the Insurance Court (vakuutusoikeus) published on finlex.fi, as a git history: one file per decision, one commit per decision,
dated the day it was given. 1,740 decisions from 1957-11-26 to 2026-11-07.

## Source and licence

Source: Finlex (www.finlex.fi), Ministry of Justice of Finland; Creative Commons Attribution 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/; retrieved 2026-11-07.

The decisions are published by Finlex, the Ministry of Justice of Finland's public legal information service, under the
Creative Commons Attribution 4.0 International licence (https://creativecommons.org/licenses/by/4.0/). Changes made here:
each decision page converted to Markdown (Finnish text, and the Swedish text where published), dated by its decision day;
links to the law added in `judgments/`. The material may not be used in ways that infringe or endanger the protection of
personal data (Finlex's own condition).

## Questions as git commands

| Question | Command |
|---|---|
| A decision | `cat decisions/<kind>/<year>/<identifier>.md` |
| By identifier, ECLI or diary number | `grep -i 'KKO:2023:8' decision-index.tsv` |
| Decisions given in a window | `git log --since=2024-03-01 --until=2024-03-31 --format='%ad %an' --date=short` |
| Decisions applying TSL 7 luku 3 § | `cat judgments/finland/national/acts/tyosopimuslaki/luku-07-pykala-003.jsonl` |
| Who wrote a line? | `git blame decisions/...`: each line is credited to the decision (court and identifier) |

## Dates and authors
The commit date is the day the decision was given, at noon Helsinki (`In-force:` and `Decided:`). The author is the court
and the decision's identifier, at an address that cannot exist. These are court rulings, not laws: every commit is
`Kind: court`.
