"""Append the Supreme Court precedents the first build left undated (decisions/undated/, inside its documentation commit)
as decisions: one Kind: court commit each, written as build.py writes them and dated as build.dating dates them (the
year only for 6,864 precedents of 1926-2004: In-force 31 December of the year, Decided: <year>, In-force-basis:), then
a documentation commit with their overlay rows, decision-index entries, README, REPRODUCE and corpus.json, then a second
documentation commit removing decisions/undated/ (each of its files is now a decision file). Append-only: the commits
are made on top of origin/main and pushed with a normal (fast-forward) push; nothing is rewritten.

  python -X utf8 backfill_year_only.py <repo> [--page-dates page_dates.json] [--no-push]

page_dates.json: {url: YYYY-MM-DD}, the day finlex.fi heads each page with (fetch.py now records it as page_date)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('fi_build', HERE / 'build.py')
B = importlib.util.module_from_spec(_s)
_s.loader.exec_module(B)
CACHE = Path.home() / 'Documents' / '.caselaw-cache' / 'fi'
LAW = Path.home() / 'Documents' / 'caselaw-law-mirrors' / 'finland-law.git'
GROUP = 'korkein-oikeus'
TOOLS = ('fetch.py', 'flight.py', 'build.py', 'fi_index.py', 'backfill_year_only.py', 'constitution_rows.py')


def git(repo, *args, inp=None, check=True):
    r = subprocess.run(['git', '-C', str(repo), *args], input=inp, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f'git {args[0]}: {r.stderr.decode(errors="replace")[-400:]}')
    return r.stdout.decode('utf-8', errors='replace')


def undated_name(r: dict) -> str:
    """The decisions/undated/ file the first build wrote for a record (build.py, before dating)."""
    return 'decisions/undated/' + re.sub(r'[^A-Za-z0-9._-]+', '-', r['url'].rsplit('/', 3)[-3] + '-' + r['year'] + '-' + r['number']) + '.md'


def blob(rel: str, data: bytes) -> bytes:
    return b'M 100644 inline %s\ndata %d\n%s\n' % (rel.encode('utf-8'), len(data), data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo'), ap.add_argument('--page-dates', default=''), ap.add_argument('--no-push', action='store_true')
    a = ap.parse_args()
    repo = Path(a.repo)
    git(repo, 'fetch', '-q', 'origin', 'main')
    base = git(repo, 'rev-parse', 'origin/main').strip()
    tree = set(git(repo, 'ls-tree', '-r', '--name-only', base).splitlines())
    undated_files = {p for p in tree if p.startswith('decisions/undated/')}
    index_txt = git(repo, 'show', f'{base}:decision-index.tsv')
    known = {l.split('\t', 1)[0] for l in index_txt.splitlines()[1:] if '\t' in l}
    git(LAW, 'fetch', '-q', 'origin', 'main:main', check=False)
    ver = B.versions(LAW, ref='main')
    res = B.Resolver(json.load(open(HERE / 'fi_index.json', encoding='utf-8')))
    pdays = json.load(open(a.page_dates, encoding='utf-8')) if a.page_dates else {}
    today = date.today().isoformat()
    todo = []
    for r in B.load(CACHE, GROUP):
        if B.ISO.match(B.decision_day(r) or ''):
            continue  # dated: the first build committed it
        ident = (r.get('title') or '').strip() or f"{r['kind_slug']} {r['year']}/{r['number']}"
        if ident in known:
            continue  # appended already
        if r['url'] in pdays:
            r['page_date'] = pdays[r['url']]
        day, decided, extra = B.dating(r)
        if not B.ISO.match(day or '') or day > today:
            print('still undated:', ident)
            continue
        todo.append((day, decided, extra, r))
    todo.sort(key=lambda x: (x[0], x[3]['kind_slug'], x[3]['year'], x[3]['number'].zfill(6)))
    if not todo:
        print('nothing to append')
        return
    covered = {undated_name(r) for _, _, _, r in todo}
    missing = undated_files - covered
    now = int(time.time())
    w, overlay, dindex, used, clamp, firstday = [], {}, [], set(tree), 0, None
    year_only = 0
    for day, decided, extra, r in todo:
        path, body, author, email, msg, rows, refs = B.render(r, day, GROUP, res, ver, today, extra, decided)
        if path in used:
            path = path[:-3] + '-' + re.sub(r'\W+', '', r['number']) + '.md'
        used.add(path)
        year_only += decided != day
        firstday = min(firstday or day, day)
        d0 = date.fromisoformat(day)
        if d0.year < 1970:  # as build.py: 1970-01-01, one second apart, In-force holding the date
            clamp += 1
            ts = B.EPOCH_NOON + clamp - 7200
        else:
            ts = int(datetime(d0.year, d0.month, d0.day, 12, tzinfo=B.HEL).timestamp())
        w.append(b'commit refs/heads/fi-backfill\nauthor %s <%s> %d +0200\ncommitter %s %d +0000\n'
                 % (author.encode('utf-8'), email.encode(), ts, B.COMMITTER, now))
        w.append(b'data %d\n%s\n' % (len(msg), msg))
        if len(w) == 2:
            w.append(b'from %s\n' % base.encode())
        w.append(blob(path, body))
        dindex += [x + '\t' + path for x in refs]
        for p, row in rows:
            row['decision'] = path
            if decided != day:
                row['decided_basis'] = f'year only ({decided}); dated 31 December'
            overlay.setdefault(p, []).append(row)
    n_rows = sum(len(v) for v in overlay.values())
    docs = {}
    for p, add in sorted(overlay.items()):
        rel = f'judgments/{B.LAW_CORPUS}/{p[:-3]}.jsonl'
        old = git(repo, 'show', f'{base}:{rel}', check=False)
        allr = [json.loads(l) for l in old.splitlines() if l.strip()] + add
        allr.sort(key=lambda x: (x['decided'], x['citation']))
        docs[rel] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in allr)
    rows_ = sorted(set(index_txt.splitlines()[1:] + dindex))
    docs['decision-index.tsv'] = 'reference\tpath\n' + ''.join(x + '\n' for x in rows_)
    # README, REPRODUCE, corpus.json: counts and the year-only note
    readme = git(repo, 'show', f'{base}:README.md')
    m = re.search(r'([\d,]+) decisions from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})\.\n', readme)
    total = int(m.group(1).replace(',', '')) + len(todo)
    first_day = min(firstday, m.group(2))
    readme = readme.replace(m.group(0), f'{total:,} decisions from {first_day} to {m.group(3)}.\n' + B.year_note(year_only), 1)
    docs['README.md'] = readme
    rep = git(repo, 'show', f'{base}:REPRODUCE.md')
    if 'backfill_year_only.py' not in rep:
        rep += f"""
The {len(todo):,} precedents Finlex gives no decision day for were first left undated (decisions/undated/) and were
appended on {today} by `tools/backfill_year_only.py`: one commit each after the build's, dated by `build.dating`
(31 December of the year given, `In-force-basis: year only ...`), then a documentation commit removing
decisions/undated/. A fresh `tools/build.py` run dates them the same way but commits them in date order.

```
python tools/backfill_year_only.py <this repo> --page-dates <url to page day JSON>
```

Overlay rows for the Constitution cited in the genitive ("perustuslain 21 §") were added by `tools/constitution_rows.py`
for decisions built before `tools/fi_index.py` knew that form; later builds read it themselves.
"""
    docs['REPRODUCE.md'] = rep
    cj = json.loads(git(repo, 'show', f'{base}:corpus.json'))
    cj['coverage'] = re.sub(r'^\d+ decisions', f'{total} decisions', cj['coverage'])
    cj['coverage'] = re.sub(r'; \d+ citations of law', '; ' + str(sum(len(v.splitlines()) for k, v in docs.items()
                                                                       if k.startswith('judgments/')) + _other_rows(repo, base, docs)) + ' citations of law', cj['coverage'])
    cj['history_from'] = first_day
    cj['record_note'] = ('Court decisions, not laws: every commit is Kind: court. In-force is the day the decision was given; '
                         f'for {year_only} precedents Finlex gives the year only, and In-force is 31 December of that year '
                         '(In-force-basis: year only ..., Decided: <year>).')
    cj['checked_on'] = today
    docs['corpus.json'] = json.dumps(cj, ensure_ascii=False, indent=2) + '\n'
    for f in TOOLS:
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    msg = (f'Documentation: overlay ({n_rows} citations) and decision index for the {len(todo)} precedents appended '
           f'with their year ({year_only} by year only, dated 31 December); README, REPRODUCE and corpus.json counts\n').encode()
    w.append(b'commit refs/heads/fi-backfill\nauthor Lexifina <corpus@lexifina.com> %d +0000\ncommitter %s %d +0000\n' % (now, B.COMMITTER, now))
    w.append(b'data %d\n%s\n' % (len(msg), msg))
    for rel in sorted(docs):
        w.append(blob(rel, docs[rel].encode('utf-8')))
    if undated_files and not missing:
        msg = (f'Documentation: remove decisions/undated/; each of its {len(undated_files)} precedents is now a decision file '
               'under decisions/ennakkopaatokset/<year>/ (history keeps the old copies)\n').encode()
        w.append(b'commit refs/heads/fi-backfill\nauthor Lexifina <corpus@lexifina.com> %d +0000\ncommitter %s %d +0000\n'
                 % (now + 1, B.COMMITTER, now + 1))
        w.append(b'data %d\n%s\n' % (len(msg), msg))
        w.append(b'D decisions/undated\n')
    elif missing:
        print(len(missing), 'undated files have no decision; decisions/undated/ kept:', sorted(missing)[:5])
    w.append(b'done\n')
    git(repo, 'fast-import', '--quiet', '--done', '--force', inp=b''.join(w))
    new = git(repo, 'rev-parse', 'refs/heads/fi-backfill').strip()
    print(json.dumps({'appended': len(todo), 'year_only': year_only, 'clamped_pre_1970': clamp, 'overlay_rows_added': n_rows,
                      'total': total, 'history_from': first_day, 'undated_removed': len(undated_files) if not missing else 0,
                      'tip': new}))
    if a.no_push:
        return
    r = subprocess.run(['git', '-C', str(repo), 'push', '-q', 'origin', 'refs/heads/fi-backfill:refs/heads/main'], capture_output=True)
    if r.returncode:
        raise SystemExit('push refused (someone pushed first?): ' + r.stderr.decode(errors='replace')[-300:])
    git(repo, 'fetch', '-q', 'origin', 'main')
    git(repo, 'update-ref', 'refs/heads/main', new, base, check=False)  # only if local main is still the base
    git(repo, 'branch', '-q', '-D', 'fi-backfill', check=False)
    print('pushed', new)


def _other_rows(repo, base, docs) -> int:
    """Overlay rows in the files this commit does not rewrite."""
    n = 0
    for rel in git(repo, 'ls-tree', '-r', '--name-only', base, 'judgments/').splitlines():
        if rel not in docs and rel.endswith('.jsonl'):
            n += sum(1 for l in git(repo, 'show', f'{base}:{rel}').splitlines() if l.strip())
    return n



if __name__ == '__main__':
    main()
