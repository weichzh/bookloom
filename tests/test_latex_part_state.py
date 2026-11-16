from __future__ import annotations

import base64
import json
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urldefrag

import pytest

from tools import translator
from tools.browser_runtime import run_hidden

ROOT = Path(__file__).resolve().parents[1]
H = '{http://www.w3.org/1999/xhtml}'
EPUB_TYPE = '{http://www.idpf.org/2007/ops}type'
PANDOC = shutil.which('pandoc')
pytestmark = pytest.mark.skipif(not PANDOC, reason='Pandoc required')


def source(*, parts: bool = True) -> str:
    blocks = [r'''\documentclass{book}
\usepackage{graphicx}
\begin{document}
\frontmatter
\chapter*{Copyright}\label{front:copyright}
Copyright paragraph.
\chapter*{Preface}\label{front:preface}
Preface paragraph.
\mainmatter
''']
    for chapter in range(1, 5):
        if parts and chapter == 2:
            blocks.append(r'\part{First part}\label{part:first}' + '\n')
        elif parts and chapter == 4:
            blocks.append(r'\part*{Second part}\label{part:second}' + '\n')
        blocks.append(rf'''\chapter{{Chapter {chapter}}}\label{{ch:{chapter}}}
Chapter {chapter} paragraph remains continuous.
\section{{Section {chapter}}}\label{{sec:{chapter}}}
\subsection{{Subsection {chapter}}}\label{{sub:{chapter}}}
\begin{{figure}}
\includegraphics[alt={{Diagram {chapter}}}]{{diagram.png}}
\caption{{Diagram {chapter}}}\label{{fig:{chapter}}}
\end{{figure}}
\begin{{table}}
\caption{{Table {chapter}}}\label{{tab:{chapter}}}
\begin{{tabular}}{{ll}}A & B \\ 1 & 2 \\\end{{tabular}}
\end{{table}}
\begin{{equation}}x={chapter}\tag{{{chapter}.1}}\label{{eq:{chapter}}}\end{{equation}}
References: \ref{{ch:{chapter}}}, \ref{{sec:{chapter}}}, \ref{{sub:{chapter}}}, \ref{{fig:{chapter}}}, \ref{{tab:{chapter}}}, \eqref{{eq:{chapter}}}.
''')
    blocks.append(r'''Final main paragraph. \backmatter
\chapter*{Editorial}\label{reader:editorial}
Editorial paragraph.
\section*{Scope}\label{reader:scope}
End paragraph.
\end{document}
''')
    return '\n'.join(blocks)


def reader(tmp_path: Path, text: str) -> dict:
    entry = tmp_path / 'main.tex'
    entry.write_text(text, encoding='utf-8')
    command = translator.latex_epub_pandoc_command(
        root=ROOT, work_path=tmp_path, entry=entry, expanded=entry,
        has_part=translator.latex_source_has_part(text),
    )
    command += ['--to=json']
    result = run_hidden(command, cwd=tmp_path, capture_output=True, text=True,
                        encoding='utf-8', timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def stringify(inlines: list[dict]) -> str:
    result = ''
    for node in inlines:
        if node['t'] == 'Str': result += node['c']
        elif node['t'] in ('Space', 'SoftBreak'): result += ' '
        elif node['t'] == 'Span': result += stringify(node['c'][1])
    return result


def test_real_reader_uses_part_state_but_keeps_original_counter_levels(tmp_path):
    document = reader(tmp_path, source())
    headers = {b['c'][1][0]: b['c'] for b in document['blocks'] if b['t'] == 'Header'}
    expected = {'front:copyright': 1, 'front:preface': 1, 'part:first': 1,
                'part:second': 1, 'reader:editorial': 1, 'reader:scope': 2}
    for n in range(1, 5):
        expected.update({f'ch:{n}': 1 if n == 1 else 2,
                         f'sec:{n}': 2 if n == 1 else 3,
                         f'sub:{n}': 3 if n == 1 else 4})
    assert {key: value[0] for key, value in headers.items()} == expected
    for n in range(1, 5):
        assert stringify(headers[f'ch:{n}'][2]) == f'{n} Chapter {n}'
        assert stringify(headers[f'sec:{n}'][2]) == f'{n}.1 Section {n}'
        assert stringify(headers[f'sub:{n}'][2]) == f'{n}.1.1 Subsection {n}'
    assert stringify(headers['front:copyright'][2]) == 'Copyright'
    assert stringify(headers['reader:editorial'][2]) == 'Editorial'
    assert stringify(headers['part:second'][2]) == 'Second part'
    for n in range(1, 5):
        paragraph = next(b for b in document['blocks'] if b['t'] == 'Para'
                         and stringify(b['c']) == f'Chapter {n} paragraph remains continuous.')
        assert paragraph['t'] == 'Para'


def test_matter_switches_work_as_raw_blocks_and_raw_inlines(tmp_path):
    text = r'''\begin{document}
\chapter{Before}\label{before}
\part{Part}\label{part}
\chapter{Inside}\label{inside}
Text. \frontmatter{}
\chapter*{Late front}\label{late-front}
\mainmatter
\chapter{Restart}\label{restart}
\part*{Other}\label{other}
\chapter{Inside again}\label{again}
\backmatter
\chapter*{Back}\label{back}
\end{document}'''
    document = reader(tmp_path, text)
    headers = {b['c'][1][0]: b['c'][0] for b in document['blocks'] if b['t'] == 'Header'}
    assert headers == {'before': 1, 'part': 1, 'inside': 2, 'late-front': 1,
                       'restart': 1, 'other': 1, 'again': 2, 'back': 1}


def test_no_part_reader_retains_legacy_levels_and_writer_numbering_contract(tmp_path):
    document = reader(tmp_path, source(parts=False))
    headers = {b['c'][1][0]: b['c'] for b in document['blocks'] if b['t'] == 'Header'}
    assert headers['front:copyright'][0] == headers['reader:editorial'][0] == 1
    for n in range(1, 5):
        assert headers[f'ch:{n}'][0] == 1
        assert headers[f'sec:{n}'][0] == 2
        assert headers[f'sub:{n}'][0] == 3
        assert stringify(headers[f'ch:{n}'][2]) == f'Chapter {n}'
        assert 'unnumbered' not in headers[f'ch:{n}'][1][1]


@pytest.mark.skipif(not all(shutil.which(t) for t in ('uv', 'latex', 'dvisvgm')),
                    reason='SVG math tools required')
@pytest.mark.parametrize('parts', [True, False], ids=['parts', 'legacy-no-parts'])
def test_real_adapter_epub_nav_ids_links_and_counters(tmp_path, parts):
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII=')
    cover = tmp_path / 'cover.png'; cover.write_bytes(png)
    (tmp_path / 'diagram.png').write_bytes(png)
    entry = tmp_path / 'main.tex'; entry.write_text(source(parts=parts), encoding='utf-8')
    output = tmp_path / 'book.epub'
    translator.build_latex_epub_source(
        root=ROOT, work_path=tmp_path, entry=entry, output=output,
        title='Not a contents root', author=None, language='en',
        identifier='urn:test:latex-part-state', cover=cover,
        structure_options=['--toc-depth=4'],
    )
    with zipfile.ZipFile(output) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml')}
    nav_name = next(name for name, doc in docs.items() if doc.find(f'.//{H}nav[@{EPUB_TYPE}="toc"]') is not None)
    toc = docs[nav_name].find(f'.//{H}nav[@{EPUB_TYPE}="toc"]')
    all_ids = {(name, e.get('id')): e for name, doc in docs.items() for e in doc.iter() if e.get('id')}
    parent = {c: p for doc in docs.values() for p in doc.iter() for c in p}
    nav_items = {}
    for a in toc.iter(f'{H}a'):
        member, fragment = urldefrag(unquote(a.get('href')))
        member = posixpath.normpath(posixpath.join(posixpath.dirname(nav_name), member))
        target = all_ids[(member, fragment)]
        heading = target if target.tag.rsplit('}', 1)[-1] in ('h1','h2','h3','h4') else next(e for e in target if e.tag.rsplit('}', 1)[-1] in ('h1','h2','h3','h4'))
        label = ' '.join(''.join(a.itertext()).split())
        assert label == ' '.join(''.join(heading.itertext()).split())
        ancestors = []; node = parent[a]
        while node is not toc:
            if node.tag == f'{H}li': ancestors.append(node)
            node = parent[node]
        parent_id = None
        if len(ancestors) > 1:
            parent_link = ancestors[1].find(f'{H}a')
            parent_id = urldefrag(parent_link.get('href'))[1]
        nav_items[fragment] = {'depth': len(ancestors), 'parent': parent_id, 'label': label}
    assert nav_items['front:copyright']['depth'] == nav_items['front:preface']['depth'] == 1
    assert nav_items['ch:1']['depth'] == nav_items['reader:editorial']['depth'] == 1
    assert nav_items['reader:scope']['parent'] == 'reader:editorial'
    assert not any(r['label'] == 'Not a contents root' for r in nav_items.values())
    for n in range(1, 5):
        assert nav_items[f'ch:{n}']['label'] == f'{n} Chapter {n}'
        assert nav_items[f'ch:{n}']['depth'] == (2 if parts and n > 1 else 1)
        assert nav_items[f'sec:{n}']['parent'] == f'ch:{n}'
        assert nav_items[f'sub:{n}']['parent'] == f'sec:{n}'
    if parts:
        assert nav_items['ch:2']['parent'] == nav_items['ch:3']['parent'] == 'part:first'
        assert nav_items['ch:4']['parent'] == 'part:second'
    assert len(nav_items) == (18 if parts else 16)
    found_refs = {}
    for name, doc in docs.items():
        for a in doc.iter(f'{H}a'):
            href = a.get('href', '')
            if '#' not in href: continue
            member, fragment = urldefrag(unquote(href))
            member = posixpath.normpath(posixpath.join(posixpath.dirname(name), member)) if member else name
            assert (member, fragment) in all_ids, (name, href)
            if a.get('data-reference-type') == 'ref' or fragment.startswith(('ch:', 'sec:', 'sub:', 'fig:', 'tab:', 'eq:')):
                found_refs.setdefault(fragment, set()).add(''.join(a.itertext()))
    for n in range(1, 5):
        for kind, expected in [('ch', str(n)), ('sec',f'{n}.1'), ('sub',f'{n}.1.1'), ('fig',f'{n}.1'), ('tab',f'{n}.1'), ('eq',f'({n}.1)')]:
            assert expected in found_refs[f'{kind}:{n}'], (kind, n, found_refs)
        for kind in ('ch', 'sec', 'sub', 'fig', 'tab', 'eq'):
            assert sum(id == f'{kind}:{n}' for _, id in all_ids) == 1
    actual_text = '\n'.join(''.join(doc.itertext()) for name, doc in docs.items() if name != nav_name)
    for n in range(1, 5):
        assert f'Chapter {n} paragraph remains continuous.' in actual_text
