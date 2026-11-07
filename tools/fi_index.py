"""finland-law index for the overlay: every section file the repo has ever held keyed per act folder ("7:3" for
luku-07-pykala-003.md, "5" for pykala-005.md), act number -> folder and title -> folder (acts.csv), and the abbreviations
Finnish courts write for the acts finland-law holds (each checked to resolve to a folder). Writes fi_index.json.
  python fi_index.py <finland-law clone (bare is fine)> [ref]"""
import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path

repo, ref = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else 'main')
git = lambda *a: subprocess.run(['git', '-C', repo, *a], capture_output=True).stdout.decode('utf-8', 'replace')
acts = list(csv.DictReader(io.StringIO(git('show', f'{ref}:acts.csv'))))
by_act = {r['act']: r['folder'] for r in acts}
by_title = {}
for r in acts:
    t = r['title'].strip().lower()
    if t:
        by_title.setdefault(t, r['folder'])
names = set(git('log', ref, '--format=', '--name-only', '--no-renames').split('\n')) | set(git('ls-tree', '-r', '--name-only', ref).split('\n'))
arts = {}
for p in names:
    m = re.match(r'([^/]+)/(?:luku-0*(\d+[a-z]?)-)?pykala-0*(\d+[a-z]*)\.md$', p)
    if m:
        key = f'{m.group(2)}:{m.group(3)}' if m.group(2) else m.group(3)
        arts.setdefault(m.group(1), {})[key] = p
# Abbreviations the courts use, by act number; kept only when finland-law holds the act.
ABBR = {
    'PL': '731/1999', 'TSL': '55/2001', 'MK': '540/1995', 'HL': '434/2003', 'KSL': '38/1978', 'VahL': '412/1974',
    'OikTL': '228/1929', 'AsOYL': '1599/2009', 'OYL': '624/2006', 'KonkL': '120/2004', 'UK': '705/2007', 'PK': '40/1965',
    'AL': '234/1929', 'KL': '355/1987', 'KauppaL': '355/1987', 'MRL': '132/1999', 'TVL': '1535/1992', 'AVL': '1501/1993',
    'VML': '1558/1995', 'UlkL': '301/2004', 'HLL': '586/1996', 'OHL': '808/2019', 'YSL': '527/2014', 'SHL': '1301/2014',
    'TTurvL': '738/2002', 'YhdenvertL': '1325/2014', 'TasaArvoL': '609/1986', 'AHVL': '481/1995', 'TekijäL': '404/1961',
    'TekL': '404/1961', 'PatL': '550/1967', 'TMKL': '544/2019', 'KilpL': '948/2011', 'VakSopL': '543/1994', 'KorkoL': '633/1982',
    'VKL': '622/1947', 'TuotevastuuL': '694/1990', 'YrSanL': '1233/2006', 'TyöttömyysturvaL': '1290/2002', 'TTL': '1290/2002',
    'KunL': '410/2015', 'JulkL': '621/1999', 'TSHL': '1050/2018', 'EAL': '1286/2015', 'PerintöK': '40/1965',
    'HolhTL': '442/1999', 'LapsenhuoltoL': '361/1983', 'ElatusL': '704/1975', 'IsyysL': '11/2015', 'VLL': '701/1993',
    'YVL': '1022/2006', 'VaihtovelkakirjaL': '242/1932', 'HankintaL': '1397/2016', 'VirkamiesL': '750/1994',
    'KVhL': '304/2003', 'TilintarkL': '1141/2015', 'KPL': '1336/1997', 'PakkokeinoL': '806/2011', 'PKL': '806/2011',
    'ETL': '805/2011', 'EsitutkintaL': '805/2011', 'ROL': '689/1997', 'YhdistysL': '503/1989', 'SäätiöL': '487/2015',
}
aliases = {k: by_act[v] for k, v in ABBR.items() if v in by_act and by_act[v] in arts}
# "perustuslain 21 §": the Constitution's title is "Suomen perustuslaki", so its genitive needs its own entry.
if by_act.get('731/1999') in arts:
    by_title.setdefault('perustuslaki', by_act['731/1999'])
# "<Stem>L" forms courts write from a one-word act name ending in -laki ("TuotevastuuL" for tuotevastuulaki)
for r in acts:
    t = r['title'].strip()
    if re.fullmatch(r'[A-ZÅÄÖ][a-zåäö\-]+laki', t) and r['folder'] in arts:
        aliases.setdefault(t[:-4].capitalize() + 'L', r['folder'])
json.dump({'by_act': by_act, 'by_title': by_title, 'aliases': aliases, 'arts': arts},
          open(Path(__file__).with_name('fi_index.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print(len(acts), 'acts;', len(arts), 'folders with sections;', sum(len(v) for v in arts.values()), 'section files (ever);', len(aliases), 'abbreviations')
for k in ('TSL', 'PL', 'MK', 'HL', 'TuotevastuuL', 'OYL'):
    print(k, aliases.get(k))
