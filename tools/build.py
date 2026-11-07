"""One Finnish case-law repo from the fetched finlex.fi records, straight into git fast-import.

  python build.py <group> <fetched dir> <out repo dir> --today YYYY-MM-DD --retrieved YYYY-MM-DD

group = the finlex.fi court path segment (korkein-oikeus, korkein-hallinto-oikeus, hovioikeudet, hallinto-oikeudet,
markkinaoikeus, tyotuomioistuin, vakuutusoikeus). One commit per decision (Kind: court), dated the day given (noon
Helsinki); then a Lexifina documentation commit with README (the credit CC BY 4.0 asks for: Finlex, Ministry of Justice of
Finland), LICENSE, REPRODUCE.md, corpus.json, decision-index.tsv, unresolved.csv and the judgment overlay
judgments/finland/national/acts/<file>.jsonl, each row pinned to the section's own version in finland-law."""
from __future__ import annotations

import argparse
import glob
import gzip
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import regex

HERE = Path(__file__).resolve().parent
_r = importlib.util.spec_from_file_location('nl_reconcile', HERE.parent / 'nl-caselaw' / 'reconcile.py')
_rec = importlib.util.module_from_spec(_r)
_r.loader.exec_module(_rec)
fix_row, versions = _rec.fix_row, _rec.versions

LAW_CORPUS = 'finland/national/acts'
HEL = timezone(timedelta(hours=2))
COMMITTER = b'Lexifina <corpus@lexifina.com>'
EPOCH_NOON = 10 * 3600
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
NAMES = {'korkein-oikeus': ('Korkein oikeus', 'the Supreme Court (Korkein oikeus)'),
         'korkein-hallinto-oikeus': ('Korkein hallinto-oikeus', 'the Supreme Administrative Court (Korkein hallinto-oikeus)'),
         'hovioikeudet': ('Hovioikeudet', 'the courts of appeal (hovioikeudet)'),
         'hallinto-oikeudet': ('Hallinto-oikeudet', 'the administrative courts (hallinto-oikeudet)'),
         'markkinaoikeus': ('Markkinaoikeus', 'the Market Court (markkinaoikeus)'),
         'tyotuomioistuin': ('Työtuomioistuin', 'the Labour Court (työtuomioistuin)'),
         'vakuutusoikeus': ('Vakuutusoikeus', 'the Insurance Court (vakuutusoikeus)')}
LICENCE = 'Creative Commons Attribution 4.0 International (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/'


def credit(retrieved: str) -> str:
    return f'Source: Finlex (www.finlex.fi), Ministry of Justice of Finland; {LICENCE}; retrieved {retrieved}'


class Resolver:
    """Section citations in Finnish decision text: "TSL 7 luku 3 §", "PL 21 §", "tuotevastuulain 5 §:n",
    "työsopimuslain 7 luvun 3 §". Tried only at each "§", on the 90 characters before it (bounded)."""

    def __init__(self, idx: dict):
        self.arts, self.aliases, self.by_title = idx['arts'], idx['aliases'], idx['by_title']
        al = '|'.join(regex.escape(a) for a in sorted(self.aliases, key=len, reverse=True))
        self.rx_abbr = regex.compile(r'(?:^|[^\w])(' + al + r')\s+(\d+[a-z]?)(?:\s+(?:luku|luvun|kap\.?)\s+(\d+[a-z]?))?\s*$')
        self.rx_gen = regex.compile(r'(?:^|[^\w])(\w+?)(lain|kaaren|lagen|balkens)\s+(\d+[a-z]?)(?:\s+(?:luvun|luku|kap\.?)\s+(\d+[a-z]?))?\s*$')
        self.timeouts = 0

    def folder_of_title(self, stem: str, ending: str) -> str | None:
        nom = stem.lower() + {'lain': 'laki', 'kaaren': 'kaari'}.get(ending, '')
        return self.by_title.get(nom)

    def path(self, folder, first, second):
        files = self.arts.get(folder, {})
        if second:
            return files.get(f'{first}:{second}')
        return files.get(first)

    def find(self, text: str) -> list[tuple[str, str]]:
        out = []
        for m in re.finditer('§', text or ''):
            win = text[max(0, m.start() - 90):m.start()]
            try:
                a = self.rx_abbr.search(win, timeout=0.05)
                g = None if a else self.rx_gen.search(win, timeout=0.05)
            except TimeoutError:
                self.timeouts += 1
                continue
            if a:
                folder = self.aliases[a.group(1)]
                first, second = (a.group(3), a.group(2)) if a.group(3) else (a.group(2), None)
                # "TSL 7 luku 3 §": group2=7 (chapter) group3=3 (section)
                p = self.path(folder, a.group(2), a.group(3)) if a.group(3) else self.path(folder, a.group(2), None)
                how = win[a.start(1):].strip() + ' §'
            elif g:
                folder = self.folder_of_title(g.group(1), g.group(2))
                if not folder:
                    continue
                p = self.path(folder, g.group(3), g.group(4)) if g.group(4) else self.path(folder, g.group(3), None)
                how = win[g.start(1):].strip() + ' §'
            else:
                continue
            if p:
                out.append((p, re.sub(r'\s+', ' ', how)[:160]))
        return out


def decision_day(r: dict) -> str:
    day = (r['meta'].get('Antopäivä') or r['meta'].get('Avgörandedatum') or '')[:10]
    # A year Finlex mistyped ("1008-02-19" for TT:2008-17) takes the year of the decision's own URL and number.
    if re.match(r'^\d{4}-', day) and day[:4] < '1900' and re.fullmatch(r'(19|20)\d{2}', str(r.get('year') or '')):
        day = f"{r['year']}{day[4:]}"
    return day


def dating(r: dict) -> tuple[str, str, tuple]:
    """(In-force day, Decided value, extra trailers) for one decision.
    - Antopäivä (the decision day) when Finlex gives it.
    - Else the day finlex.fi heads the decision page with (page_date, read from its <title> by fetch.py), unless it is
      the 1 January placeholder Finlex shows for a year-only record (KKO:2025:29 and :31 give no Antopäivä but a day).
    - Else the year only (Tapausvuosi, the year in the precedent's identifier): 6,864 KKO precedents of 1926-2004
      (KKO:1931-II-577 ...). Finlex gives no day for them; the tools read In-force as a day, so they are dated
      31 December of their year, the last day of the year they were given (never before the decision existed), with
      Decided: <year> and an In-force-basis: trailer saying so; Presented: is the day the case was presented
      (Esittelypäivä) where Finlex gives it, the decision being given that day or later."""
    day = decision_day(r)
    if ISO.match(day or ''):
        return day, day, ()
    pd = r.get('page_date') or ''
    if ISO.match(pd) and pd[5:] != '01-01':
        return pd, pd, ('In-force-basis: the day finlex.fi heads the decision page with; its metadata gives no Antopäivä',)
    y = str(r['meta'].get('Tapausvuosi') or r.get('year') or '')
    if not re.fullmatch(r'(19|20)\d{2}', y):
        return '', '', ()
    tr = [f'In-force-basis: year only: Finlex gives the year {y} but not the day (its page shows the placeholder 1.1.{y}); '
          f'dated 31 December {y}, the last day of the year the decision was given']
    if ISO.match(r['meta'].get('Esittelypäivä') or ''):
        tr.append(f"Presented: {r['meta']['Esittelypäivä']} (Esittelypäivä; the decision was given that day or later)")
    return f'{y}-12-31', y, tuple(tr)


def year_note(n: int) -> str:
    """The README paragraph on decisions dated by year only (empty when there are none)."""
    if not n:
        return ''
    return f"""
Of these, {n:,} (the Supreme Court's precedents of 1926-2004 such as KKO:1931-II-577) have a year but no day on
Finlex: its page shows the placeholder 1.1.<year>. Each is dated 31 December of its year, the last day of the year it
was given, so the tools never show it before it existed: `In-force: <year>-12-31`, `Decided: <year>` and an
`In-force-basis:` trailer saying so (`in_force` and `in_force_basis` in the file's front matter), with `Presented:` (the
Esittelypäivä) where Finlex gives it. Those before 1970 are committed on 1970-01-01, one second apart, as git dates
start there; `In-force:` holds the date. List them with `git log --grep='^In-force-basis: year only' --format='%(trailers:key=Decided,valueonly) %an'`.
"""


def render(r: dict, day: str, group: str, res: Resolver, ver: dict, retrieved: str, extra: tuple = (), decided: str = ''):
    court = NAMES.get(group, (group, group))[0]
    ident = (r.get('title') or '').strip() or f"{r['kind_slug']} {r['year']}/{r['number']}"
    ecli = r['meta'].get('ECLI-tunnus')
    docket = r['meta'].get('Diaarinumero')
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', ident).strip('-')
    path = f"decisions/{r['kind_slug']}/{day[:4]}/{slug}.md"
    cites = res.find(r['text_fi']) + res.find(r.get('text_sv') or '')
    decided = decided or day
    fm = {'identifier': ident, 'ecli': ecli, 'court': court, 'kind': r['kind_slug'], 'docket': docket, 'decided': decided,
          'volume': r['meta'].get('Taltio'), 'keywords': r['meta'].get('Asiasanat'), 'source_url': r['url'],
          'source': 'Finlex (www.finlex.fi), Ministry of Justice of Finland', 'licence': 'CC BY 4.0', 'source_retrieved': retrieved}
    lines = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items() if v not in (None, '')]
    uniq = sorted({p for p, _ in cites})
    if uniq:
        lines.append('cites_law: ' + json.dumps([f'{LAW_CORPUS}/{p}' for p in uniq], ensure_ascii=False))
    lines += ['---', '', f'# {ident}', '', '## Suomeksi', '', r['text_fi'].strip(), '']
    if (r.get('text_sv') or '').strip():
        lines += ['## På svenska', '', r['text_sv'].strip(), '']
    body = '\n'.join(lines).encode('utf-8')
    author = f'{court} {ident}'.replace('<', '').replace('>', '')
    email = re.sub(r'[^a-z0-9]+', '-', ident.lower()).strip('-')[:60] + '@caselaw.invalid'
    kind = 'correction' if any(t.startswith('Corrected:') for t in extra) else 'court'
    if decided != day:  # a year-only decision: what its In-force day stands for, in the file as in the commit
        basis = next((t.split(':', 1)[1].strip() for t in extra if t.startswith('In-force-basis:')), '')
        lines.insert(lines.index('---', 1), f'in_force: "{day}"')
        if basis:
            lines.insert(lines.index('---', 1), f'in_force_basis: {json.dumps(basis, ensure_ascii=False)}')
        body = '\n'.join(lines).encode('utf-8')
    tr = [f'Kind: {kind}', f'In-force: {day}', f'Decided: {decided}', f'Court: {court}', f'Docket: {docket or ident}']
    if ecli:
        tr.append(f'ECLI: {ecli}')
    tr.append(f"Source: {r['url']}")
    seen = set()
    for p, how in cites:
        if p not in seen and len(seen) < 60:
            seen.add(p)
            tr.append(f'Cites: {LAW_CORPUS}/{p} ({how[:120]})')
    tr += [t for t in extra if t and not t.rstrip().endswith(':')]
    msg = (f'{court} {ident}'[:200] + '\n\n' + '\n'.join(tr) + '\n').encode('utf-8')
    counts = {}
    for p, _ in cites:
        counts[p] = counts.get(p, 0) + 1
    rows, done = [], set()
    for p, how in cites:
        if p in done:
            continue
        done.add(p)
        row = {'decided': day, 'name': f'{court} {ident}', 'court': court, 'citation': ecli or ident, 'url': r['url'],
               'how': how, 'mentions': counts[p], 'decision': path}
        rows.append((p, fix_row(row, p, ver)))
    refs = sorted({x for x in (ident, ecli, docket) if x})
    return path, body, author, email, msg, rows, refs


def from_url(r: dict) -> dict:
    """court, kind, year and number from the page URL: /oikeuskaytanto/<court>/<kind>/<year>/<n> for the supreme
    courts, /oikeuskaytanto/<court>/<year>/<n> for the others (their kind is 'paatokset')."""
    parts = r['url'].split('/oikeuskaytanto/', 1)[-1].strip('/').split('/')
    if len(parts) == 4:
        r['court_slug'], r['kind_slug'], r['year'], r['number'] = parts
    elif len(parts) == 3:
        r['court_slug'], r['year'], r['number'] = parts
        r['kind_slug'] = 'paatokset'
    return r


def load(fetched: Path, group: str) -> list[dict]:
    recs = {}
    for f in sorted(glob.glob(str(fetched / '*.jsonl.gz'))):
        try:
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                for line in fh:
                    try:
                        r = from_url(json.loads(line))
                    except ValueError:
                        continue
                    if r.get('court_slug') == group and (r.get('text_fi') or '').strip():
                        recs[r['url']] = r
        except (EOFError, OSError):
            pass
    return list(recs.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('group'), ap.add_argument('fetched'), ap.add_argument('out')
    ap.add_argument('--today', required=True), ap.add_argument('--retrieved', required=True)
    ap.add_argument('--law-repo', default=str(Path.home() / 'Documents' / 'caselaw-law-mirrors' / 'finland-law.git'))
    ap.add_argument('--chunk-bytes', type=int, default=2_000_000_000)
    a = ap.parse_args()
    out = Path(a.out)
    res = Resolver(json.load(open(HERE / 'fi_index.json', encoding='utf-8')))
    ver = versions(Path(a.law_repo), ref='main')
    recs = load(Path(a.fetched), a.group)
    recs.sort(key=lambda r: (dating(r)[0] or '9999', r['kind_slug'], r['year'], r['number'].zfill(6)))
    print(f'{a.group}: {len(recs)} decisions', file=sys.stderr, flush=True)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(out)], check=True)
    fi = subprocess.Popen(['git', '-C', str(out), 'fast-import', '--quiet', '--done', f'--export-marks={out / ".git" / "marks"}'],
                          stdin=subprocess.PIPE)
    w = fi.stdin.write
    overlay, dindex, undated, checkpoints = {}, [], [], []
    mark = raw = clamp = yearonly = 0
    first = None
    used = set()
    for r in recs:
        day, decided, extra = dating(r)
        if not ISO.match(day or '') or day > a.today:
            undated.append(r)
            continue
        first = first or day
        yearonly += decided != day
        path, body, author, email, msg, rows, refs = render(r, day, a.group, res, ver, a.retrieved, extra, decided)
        if path in used:
            path = path[:-3] + '-' + re.sub(r'\W+', '', r['number']) + '.md'
        used.add(path)
        dindex += [x + '\t' + path for x in refs]
        raw += len(body)
        d0 = date.fromisoformat(day)
        if d0.year < 1970:
            clamp += 1
            ts = EPOCH_NOON + clamp - 7200
        else:
            ts = int(datetime(d0.year, d0.month, d0.day, 12, tzinfo=HEL).timestamp())
        mark += 1
        w(b'commit refs/heads/main\nmark :%d\n' % mark)
        w(b'author %s <%s> %d +0200\ncommitter %s %d +0200\n' % (author.encode('utf-8'), email.encode(), ts, COMMITTER, ts))
        w(b'data %d\n%s\n' % (len(msg), msg))
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (path.encode('utf-8'), len(body), body))
        if raw >= (len(checkpoints) + 1) * a.chunk_bytes:
            checkpoints.append(mark)
        for p, row in rows:
            row['decision'] = path
            overlay.setdefault(p, []).append(row)
    cr = credit(a.retrieved)
    court, where = NAMES.get(a.group, (a.group, a.group))
    gl_path = f'finland/{a.group}/caselaw'
    docs = {}
    for p, rows in overlay.items():
        rows.sort(key=lambda x: (x['decided'], x['citation']))
        docs[f'judgments/{LAW_CORPUS}/{p[:-3]}.jsonl'] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in rows)
    for r in undated:
        docs[f"decisions/undated/{re.sub(r'[^A-Za-z0-9._-]+', '-', r['url'].rsplit('/', 3)[-3] + '-' + r['year'] + '-' + r['number'])}.md"] = \
            f"---\nsource_url: {json.dumps(r['url'])}\n---\n\n# {r.get('title') or r['url']}\n\n{r['text_fi']}\n"
    n_refs = sum(len(v) for v in overlay.values())
    docs['decision-index.tsv'] = 'reference' + chr(9) + 'path' + chr(10) + ''.join(x + chr(10) for x in sorted(set(dindex)))
    docs['unresolved.csv'] = 'note\nCitations are read from the text; a citation of an act finland-law does not hold (for example the Criminal Code and the Code of Judicial Procedure, which it leaves out) is not linked.\n'
    docs['corpus.json'] = json.dumps({
        'corpus': f'fi/{a.group}/caselaw', 'path': gl_path, 'name': f'Case law: Finland, {where}', 'label': f'Finland case law ({court})',
        'language': 'fi', 'kind': 'judgments', 'history': 'in-force',
        'coverage': f'{mark} decisions published on finlex.fi, one file and one commit each (Finnish text, and Swedish where published), dated the day given; {n_refs} citations of law linked to finland-law.',
        'history_from': first, 'record_to': a.retrieved, 'checked_on': a.today,
        'record_note': 'Court decisions, not laws: every commit is Kind: court. In-force is the day the decision was given'
                       + (f'; for {yearonly} decisions Finlex gives the year only, and In-force is 31 December of that year '
                          '(In-force-basis: year only ..., Decided: <year>).' if yearonly else '.'),
        'in_force_trailer': 'In-force', 'correction_trailer': 'Corrected', 'dates': {'commit': 'decided', 'time': '12:00 Europe/Helsinki'},
        'source': {'publisher': 'Finlex, Ministry of Justice of Finland', 'dataset': 'finlex.fi case law (oikeuskäytäntö)',
                   'url': 'https://www.finlex.fi/fi/oikeuskaytanto', 'updated': a.retrieved},
        'licence': 'Creative Commons Attribution 4.0 International (CC BY 4.0)', 'credit': cr,
        'overlay': {'path': f'judgments/{LAW_CORPUS}/<file>.jsonl', 'law_corpus': LAW_CORPUS},
    }, ensure_ascii=False, indent=2) + '\n'
    docs['README.md'] = f"""# Finnish case law: {court}

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the case law of {where}, Finland, which global-law reads at `{gl_path}/`.

Every decision of {where} published on finlex.fi, as a git history: one file per decision, one commit per decision,
dated the day it was given. {mark:,} decisions from {first} to {a.retrieved}.
{year_note(yearonly)}
## Source and licence

{cr}.

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
"""
    docs['LICENSE'] = f'Decisions: {LICENCE}\n{cr}.\n\nThe arrangement of this repository (layout, commits, overlay, tools): CC0 1.0.\n'
    docs['REPRODUCE.md'] = f"""# Reproducing this repository

Input: the decision pages of finlex.fi listed in its sitemaps (court types under /fi/oikeuskaytanto/{a.group}/), fetched on
{a.retrieved} by `tools/fetch.py` (at most 4 requests a second) and read by `tools/flight.py`; finland-law `main` for
the overlay (`tools/fi_index.py`).

```
python tools/fetch.py <fetched>
python tools/build.py {a.group} <fetched> <empty folder> --today {a.today} --retrieved {a.retrieved}
```
"""
    for f in ('fetch.py', 'flight.py', 'build.py', 'fi_index.py'):
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    ts = int(datetime.fromisoformat(a.today + 'T12:00:00+02:00').timestamp())
    msg = f'Documentation: README, LICENSE (CC BY 4.0 credit), REPRODUCE, corpus.json and the judgment overlay ({n_refs} citations)\n'.encode()
    w(b'commit refs/heads/main\nauthor Lexifina <corpus@lexifina.com> %d +0200\ncommitter %s %d +0200\n' % (ts, COMMITTER, ts))
    w(b'data %d\n%s\n' % (len(msg), msg))
    for p in sorted(docs):
        b = docs[p].encode('utf-8')
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (p.encode('utf-8'), len(b), b))
    w(b'done\n')
    fi.stdin.close()
    if fi.wait():
        raise SystemExit('fast-import failed')
    print(json.dumps({'group': a.group, 'decisions': mark, 'year_only': yearonly, 'undated': len(undated), 'overlay_rows': n_refs,
                      'regex_timeouts': res.timeouts, 'raw_gb': round(raw / 1e9, 3)}), flush=True)


if __name__ == '__main__':
    main()
