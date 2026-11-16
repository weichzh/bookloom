from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import translator
from tools.epub_page_navigation import NativePageLocation, PageNavigationConfig

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / 'formats/pandoc/semantics.lua'
READER = 'markdown+fenced_divs+bracketed_spans+footnotes-raw_html'
H = '{http://www.w3.org/1999/xhtml}'
pytestmark = pytest.mark.skipif(shutil.which('pandoc') is None, reason='Pandoc required')


def convert(source, target='html5'):
    return translator.run(['pandoc', '--from='+READER, '--to='+target,
                           '--lua-filter='+str(FILTER)], input_text=source).stdout


def test_pure_title_source_unit_preserves_native_reader_and_unwraps_on_export():
    source = ('::: {.source-unit data-unit="1"}\n\n'
              '# Part *One* {#part-one}\n\n:::\n\n'
              '## Chapter {#chapter}\n\n[]{.source-unit data-unit="2"}Body.\n')
    assert translator.source_units_for('markdown', source) == [1, 2]
    ast = json.loads(convert(source, 'json'))
    assert ast['blocks'][0]['t'] == 'Div'
    assert ast['blocks'][0]['c'][0] == ['', ['source-unit'], [['data-unit', '1']]]
    heading = ast['blocks'][0]['c'][1][0]
    assert heading['t'] == 'Header' and heading['c'][1][0] == 'part-one'
    assert not translator.markdown_marker_heading_problems(ast)
    html = convert(source)
    assert 'source-unit' not in html and 'data-unit' not in html
    assert '<div' not in html and '<p></p>' not in html
    assert '<h1 id="part-one">Part <em>One</em></h1>' in html
    assert '<p>Body.</p>' in html


@pytest.mark.parametrize('attrs,body,message', [
    ('.source-unit', '# Part', 'numeric data-unit'),
    ('.source-unit data-unit="bad"', '# Part', 'numeric data-unit'),
    ('.source-unit data-unit="1"', 'Body.', 'exactly one Header'),
    ('.source-unit data-unit="1"', '# Part\n\n## Other', 'exactly one Header'),
    ('.source-unit data-unit="1"', '# Part\n\nBody.', 'exactly one Header'),
    ('#legacy .source-unit data-unit="1"', '# Part', 'container ID or extra classes'),
    ('.source-unit .styled data-unit="1"', '# Part', 'container ID or extra classes'),
    ('.source-unit data-unit="1" style="color:red"', '# Part', 'extra attributes'),
])
def test_pure_title_metadata_never_swallows_other_content_or_identity(attrs, body, message):
    source = '::: {'+attrs+'}\n\n'+body+'\n\n:::\n'
    with pytest.raises(translator.CliError, match=message):
        convert(source)


def test_old_span_and_header_marker_rejection_are_unchanged():
    assert convert('[]{.source-unit data-unit="3"}Body.\n').strip() == '<p>Body.</p>'
    ast = json.loads(convert('# Part []{.source-unit data-unit="1"}\n', 'json'))
    assert translator.markdown_marker_heading_problems(ast) == ['溯源标记不得放入标题节点']


def test_real_epub_keeps_part_chapter_outline_and_before_target_pagepoint(tmp_path):
    work = tmp_path/'Works'/'title-unit'
    work.mkdir(parents=True)
    entry = work/'main.md'
    entry.write_text('::: {.source-unit data-unit="1"}\n\n'
                     '# Part {#part}\n\n:::\n\n'
                     '## Chapter {#chapter}\n\n'
                     '[]{.source-unit data-unit="2"}'
                     '[]{#page_1 epub:type="pagebreak" role="doc-pagebreak" title="1"}Body.\n',
                     encoding='utf-8')
    output = work/'book.epub'
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=entry,
        source_command=['pandoc',str(entry),'--from='+READER,
                        '--lua-filter='+str(FILTER),'--resource-path='+str(work)],
        writer_options=['--toc','--toc-depth=2','--split-level=2','--epub-title-page=false',
                        '--metadata=title:Title Unit'],
        source_cwd=ROOT, output=output, toc_depth=2,
        page_navigation=PageNavigationConfig('urn:test:title-unit','Original','en',
            (NativePageLocation('page_1','1','chapter'),),mode='native-pagebreaks'))
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n))for n in archive.namelist()if n.endswith('.xhtml')]
        nav = ET.fromstring(archive.read('EPUB/nav.xhtml'))
    toc = next(n for n in nav.iter(H+'nav')if n.get('{http://www.idpf.org/2007/ops}type')=='toc')
    assert [''.join(a.itertext())for a in toc.iter(H+'a')]==['Part','Chapter']
    assert not any(e.get('class')=='source-unit'for d in docs for e in d.iter())
    assert not any(e.tag==H+'p' and not ''.join(e.itertext()).strip() and not list(e)
                   for d in docs for e in d.iter())
    chapter = next(e for d in docs for e in d.iter()if e.get('id')=='chapter')
    parent = next(p for d in docs for p in d.iter()if chapter in list(p))
    assert list(parent)[list(parent).index(chapter)-1].get('id')=='page_1'


def test_json_writer_boundary_expands_only_title_metadata_and_preserves_old_nodes():
    heading = {'t':'Header','c':[1,['part',[],[]],[{'t':'Str','c':'Part'}]]}
    span = {'t':'Span','c':[['',['source-unit'],[['data-unit','2']]],[]]}
    source = {'blocks':[
        {'t':'Div','c':[['',['source-unit'],[['data-unit','1']]],[heading]]},
        {'t':'Div','c':[['ordinary',[],[]],[{'t':'Para','c':[span,{'t':'Str','c':'Body'}]}]]}
    ]}
    writer = json.loads(json.dumps(source))
    assert translator._unwrap_pure_title_source_units(writer) == 1
    assert source['blocks'][0]['t'] == 'Div'
    assert writer['blocks'][0] == heading
    assert writer['blocks'][1] == source['blocks'][1]


@pytest.mark.parametrize('attrs,blocks,message',[
    (['',['source-unit'],[['data-unit','bad']]],[], 'numeric data-unit'),
    (['legacy',['source-unit'],[['data-unit','1']]],[], 'container ID'),
    (['',['source-unit'],[['data-unit','1'],['style','color:red']]],[], 'extra attributes'),
    (['',['source-unit'],[['data-unit','1']]],[{'t':'Para','c':[]}], 'exactly one Header'),
])
def test_json_writer_boundary_rejects_nonmetadata_container(attrs,blocks,message):
    with pytest.raises(translator.CliError,match=message):
        translator._unwrap_pure_title_source_units({'blocks':[{'t':'Div','c':[attrs,blocks]}]})
