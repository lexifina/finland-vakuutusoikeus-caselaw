"""Fetch every court decision page listed in finlex.fi's sitemaps, politely (at most --rate requests a second; robots.txt
allows it), parse each page as it arrives (flight.py) and keep only the record: one gzip JSONL shard per court type.
Resumable: URLs already in a shard are skipped.

  python fetch.py <out dir> [--rate 8] [--workers 32]"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import flight

UA = 'lexifina-gitlaw (case law archive)'
SITEMAP = 'https://www.finlex.fi/sitemap.xml'
COURT_TYPES = ('supreme-court-precedent', 'supreme-court-other-decision', 'supreme-court-leave-to-appeal',
               'supreme-administrative-court-precedent', 'supreme-administrative-court-decision-summary',
               'supreme-administrative-court-other-decision', 'administrative-court-decision', 'court-of-appeal-decision',
               'market-court-decision', 'labour-court-decision', 'insurance-court-decision')
lock = threading.Lock()
nxt = [0.0]


def get(url: str, rate: float) -> bytes:
    with lock:
        wait = nxt[0] - time.time()
        nxt[0] = max(nxt[0], time.time()) + 1 / rate
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(6):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=90).read()
        except Exception as e:  # transient: back off
            err = e
            time.sleep(4 * (i + 1))
    raise err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'), ap.add_argument('--rate', type=float, default=8), ap.add_argument('--workers', type=int, default=32)
    ap.add_argument('--types', default='', help='only these court types (sitemap names), comma separated')
    a = ap.parse_args()
    global COURT_TYPES
    if a.types:
        COURT_TYPES = tuple(t for t in COURT_TYPES if t in a.types.split(','))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    idx = get(SITEMAP, a.rate).decode('utf-8')
    maps = [u for u in re.findall(r'<loc>([^<]+)</loc>', idx) if any(f'/sitemaps/{t}.xml' in u for t in COURT_TYPES)]
    urls = []
    for m in maps:
        t = re.search(r'/sitemaps/([a-z-]+)\.xml', m).group(1)
        for u in re.findall(r'<loc>([^<]+)</loc>', get(m, a.rate).decode('utf-8')):
            urls.append((t, u))
    (out / 'urls.json').write_text(json.dumps(urls))
    done = set()
    for f in out.glob('*.jsonl.gz'):
        try:  # a shard cut short by a killed run still yields its complete lines
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                for line in fh:
                    try:
                        done.add(json.loads(line)['url'])
                    except ValueError:
                        pass
        except (EOFError, OSError):
            pass
    todo = [(t, u) for t, u in urls if u not in done]
    todo.sort(key=lambda x: COURT_TYPES.index(x[0]))  # precedents first
    print(f'{len(urls)} pages listed, {len(todo)} to fetch', flush=True)
    writers = {t: gzip.open(out / f'{t}.{int(time.time())}.jsonl.gz', 'wt', encoding='utf-8', compresslevel=5) for t in COURT_TYPES}
    wlock = threading.Lock()
    count = [0, 0]

    def one(item):
        t, u = item
        try:
            html = get(u, a.rate).decode('utf-8', 'replace')
            p = flight.Page(html)
            m = re.search(r'/oikeuskaytanto/([^/]+)/([^/]+)/(\d{4})/([^/?#]+)', u)
            rec = {'url': u, 'type': t, 'court_slug': m.group(1) if m else '', 'kind_slug': m.group(2) if m else '',
                   'year': m.group(3) if m else '', 'number': m.group(4) if m else '', 'meta': flight.meta(p.fl),
                   'title': (re.search(r'"h2",null,\{"lang":"fi","children":"([^"]+)"', p.fl) or [None, None])[1],
                   'text_fi': p.text('fi'), 'text_sv': p.text('sv')}
            # the day the page is headed with ("KKO:2025:31 | 24.3.2025 | ..."): 1.1.<year> when Finlex has the year only
            pd = re.search(r'<title>[^|<]*\|\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*\|', html)
            if pd:
                rec['page_date'] = f'{pd.group(3)}-{int(pd.group(2)):02d}-{int(pd.group(1)):02d}'
            line = json.dumps(rec, ensure_ascii=False) + '\n'
            with wlock:
                writers[t].write(line)
                count[0] += 1
                if count[0] % 1000 == 0:
                    print(f'fetched {count[0]} ({count[1]} errors)', flush=True)
                    for w in writers.values():
                        w.flush()
        except Exception as e:
            if '429' in repr(e):  # the site asks us to slow down
                time.sleep(30)
            with wlock:
                count[1] += 1
                with open(out / 'errors.log', 'a', encoding='utf-8') as f:
                    f.write(f'{u}\t{e!r}\n')

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, todo))
    for w in writers.values():
        w.close()
    (out / 'DONE').write_text(time.strftime('%Y-%m-%dT%H:%M:%S'))
    print(f'DONE fetched {count[0]}, errors {count[1]}', flush=True)


if __name__ == '__main__':
    main()
