"""Add the judgment-overlay rows a Finnish case-law repo is missing for the Constitution cited in the genitive
("perustuslain 21 §", "Suomen perustuslain 6 §:n"): the first builds' index (fi_index.json) had no title "perustuslaki"
for it (finland-law's title is "Suomen perustuslaki"), so only "PL 21 §" was linked. One append pass over every decision
in the repo: each citation of perustuslaki the current index finds and the overlay lacks (decision, section) becomes a row,
built as build.render builds rows and pinned with fix_row to the section's own version on the decision day. One
Lexifina documentation commit on top of origin/main, pushed fast-forward only; nothing is rewritten.

  python -X utf8 constitution_rows.py <repo> <group> [--no-push]"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location('fi_build', HERE / 'build.py')
B = importlib.util.module_from_spec(_s)
_s.loader.exec_module(B)
CACHE = Path.home() / 'Documents' / '.caselaw-cache' / 'fi'
LAW = Path.home() / 'Documents' / 'caselaw-law-mirrors' / 'finland-law.git'
ACT = 'perustuslaki/'
TOOLS = ('fetch.py', 'flight.py', 'build.py', 'fi_index.py', 'backfill_year_only.py', 'constitution_rows.py')


def git(repo, *args, inp=None, check=True):
    r = subprocess.run(['git', '-C', str(repo), *args], input=inp, capture_output=True)
    if check and r.returncode:
        raise SystemExit(f'git {args[0]}: {r.stderr.decode(errors="replace")[-400:]}')
    return r.stdout.decode('utf-8', errors='replace')


def blobs(repo: Path, ref: str, paths: list[str]) -> dict[str, str]:
    """path -> text, read with one cat-file --batch."""
    p = subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    import threading
    threading.Thread(target=lambda: (p.stdin.write(''.join(f'{ref}:{x}\n' for x in paths).encode('utf-8')), p.stdin.close()),
                     daemon=True).start()
    out = {}
    for x in paths:
        head = p.stdout.readline().split()
        if len(head) < 3:
            continue
        data = p.stdout.read(int(head[2]))
        p.stdout.read(1)
        out[x] = data.decode('utf-8', 'replace')
    p.wait()
    return out


def front(text: str) -> dict:
    fm = {}
    if text.startswith('---\n'):
        for line in text[4:text.find('\n---', 4)].splitlines():
            k, _, v = line.partition(': ')
            try:
                fm[k] = json.loads(v)
            except ValueError:
                fm[k] = v
    return fm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo'), ap.add_argument('group'), ap.add_argument('--no-push', action='store_true')
    a = ap.parse_args()
    repo = Path(a.repo)
    git(repo, 'fetch', '-q', 'origin', 'main')
    base = git(repo, 'rev-parse', 'origin/main').strip()
    names = git(repo, 'ls-tree', '-r', '--name-only', base).splitlines()
    decs = [n for n in names if n.startswith('decisions/') and not n.startswith('decisions/undated/') and n.endswith('.md')]
    by_url = {}
    for path, text in blobs(repo, base, decs).items():
        fm = front(text)
        if fm.get('source_url'):
            by_url[fm['source_url']] = (path, fm)
    prefix = f'judgments/{B.LAW_CORPUS}/{ACT}'
    ov_files = [n for n in names if n.startswith(prefix) and n.endswith('.jsonl')]
    old = {n: [json.loads(l) for l in t.splitlines() if l.strip()] for n, t in blobs(repo, base, ov_files).items()}
    have = {(row['decision'], n) for n, rows in old.items() for row in rows}
    ver = B.versions(LAW, ref='main')
    res = B.Resolver(json.load(open(HERE / 'fi_index.json', encoding='utf-8')))
    add: dict[str, list] = {}
    seen_dec = 0
    for r in B.load(CACHE, a.group):
        hit = by_url.get(r['url'])
        if not hit:
            continue
        seen_dec += 1
        path, fm = hit
        cites = [(p, how) for p, how in res.find(r['text_fi']) + res.find(r.get('text_sv') or '') if p.startswith(ACT)]
        counts: dict[str, int] = {}
        for p, _ in cites:
            counts[p] = counts.get(p, 0) + 1
        day = fm.get('in_force') or fm.get('decided')
        if not B.ISO.match(str(day or '')):
            continue
        done = set()
        for p, how in cites:
            rel = f'judgments/{B.LAW_CORPUS}/{p[:-3]}.jsonl'
            if p in done or (path, rel) in have:
                continue
            done.add(p)
            court = fm.get('court') or ''
            ident = fm.get('identifier') or ''
            row = {'decided': day, 'name': f'{court} {ident}', 'court': court, 'citation': fm.get('ecli') or ident,
                   'url': r['url'], 'how': how, 'mentions': counts[p], 'decision': path}
            if fm.get('in_force') and fm.get('decided') != day:
                row['decided_basis'] = f"year only ({fm.get('decided')}); dated 31 December"
            add.setdefault(rel, []).append(B.fix_row(row, p, ver))
    n_add = sum(len(v) for v in add.values())
    stats = {'group': a.group, 'decisions_read': seen_dec, 'rows_added': n_add, 'sections': len(add),
             'pinned': sum(1 for v in add.values() for x in v if x.get('law_commit')),
             'record_from_only': sum(1 for v in add.values() for x in v if x.get('law_record_from'))}
    print(json.dumps(stats))
    if not n_add:
        return
    docs = {}
    for rel, rows in add.items():
        allr = old.get(rel, []) + rows
        allr.sort(key=lambda x: (x['decided'], x['citation']))
        docs[rel] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in allr)
    others = [n for n in names if n.startswith('judgments/') and n.endswith('.jsonl') and n not in docs]
    total = sum(len([l for l in t.splitlines() if l.strip()]) for t in blobs(repo, base, others).values()) + \
        sum(len(v.splitlines()) for v in docs.values())
    cj = json.loads(git(repo, 'show', f'{base}:corpus.json'))
    cj['coverage'] = re.sub(r'; \d+ citations of law', f'; {total} citations of law', cj['coverage'])
    docs['corpus.json'] = json.dumps(cj, ensure_ascii=False, indent=2) + '\n'
    rep = git(repo, 'show', f'{base}:REPRODUCE.md')
    if 'constitution_rows.py' not in rep:
        docs['REPRODUCE.md'] = rep + """
Overlay rows for the Constitution cited in the genitive ("perustuslain 21 §") were appended by
`tools/constitution_rows.py` for decisions built before `tools/fi_index.py` knew that form; later builds read it themselves.

```
python tools/constitution_rows.py <this repo> <court path segment>
```
"""
    for f in TOOLS:
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    now = int(time.time())
    msg = (f'Documentation: {n_add} overlay rows for the Constitution cited in the genitive ("perustuslain N §"), '
           f'{len(add)} sections; each pinned to the section\'s own version on the decision day\n').encode()
    w = [b'commit refs/heads/fi-constitution\nauthor Lexifina <corpus@lexifina.com> %d +0000\ncommitter %s %d +0000\n' % (now, B.COMMITTER, now),
         b'data %d\n%s\n' % (len(msg), msg), b'from %s\n' % base.encode()]
    for rel in sorted(docs):
        b = docs[rel].encode('utf-8')
        w.append(b'M 100644 inline %s\ndata %d\n%s\n' % (rel.encode('utf-8'), len(b), b))
    w.append(b'done\n')
    git(repo, 'fast-import', '--quiet', '--done', '--force', inp=b''.join(w))
    new = git(repo, 'rev-parse', 'refs/heads/fi-constitution').strip()
    print('commit', new, 'overlay rows now', total)
    if a.no_push:
        return
    r = subprocess.run(['git', '-C', str(repo), 'push', '-q', 'origin', 'refs/heads/fi-constitution:refs/heads/main'], capture_output=True)
    if r.returncode:
        raise SystemExit('push refused (someone pushed first?): ' + r.stderr.decode(errors='replace')[-300:])
    git(repo, 'fetch', '-q', 'origin', 'main')
    git(repo, 'update-ref', 'refs/heads/main', new, base, check=False)
    git(repo, 'branch', '-q', '-D', 'fi-constitution', check=False)
    print('pushed', new)


if __name__ == '__main__':
    main()
