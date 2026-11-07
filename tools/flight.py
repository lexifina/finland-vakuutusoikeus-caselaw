"""Read a finlex.fi decision page: the Next.js flight payload holds the metadata (dt/dd pairs) and the decision text as
a React element tree (["$", tag, key, props]) split over rows that refer to each other ("$L8c"). Standard library only."""
from __future__ import annotations

import json
import re
import sys

sys.setrecursionlimit(20000)
PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
ROW = re.compile(r'^([0-9a-f]+):(.*)$')
REF = re.compile(r'^\$L?([0-9a-f]+)$')
BLOCK = {'p', 'div', 'section', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'dt', 'dd', 'table', 'ul', 'ol',
         'article', 'header', 'br'}
SKIP = {'button', 'svg', 'script', 'style', 'nav', 'img'}


def flight(html: str) -> str:
    return ''.join(json.loads('"' + m + '"') for m in PUSH.findall(html))


def rows(fl: str) -> dict:
    out = {}
    for line in fl.split('\n'):
        m = ROW.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def meta(fl: str) -> dict:
    """Every <dt>label</dt><dd>value</dd> pair the page renders (Antopäivä, Diaarinumero, ECLI-tunnus, ...)."""
    out = {}
    for m in re.finditer(r'\["\$","dt",null,\{"children":"([^"]+)"\}\],\["\$","dd",null,\{(.{0,1500}?)\}\]\]\}\]', fl):
        label, body = m.group(1), m.group(2)
        dt = re.search(r'"dateTime":"(\d{4}-\d{2}-\d{2})"', body)
        if dt:
            out.setdefault(label, dt.group(1))
            continue
        vals = []
        for a, b in re.findall(r'"children":(?:"((?:[^"\\]|\\.)*)"|(\d+))', body):
            v = json.loads('"' + a + '"') if a else b
            if v and not v.startswith('$'):
                vals.append(v)
        if vals:
            out.setdefault(label, ' '.join(dict.fromkeys(vals)))
    return out


class Page:
    def __init__(self, html: str):
        self.fl = flight(html)
        self.raw = rows(self.fl)
        self.cache = {}

    def row(self, k):
        if k not in self.cache:
            try:
                self.cache[k] = json.loads(self.raw[k])
            except (KeyError, ValueError):
                self.cache[k] = None
        return self.cache[k]

    def render(self, node, out, depth=0):
        if depth > 4000:
            return
        if isinstance(node, str):
            m = REF.match(node)
            if m and m.group(1) in self.raw:
                self.render(self.row(m.group(1)), out, depth + 1)
            elif not node.startswith('$'):
                out.append(node)
            return
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            out.append(str(node))
            return
        if isinstance(node, list):
            if len(node) == 4 and node[0] == '$' and isinstance(node[1], str):
                tag, props = node[1], node[3] if isinstance(node[3], dict) else {}
                if tag in SKIP or (props.get('className') or '').find('visually') >= 0:
                    return
                if tag == 'br':
                    out.append('\n')
                    return
                self.render(props.get('children'), out, depth + 1)
                if tag in BLOCK:
                    out.append('\n')
                return
            for c in node:
                self.render(c, out, depth + 1)
            return
        if isinstance(node, dict):
            self.render(node.get('children'), out, depth + 1)

    def sections(self):
        """(lang, element) for each akomaNtoso section of the page."""
        found = []
        for k in self.raw:
            if 'akomaNtoso' not in self.raw[k]:
                continue
            stack = [self.row(k)]
            while stack:
                x = stack.pop()
                if isinstance(x, list):
                    if len(x) == 4 and x[0] == '$' and isinstance(x[3], dict) and 'akomaNtoso' in str(x[3].get('className', '')):
                        found.append((x[3].get('lang') or '', x))
                        continue
                    stack.extend(x)
                elif isinstance(x, dict):
                    stack.extend(x.values())
        return found

    def text(self, lang='fi'):
        out = []
        for lg, el in self.sections():
            if lg == lang:
                self.render(el, out)
        t = ''.join(out)
        t = re.sub(r'[ \t]+\n', '\n', t)
        return re.sub(r'\n{3,}', '\n\n', t).strip()

    def langs(self):
        return sorted({lg for lg, _ in self.sections() if lg})
