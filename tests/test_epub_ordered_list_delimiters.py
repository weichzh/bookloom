from __future__ import annotations

import base64
import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_browser_qa as qa
from tools import translator
from tools.browser_runtime import AgentBrowserSession

ROOT = Path(__file__).resolve().parents[1]
READER = 'markdown+fenced_divs+bracketed_spans+footnotes-raw_html'
H = '{http://www.w3.org/1999/xhtml}'
WRAPPER = 'translator-list-decimal-two-parens'
STYLE = 'translator-decimal-two-parens'
SOURCE = '''# Marker fixture

See [first](#first) and [note](#note).

(3) [first item]{#first style="font-style: italic;"}, with [note](#note).

    1. nested period one
    2. nested period two

(4) second item.

    (1) nested paren one
    (2) nested paren two

Paragraph between lists.

1. adjacent period one
2. adjacent period two

(a) lower alpha paren one
(b) lower alpha paren two

5) decimal one-paren five
6) decimal one-paren six

## Note {#note}

Return [first](#first).
'''

pytestmark = pytest.mark.skipif(shutil.which('pandoc') is None, reason='Pandoc required')


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@pytest.fixture(scope='module')
def built_epub(tmp_path_factory):
    work_path = tmp_path_factory.mktemp('native-list')
    entry = work_path / 'main.md'
    output = work_path / 'book.epub'
    cover = work_path / 'cover.png'
    entry.write_text(SOURCE, encoding='utf-8')
    cover.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='
    ))
    language = translator.Language('en', 'source', entry, None, {'epub': output})
    work = translator.Work(
        ROOT, work_path,
        {'epub': {'cover': 'cover.png', 'title_page': False, 'toc_depth': 2, 'split_level': 1}},
        'native-list-fixture', 'Marker fixture', 'markdown', None,
        work_path / 'source.pdf', 1, (language,),
    )
    # Uses the production Markdown adapter, including its JSON-to-EPUB writer.
    translator.build_markdown(work, language, 'epub', output)
    with zipfile.ZipFile(output) as archive:
        members = [name for name in archive.namelist() if name.endswith('.xhtml')]
        member = next(name for name in members if b'first item' in archive.read(name))
        document = ET.fromstring(archive.read(member))
    return work, language, output, member, document


def test_real_epub_writer_preserves_list_structure_and_unrelated_formats(built_epub):
    work, language, _output, _member, document = built_epub
    source = translator._pandoc_boundary_ast(work, SOURCE, 'markdown')
    lists = [node for node in walk(source) if node.get('t') == 'OrderedList']
    attributes = [(node['c'][0][0], node['c'][0][1]['t'], node['c'][0][2]['t'])
                  for node in lists]
    assert attributes == [
        (3, 'Decimal', 'TwoParens'), (1, 'Decimal', 'Period'),
        (1, 'Decimal', 'TwoParens'), (1, 'Decimal', 'Period'),
        (1, 'LowerAlpha', 'TwoParens'), (5, 'Decimal', 'OneParen'),
    ]

    wrappers = [n for n in document.iter(H + 'div') if n.get('class') == WRAPPER]
    assert len(wrappers) == 2
    outer = wrappers[0].find(H + 'ol')
    assert outer is not None and outer.get('start') == '3' and outer.get('type') == '1'
    items = outer.findall(H + 'li')
    assert len(items) == 2
    assert items[0].find('.//' + H + 'span[@id="first"]') is not None
    assert items[0].find(H + 'ol') is not None  # ordinary nested list, no wrapper
    assert items[1].find(H + 'div[@class="' + WRAPPER + '"]/' + H + 'ol') is not None
    assert [len(n.findall(H + 'li')) for n in document.iter(H + 'ol')] == [2, 2, 2, 2, 2, 2]
    assert all(n.find(H + 'ol') is not None for n in wrappers)
    assert len(list(document.iter(H + 'ol'))) == len(lists)
    assert document.find('.//*[@id="note"]') is not None
    hrefs = [n.get('href') for n in document.iter(H + 'a')]
    assert sum(bool(href and href.endswith('#note')) for href in hrefs) == 2
    assert sum(bool(href and href.endswith('#first')) for href in hrefs) == 2

    # The dedicated writer filter must be inert when used with non-EPUB writers.
    for target in ('html5', 'typst'):
        command = ['pandoc', str(language.entry), '--from=' + READER,
                   '--to=' + target, '--lua-filter=' + str(ROOT / 'formats/pandoc/semantics.lua')]
        original = translator.run(command, cwd=ROOT).stdout
        unchanged = translator.run(
            command + ['--lua-filter=' + str(ROOT / 'formats/pandoc/epub-list-delimiters.lua')],
            cwd=ROOT,
        ).stdout
        assert unchanged == original


@pytest.mark.skipif(
    not shutil.which('java') or not translator.EPUBCHECK_JAR.is_file(),
    reason='installed EPUBCheck and Java required',
)
def test_real_epub_passes_project_qa_and_epubcheck(built_epub, tmp_path, monkeypatch):
    work, language, output, _, _ = built_epub
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    temporary = tmp_path / 'epubcheck-tmp'
    temporary.mkdir()
    monkeypatch.setattr(translator, 'temp_root', lambda: temporary)
    translator.qa_epub(work, language, output, evidence)
    assert 'epubcheck=passed' in (evidence / 'summary.txt').read_text(encoding='utf-8')
    assert 'No errors or warnings detected.' in (evidence / 'epubcheck.txt').read_text(encoding='utf-8')


@pytest.mark.skipif(
    not (os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser')),
    reason='agent-browser required',
)
def test_real_epub_list_markers_in_three_reader_modes(built_epub, tmp_path):
    _, _, output, member, _ = built_epub
    unpacked = tmp_path / 'book'
    qa.safe_extract(output, unpacked)
    page = unpacked / member
    keys = ('PATH', 'SystemRoot', 'WINDIR', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA',
            'COMSPEC', 'TRANSLATOR_AGENT_BROWSER', 'TRANSLATOR_BROWSER_EXECUTABLE',
            'AGENT_BROWSER_EXECUTABLE_PATH')
    environment = {key: value for key in keys if (value := os.getenv(key)) is not None}
    runtime = AgentBrowserSession(
        'translator-fa3bb6797355', 'reader-list-' + str(time.time_ns()),
        scratch=tmp_path / 'runtime', environment=environment,
    )

    def command(arguments, capture=True):
        result = runtime.run(arguments, capture=capture)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def evaluate(script):
        return qa.parse_json_output(command(['eval', script]))

    expected_markers = ['(3) ', '1. ', '2. ', '(4) ', '(1) ', '(2) ',
                        '1. ', '2. ', 'a. ', 'b. ', '5. ', '6. ']
    try:
        command(['open', 'about:blank'], capture=False)
        for mode in qa.default_modes():
            name = mode['name']
            width, height = mode['viewport']
            harness = tmp_path / (name + '.html')
            qa.write_harness(harness, [member], width, height, 10, mode,
                             base_prefix=unpacked.as_uri() + '/')
            command(['set', 'viewport', str(width), str(height)])
            command(['set', 'media', mode['color_scheme']])
            command(['open', harness.as_uri()])
            result = evaluate('window.qaShowPage(' + json.dumps(page.as_uri() + '#first') + ')')
            detail = evaluate(r'''(()=>{
              const d=frame.contentDocument, first=d.getElementById('first');
              const outer=first.closest('ol'), items=[...outer.children].filter(n=>n.localName==='li');
              return {start:outer.start,type:outer.type,
                topStyle:getComputedStyle(items[0]).listStyleType,
                nestedPeriod:getComputedStyle(items[0].querySelector('ol li')).listStyleType,
                nestedParen:getComputedStyle(items[1].querySelector('ol li')).listStyleType,
                styles:d.querySelectorAll('link[rel~="stylesheet"],style:not(#qa-reader-overrides)').length,
                inlineStyles:d.querySelectorAll('[style]').length};})()''')
            assert detail['start'] == 3 and detail['type'] == '1'
            assert detail['topStyle'] == STYLE and detail['nestedParen'] == STYLE
            assert detail['nestedPeriod'] == 'decimal'
            assert result['overflowCount'] == 0, result['overflowElements']
            settings = result['readerSettings']
            assert settings['actualColorScheme'] == mode['color_scheme']
            if mode['disable_publisher_css']:
                assert settings['publisherCssDisabled'] is True
                assert detail['styles'] == 0 and detail['inlineStyles'] == 0
                assert settings['userFontApplied'] is True
                assert settings['actualFontSizePx'] == 32
            else:
                assert settings['publisherCssDisabled'] is False and detail['styles'] > 0
            snapshot = command(['snapshot'])
            (tmp_path / (name + '-ax.txt')).write_text(snapshot, encoding='utf-8')
            markers = re.findall(r'^\s*- ListMarker "([^"]*)"\s*$', snapshot, re.MULTILINE)
            assert markers == expected_markers
            command(['screenshot', str(tmp_path / (name + '.png'))])
            forward = evaluate(r'''(async()=>{const a=frame.contentDocument.querySelector('#first').closest('li').querySelector('a');
              return await clickNavigation(a,linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href));})()''')
            back = evaluate(r'''(async()=>{const a=frame.contentDocument.querySelector('#note a');
              return await clickNavigation(a,linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href));})()''')
            assert forward is True and back is True
    finally:
        closed = runtime.run(['close'])
        assert closed.returncode == 0, closed.stderr
