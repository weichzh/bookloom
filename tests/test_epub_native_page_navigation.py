from __future__ import annotations

import base64
import copy
import json
import posixpath
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urldefrag

import pytest

from tools import epub_page_navigation as pages
from tools import translator

ROOT = Path(__file__).resolve().parents[1]
H = '{' + pages.XHTML + '}'
T = pages.EPUB_TYPE


def settings(*pairs):
    return pages.PageNavigationConfig('urn:sha256:' + 'a' * 64,
        'Original EPUB, native publisher pagination', 'zh-CN',
        tuple(pages.NativePageLocation(target, label) for target, label in pairs),
        mode='native-pagebreaks')


def document(body):
    return ET.fromstring(f'<html xmlns="{pages.XHTML}" xmlns:epub="{pages.EPUB}"><head><title>Native</title></head><body>{body}</body></html>')


def fixture():
    docs = {
        'EPUB/nav.xhtml': document('<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="b.xhtml#body">Body</a></li></ol></nav>'),
        'EPUB/a.xhtml': document('<p>前<span id="page_iv" epub:type="pagebreak" title="iv"/>后。</p><p><a href="b.xhtml#page_1">Original link</a></p>'),
        'EPUB/b.xhtml': document('<h1 id="body">Body</h1><p><span id="page_1" epub:type="pagebreak" title="1" role="doc-pagebreak" aria-label="Original page one"/>Start.</p><pre><code>one\n  <span id="page_2" epub:type="pagebreak" title="2"/>two\n\tthree</code></pre><a href="a.xhtml#page_iv">Roman page</a>'),
    }
    payloads = {
        'META-INF/container.xml': b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        'EPUB/book.opf': f'<package xmlns="{pages.OPF}" version="3.0"><metadata xmlns:dc="{pages.DC}"><dc:title>Native</dc:title></metadata><manifest><item id="n" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>'.encode(),
    }
    return docs, payloads


def write_config(tmp_path, entries):
    (tmp_path/'native.json').write_text(json.dumps({'schema_version':1, 'pages':entries}), 'utf-8')
    return {'source':{'relative_path':'original.epub', 'format':'epub', 'sha256':'a'*64, 'units':'source-units.tsv'},
            'epub':{'page_navigation':{'mode':'native-pagebreaks', 'source_label':'Original EPUB', 'locations':{'zh-CN':'native.json'}}}}


def test_native_configuration_requires_no_pdf_map_or_fake_page_number(tmp_path):
    manifest = write_config(tmp_path, [{'target':'page_iv','label':'iv'}, {'target':'page_1','label':'1'}])
    config = pages.load_page_navigation_config(tmp_path, manifest, 'zh-CN')
    assert config.mode == 'native-pagebreaks'
    assert config.pages == (pages.NativePageLocation('page_iv','iv'), pages.NativePageLocation('page_1','1'))
    assert not (tmp_path/'page-map.tsv').exists()
    assert all(not hasattr(page, 'pdf_page') for page in config.pages)
    assert pages.load_page_navigation_config(tmp_path, {}, 'zh-CN') is None
    manifest['epub']['page_navigation'] = {'mode':'not-applicable', 'reason':'No reliable native pagination'}
    assert pages.load_page_navigation_config(tmp_path, manifest, 'zh-CN') is None
    legacy = pages.PageNavigationConfig('source', 'label', 'en', ())
    assert legacy.mode == 'source-pages'


@pytest.mark.parametrize('entries', [[], [{'target':'page_1','label':''}], [{'target':'page 1','label':'1'}],
    [{'target':'page_1','label':'1','pdf_page':1}], [{'pdf_page':1,'position':'marker'}],
    [{'target':'page_1','label':'1'}, {'target':'page_1','label':'2'}]])
def test_native_configuration_rejects_incomplete_or_pdf_shaped_inventory(tmp_path, entries):
    with pytest.raises(pages.PageNavigationError):
        pages.load_page_navigation_config(tmp_path, write_config(tmp_path,entries), 'zh-CN')


def test_native_mode_rejects_a_pdf_source(tmp_path):
    manifest = write_config(tmp_path,[{'target':'page_1','label':'1'}]);manifest['source']['format']='pdf'
    with pytest.raises(pages.PageNavigationError, match='EPUB'):
        pages.load_page_navigation_config(tmp_path,manifest,'zh-CN')


def test_native_points_are_not_moved_or_recreated_and_characters_links_toc_survive():
    docs,payloads = fixture();config=settings(('page_iv','iv'),('page_1','1'),('page_2','2'))
    old_toc=ET.tostring(docs['EPUB/nav.xhtml']);old_text={name:''.join(r.itertext()) for name,r in docs.items()}
    old_links=[(name,e.get('href')) for name,r in docs.items() for e in r.iter(H+'a')]
    points=[(name,e) for name,r in docs.items() for e in r.iter() if e.get(T)=='pagebreak']
    parents={c:(p,list(p).index(c),c.tail) for r in docs.values() for p in r.iter() for c in p}
    result=pages.apply_page_navigation(docs,payloads,config)
    assert result.page_count==3 and result.mode=='native-pagebreaks'
    assert [x[0] for x in result.targets]==['iv','1','2']
    for name,e in points:
        parent,index,tail=parents[e]
        assert parent[index] is e and e.tail==tail
        assert e.get('data-page') is None
        assert e.get('role')=='doc-pagebreak' and e.get('aria-label')
    assert points[1][1].get('aria-label')=='Original page one'
    for name in ('EPUB/a.xhtml','EPUB/b.xhtml'):
        assert ''.join(docs[name].itertext())==old_text[name]
    assert [(name,e.get('href')) for name,r in docs.items() if name!='EPUB/nav.xhtml' for e in r.iter(H+'a')]==[x for x in old_links if x[0]!='EPUB/nav.xhtml']
    nav=docs['EPUB/nav.xhtml'];navigation=nav.find(f'.//{H}nav[@{T}="page-list"]')
    assert [a.text for a in navigation.iter(H+'a')]==['iv','1','2']
    stripped=copy.deepcopy(nav);stripped.find(H+'body').remove(stripped.find(f'.//{H}nav[@{T}="page-list"]'))
    assert ET.tostring(stripped)==old_toc
    assert pages.validate_page_navigation(docs,payloads,config)==result
    assert pages.validate_page_navigation(docs,payloads)==result
    opf=ET.fromstring(payloads['EPUB/book.opf']);source=opf.find(f'.//{{{pages.DC}}}source')
    assert source.text=='Original EPUB, native publisher pagination; urn:sha256:'+'a'*64
    assert not any('pdf_page' in key for _,e in points for key in e.attrib)


@pytest.mark.parametrize('mutation', ['missing','extra','label','order','not-pagebreak','text','child','duplicate','duplicate-other-member','outside-spine','role','page-list','source-collision'])
def test_native_preflight_rejects_false_or_ambiguous_targets(mutation):
    docs,payloads=fixture();config=settings(('page_iv','iv'),('page_1','1'),('page_2','2'))
    first=next(e for e in docs['EPUB/a.xhtml'].iter() if e.get('id')=='page_iv')
    if mutation=='missing':first.attrib.pop('id')
    elif mutation=='extra':ET.SubElement(docs['EPUB/b.xhtml'].find(H+'body'),H+'span',{'id':'extra',T:'pagebreak','title':'3'})
    elif mutation=='label':first.set('title','v')
    elif mutation=='order':config=settings(('page_1','1'),('page_iv','iv'),('page_2','2'))
    elif mutation=='not-pagebreak':first.attrib.pop(T)
    elif mutation=='text':first.text='Visible invented page'
    elif mutation=='child':ET.SubElement(first,H+'em').text='x'
    elif mutation=='duplicate':ET.SubElement(docs['EPUB/a.xhtml'].find(H+'body'),H+'span',{'id':'page_iv'})
    elif mutation=='duplicate-other-member':ET.SubElement(docs['EPUB/b.xhtml'].find(H+'body'),H+'span',{'id':'page_iv'})
    elif mutation=='outside-spine':docs['EPUB/not-in-spine.xhtml']=document('<span id="extra" epub:type="pagebreak" title="99"/>')
    elif mutation=='role':first.set('role','button')
    elif mutation=='page-list':ET.SubElement(docs['EPUB/nav.xhtml'].find(H+'body'),H+'nav',{T:'page-list'})
    elif mutation=='source-collision':
        opf=ET.fromstring(payloads['EPUB/book.opf']);ET.SubElement(opf.find(f'{{{pages.OPF}}}metadata'),f'{{{pages.DC}}}source',{'id':'page-navigation-source'});payloads['EPUB/book.opf']=ET.tostring(opf)
    with pytest.raises(pages.PageNavigationError):pages.apply_page_navigation(docs,payloads,config)


@pytest.mark.parametrize('mutation',['wrong-link','wrong-label','wrong-id','wrong-order','wrong-role','duplicate','missing-source'])
def test_native_final_qa_rejects_drift(mutation):
    docs,payloads=fixture();config=settings(('page_iv','iv'),('page_1','1'),('page_2','2'));pages.apply_page_navigation(docs,payloads,config)
    point=next(e for e in docs['EPUB/a.xhtml'].iter() if e.get('id')=='page_iv')
    listing=docs['EPUB/nav.xhtml'].find(f'.//{H}nav[@{T}="page-list"]');link=next(listing.iter(H+'a'))
    if mutation=='wrong-link':link.set('href','b.xhtml#body')
    elif mutation=='wrong-label':point.set('title','v');link.text='v'
    elif mutation=='wrong-id':point.set('id','not-original');link.set('href','a.xhtml#not-original')
    elif mutation=='wrong-order':
        ol=listing.find(H+'ol');children=list(ol);ol[:]=[children[1],children[0],*children[2:]]
    elif mutation=='wrong-role':point.set('role','note')
    elif mutation=='duplicate':ET.SubElement(docs['EPUB/b.xhtml'].find(H+'body'),H+'span',{'id':'page_iv'})
    elif mutation=='missing-source':
        opf=ET.fromstring(payloads['EPUB/book.opf']);metadata=opf.find(f'{{{pages.OPF}}}metadata');metadata.remove(metadata.find(f'{{{pages.DC}}}source'));payloads['EPUB/book.opf']=ET.tostring(opf)
    with pytest.raises(pages.PageNavigationError):pages.validate_page_navigation(docs,payloads,config)


def build_native_markdown_adapter_fixture(tmp_path, marker_class, *, ordinary_provenance=False):
    entry=tmp_path/'main.md';cover=tmp_path/'cover.png';output=tmp_path/'book.epub'
    cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='))
    entry.write_text('''# Copyright {#copyright .unnumbered .unlisted}

Before[]{#page_iv epub:type="pagebreak" role="doc-pagebreak" title="iv"}after.

# Body {#body .unnumbered}

[]{#page_1 epub:type="pagebreak" role="doc-pagebreak" title="1"}Start.

# Notes {#notes .unnumbered}

A single paragraph continues[]{#page_2 epub:type="pagebreak" role="doc-pagebreak" title="2"} on the next original page. [Back](#page_iv)
''','utf-8')
    if marker_class:
        entry.write_text(entry.read_text('utf-8').replace('#page_1 epub:', '#page_1 .'+marker_class+' epub:'), 'utf-8')
    if ordinary_provenance:
        entry.write_text(entry.read_text('utf-8').replace('Start.\n', 'Start.\n\n[Legacy provenance]{#ordinary-provenance .source-page data-page="99"}\n'), 'utf-8')
    manifest=write_config(tmp_path,[{'target':'page_iv','label':'iv'},{'target':'page_1','label':'1'},{'target':'page_2','label':'2'}]);manifest['epub'].update(cover='cover.png',title_page=False,toc_depth=1,bodymatter='body')
    original_docs, original_payloads = fixture()
    with zipfile.ZipFile(tmp_path/'original.epub', 'w') as original:
        for name,data in original_payloads.items():original.writestr(name,data)
        for name,root in original_docs.items():original.writestr(name,ET.tostring(root))
    language=translator.Language('zh-CN','source',entry,None,{'epub':output})
    work=translator.Work(ROOT,tmp_path,manifest,'native-page-test','Native pages','markdown',None,tmp_path/'original.epub',0,(language,))
    translator.build_markdown(work,language,'epub',output)
    return output, manifest


@pytest.mark.skipif(not shutil.which('pandoc'),reason='Pandoc required')
@pytest.mark.parametrize('marker_class', ['', 'source-page', 'source-unit', 'image-tail'])
def test_real_markdown_adapter_keeps_native_roman_and_paragraph_page_positions(tmp_path, marker_class):
    output, manifest = build_native_markdown_adapter_fixture(tmp_path, marker_class)
    with zipfile.ZipFile(output) as z:
        docs={name:ET.fromstring(z.read(name)) for name in z.namelist() if name.endswith('.xhtml')}
        payloads={name:z.read(name) for name in z.namelist()}
    config=pages.load_page_navigation_config(tmp_path,manifest,'zh-CN');result=pages.validate_page_navigation(docs,payloads,config)
    assert result.page_count==3 and result.mode=='native-pagebreaks'
    points=[e for r in docs.values() for e in r.iter() if e.get(T)=='pagebreak']
    assert [(e.get('id'),e.get('title')) for e in points]==[('page_iv','iv'),('page_1','1'),('page_2','2')]
    roman=points[0];parents={c:p for r in docs.values() for p in r.iter() for c in p}
    assert parents[roman].text=='Before' and roman.tail=='after.'
    assert parents[points[2]].text=='A single paragraph continues'
    assert points[2].tail==' on the next original page. '
    assert not (tmp_path/'page-map.tsv').exists()
    assert all(e.get('data-page') is None for e in points)
    toc=next(e for r in docs.values() for e in r.iter(H+'nav') if e.get(T)=='toc')
    assert [''.join(a.itertext()) for a in toc.iter(H+'a')]==['Body','Notes']
    for name,r in docs.items():
        for a in r.iter(H+'a'):
            href=a.get('href','')
            if '#' not in href:continue
            member,id=urldefrag(unquote(href));member=posixpath.normpath(posixpath.join(posixpath.dirname(name),member)) if member else name
            assert sum(e.get('id')==id for e in docs[member].iter())==1


@pytest.mark.parametrize('class_name', ['source-page', None])
@pytest.mark.parametrize('kind,types,keep', [
    ('namespace','pagebreak',True), ('namespace','foo pagebreak bar',True),
    ('literal','pagebreak',True), ('literal','pagebreak foo',True),
    (None,None,False), ('namespace','not-pagebreak',False),
])
def test_empty_span_cleanup_keeps_only_standard_pagebreak_tokens(class_name,kind,types,keep):
    root=document('<p>before<span id="native" class="source-page"/>after</p>')
    parent=root.find(f'{H}body/{H}p');point=parent[0]
    if kind is not None:point.set(T if kind=='namespace' else 'epub:type',types)
    old_attrs=dict(point.attrib)
    removed=translator.remove_empty_spans(root,class_name)
    assert removed==(0 if keep else 1)
    assert ''.join(parent.itertext())=='beforeafter'
    if keep:
        assert parent[0] is point and point.attrib==old_attrs and point.tail=='after'
    else:
        assert len(parent)==0


def test_native_declared_truth_does_not_require_contiguous_numbers_or_a_source_total():
    docs,payloads=fixture()
    next(e for e in docs['EPUB/b.xhtml'].iter() if e.get('id')=='page_1').set('title','3')
    next(e for e in docs['EPUB/b.xhtml'].iter() if e.get('id')=='page_2').set('title','7')
    config=settings(('page_iv','iv'),('page_1','3'),('page_2','7'))
    result=pages.apply_page_navigation(docs,payloads,config)
    assert [label for label,_,_ in result.targets]==['iv','3','7']


@pytest.mark.parametrize('orphan_member', ['EPUB/nav.xhtml', 'EPUB/non-spine.xhtml'])
def test_native_final_qa_rejects_additional_pagebreak_outside_content_spine(orphan_member):
    docs,payloads=fixture()
    config=settings(('page_iv','iv'),('page_1','1'),('page_2','2'))
    pages.apply_page_navigation(docs,payloads,config)
    if orphan_member not in docs:docs[orphan_member]=document('<p>Non-spine content.</p>')
    ET.SubElement(docs[orphan_member].find(H+'body'),H+'span',{
        'id':'orphan-native',T:'pagebreak','title':'99',
        'role':'doc-pagebreak','aria-label':'Original page 99'})
    with pytest.raises(pages.PageNavigationError,match='outside the content spine'):
        pages.validate_page_navigation(docs,payloads,config)


def table_boundary_fixture(kind):
    docs, payloads = fixture()
    docs['EPUB/a.xhtml'] = document(
        '<p>Existing text<span id="page_a" epub:type="pagebreak" title="10"/> between'
        '<span id="page_b" epub:type="pagebreak" title="11"/> end. '
        '<a href="a.xhtml#page_a">Original page</a></p>'
    )
    table = ('<table class="data" data-origin="synthetic"><thead><tr><th>Item</th>'
             '<th>Value</th></tr></thead><tbody><tr><td><a href="#end">A</a></td>'
             '<td><em>B</em></td></tr></tbody></table>')
    group = (table.replace('<table ', '<table id="table-group" ', 1) if kind == 'table'
             else f'<{kind} id="table-group">{table}<p>Table note.</p>{table}</{kind}>')
    docs['EPUB/b.xhtml'] = document('<h1 id="body">Body</h1>' + group + '<p id="end">End.</p>')
    config = pages.PageNavigationConfig(
        'urn:sha256:' + 'a' * 64, 'Original EPUB', 'en',
        (pages.NativePageLocation('page_a', '10', 'table-group'),
         pages.NativePageLocation('page_b', '11', 'table-group')),
        mode='native-pagebreaks',
    )
    return docs, payloads, config


@pytest.mark.parametrize('kind', ['table', 'div', 'section'])
def test_whole_table_boundary_keeps_source_chain_and_cross_file_links(kind):
    docs, payloads, config = table_boundary_fixture(kind)
    before_text = {name: ''.join(docs[name].itertext()) for name in ('EPUB/a.xhtml', 'EPUB/b.xhtml')}
    tables = [ET.tostring(node) for node in docs['EPUB/b.xhtml'].iter(H + 'table')]
    result = pages.apply_page_navigation(docs, payloads, config)
    target = next(node for node in docs['EPUB/b.xhtml'].iter() if node.get('id') == 'table-group')
    parent = next(node for node in docs['EPUB/b.xhtml'].iter() if target in list(node))
    index = list(parent).index(target)
    points = list(parent)[index-2:index]
    assert [(point.get('id'), point.get('title')) for point in points] == [('page_a', '10'), ('page_b', '11')]
    assert all(point.tag == H + 'span' and point.get(T) == 'pagebreak' and len(point) == 0
               and point.text is None and not (point.tail or '').strip() for point in points)
    assert [ET.tostring(node) for node in docs['EPUB/b.xhtml'].iter(H + 'table')] == tables
    assert {name: ''.join(docs[name].itertext()) for name in before_text} == before_text
    assert next(docs['EPUB/a.xhtml'].iter(H + 'a')).get('href') == 'b.xhtml#page_a'
    lists = [node for node in docs['EPUB/nav.xhtml'].iter(H + 'nav') if node.get(T) == 'page-list']
    assert len(lists) == 1
    assert [(a.text, a.get('href')) for a in lists[0].iter(H + 'a')] == [
        ('10', 'b.xhtml#page_a'), ('11', 'b.xhtml#page_b')]
    assert pages.validate_page_navigation(docs, payloads, config) == result
    # QA binds the pagepoint chain to the actual object, not only the page-list.
    parent.insert(index, ET.Element(H + 'p'))
    with pytest.raises(pages.PageNavigationError, match='verified before_target'):
        pages.validate_page_navigation(docs, payloads, config)
    if kind != 'table':
        parent.remove(parent[index])
        target.insert(0, ET.Element(H + 'p'))
        with pytest.raises(pages.PageNavigationError, match='first-heading section'):
            pages.validate_page_navigation(docs, payloads, config)


def test_table_group_can_move_standard_empty_points_already_before_its_first_table():
    docs, payloads, config = table_boundary_fixture('div')
    original_parent = docs['EPUB/a.xhtml'].find(H + 'body/' + H + 'p')
    target = next(node for node in docs['EPUB/b.xhtml'].iter() if node.get('id') == 'table-group')
    points = [node for node in original_parent if node.get(T) == 'pagebreak']
    for index, point in enumerate(points):
        pages._remove_migrated_marker(docs['EPUB/a.xhtml'], point)
        point.tail = '\n'
        target.insert(index, point)
    tables = [ET.tostring(node) for node in target.iter(H + 'table')]
    result = pages.apply_page_navigation(docs, payloads, config)
    parent = next(node for node in docs['EPUB/b.xhtml'].iter() if target in list(node))
    index = list(parent).index(target)
    assert list(parent)[index-2:index] == points
    assert [ET.tostring(node) for node in target.iter(H + 'table')] == tables
    assert pages.validate_page_navigation(docs, payloads, config) == result


@pytest.mark.parametrize('position', ['first', 'middle'])
def test_normalizer_keeps_tail_of_emptied_pagepoint_paragraph(tmp_path, position):
    docs, payloads, _ = table_boundary_fixture('table')
    previous = '<em>Previous</em> between ' if position == 'middle' else ''
    tail = 'KEEP THIS INLINE TAIL. \n'
    docs['EPUB/a.xhtml'] = document(
        '<div id="carrier">Lead ' + previous
        + '<p><span id="page_a" epub:type="pagebreak" title="10"/></p>'
        + tail + '<p/> \t<p/>\n'
        + '<p id="text" lang="en">Visible text</p>'
        + '<p id="child" data-kind="empty-child"><em/></p></div>'
    )
    config = pages.PageNavigationConfig(
        'urn:sha256:' + 'a' * 64, 'Original EPUB', 'en',
        (pages.NativePageLocation('page_a', '10', 'table-group'),),
        mode='native-pagebreaks',
    )
    before_text = {name: ''.join(docs[name].itertext()) for name in ('EPUB/a.xhtml', 'EPUB/b.xhtml')}
    original_table = copy.deepcopy(next(docs['EPUB/b.xhtml'].iter(H + 'table')))
    source, output = tmp_path / 'input.epub', tmp_path / 'output.epub'
    with zipfile.ZipFile(source, 'w') as archive:
        for name, data in payloads.items():
            archive.writestr(name, data)
        for name, root in docs.items():
            archive.writestr(name, ET.tostring(root))

    translator.normalize_epub_navigation(source, output, page_navigation=config)
    with zipfile.ZipFile(output) as archive:
        final_payloads = {name: archive.read(name) for name in archive.namelist()}
    final_docs = {name: ET.fromstring(data) for name, data in final_payloads.items()
                  if name.endswith('.xhtml')}
    assert {name: ''.join(final_docs[name].itertext()) for name in before_text} == before_text
    carrier = next(node for node in final_docs['EPUB/a.xhtml'].iter() if node.get('id') == 'carrier')
    assert [node.get('id') for node in carrier if node.tag == H + 'p'] == ['text', 'child']
    if position == 'first':
        assert carrier.text == 'Lead ' + tail + ' \t\n'
    else:
        assert carrier.text == 'Lead '
        assert carrier[0].tag == H + 'em'
        assert carrier[0].tail == ' between ' + tail + ' \t\n'
    text = next(node for node in carrier if node.get('id') == 'text')
    child = next(node for node in carrier if node.get('id') == 'child')
    assert text.attrib == {'id': 'text', 'lang': 'en'} and text.text == 'Visible text'
    assert child.attrib == {'id': 'child', 'data-kind': 'empty-child'}
    assert len(child) == 1 and child[0].tag == H + 'em'
    target = next(node for node in final_docs['EPUB/b.xhtml'].iter() if node.get('id') == 'table-group')
    parent = next(node for node in final_docs['EPUB/b.xhtml'].iter() if target in list(node))
    point = parent[list(parent).index(target) - 1]
    assert (point.tag, point.get('id'), point.get(T), point.get('title')) == (
        H + 'span', 'page_a', 'pagebreak', '10')
    assert not point.text and not list(point)
    assert ET.tostring(target) == ET.tostring(original_table)
    result = pages.validate_page_navigation(final_docs, final_payloads, config)
    assert [label for label, _, _ in result.targets] == ['10']
    lists = [node for root in final_docs.values() for node in root.iter(H + 'nav')
             if node.get(T) == 'page-list']
    assert len(lists) == 1 and [node.text for node in lists[0].iter(H + 'a')] == ['10']


@pytest.mark.parametrize('mutation', [
    'row', 'cell', 'header', 'generic-div', 'leading-text', 'non-table-first',
    'code-class', 'wrapper-code-class', 'code-ancestor', 'missing', 'duplicate',
    'nested-table', 'point-in-table', 'point-in-code',
])
def test_table_boundary_rejects_false_or_unsafe_bindings_without_mutation(mutation):
    docs, payloads, config = table_boundary_fixture('div')
    body = docs['EPUB/b.xhtml'].find(H + 'body')
    target = next(node for node in body.iter() if node.get('id') == 'table-group')
    table = next(target.iter(H + 'table'))
    if mutation in {'row', 'cell', 'header'}:
        target.attrib.pop('id')
        tag = {'row': 'tr', 'cell': 'td', 'header': 'th'}[mutation]
        next(table.iter(H + tag)).set('id', 'table-group')
    elif mutation == 'generic-div':
        target.remove(table)
    elif mutation == 'leading-text':
        target.text = 'A real introduction is not part of the table boundary.'
    elif mutation == 'non-table-first':
        target.insert(0, ET.Element(H + 'p'))
    elif mutation == 'code-class':
        table.set('class', 'code-example')
    elif mutation == 'wrapper-code-class':
        target.set('class', 'binding-example')
    elif mutation == 'code-ancestor':
        body.remove(target)
        ET.SubElement(body, H + 'pre').append(target)
    elif mutation == 'missing':
        target.attrib.pop('id')
    elif mutation == 'duplicate':
        ET.SubElement(body, H + 'table', {'id': 'table-group'})
    elif mutation == 'nested-table':
        body.remove(target)
        outer = ET.SubElement(body, H + 'table')
        ET.SubElement(ET.SubElement(ET.SubElement(outer, H + 'tbody'), H + 'tr'), H + 'td').append(target)
    else:
        original = docs['EPUB/a.xhtml'].find(H + 'body/' + H + 'p')
        points = [node for node in original if node.get(T) == 'pagebreak']
        carrier = (next(table.iter(H + 'td')) if mutation == 'point-in-table' else
                   ET.SubElement(docs['EPUB/a.xhtml'].find(H + 'body'), H + 'pre'))
        for point in points:
            pages._remove_migrated_marker(docs['EPUB/a.xhtml'], point)
            point.tail = None
            carrier.append(point)
    before = {name: ET.tostring(root) for name, root in docs.items()}
    with pytest.raises(pages.PageNavigationError):
        pages.apply_page_navigation(docs, payloads, config)
    assert {name: ET.tostring(root) for name, root in docs.items()} == before


def pandoc_markdown_ast(source):
    result = subprocess.run(
        ['pandoc', '--from=markdown+fenced_divs+bracketed_spans+footnotes-raw_html',
         '--to=json', f'--lua-filter={ROOT / "formats/pandoc/semantics.lua"}'],
        input=source, text=True, encoding='utf-8', capture_output=True, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    return json.loads(result.stdout)


def pandoc_tables(value):
    if isinstance(value, dict):
        return ([value] if value.get('t') == 'Table' else
                [table for child in value.values() for table in pandoc_tables(child)])
    if isinstance(value, list):
        return [table for child in value for table in pandoc_tables(child)]
    return []


@pytest.mark.skipif(not shutil.which('pandoc'), reason='Pandoc required')
def test_real_writer_wrapper_preserves_complete_short_table_and_page_list(tmp_path):
    table = ('| Item | Value |\n| --- | --- |\n| [A](#end) | *B* |\n'
             '| C | [D]{.sample data-key="v"} |\n')
    marker_a = '[]{#page_a .source-page epub:type="pagebreak" role="doc-pagebreak" title="10"}'
    marker_b = '[]{#page_b .source-page epub:type="pagebreak" role="doc-pagebreak" title="11"}'
    baseline = '# Body {#body}\n\nExisting prose.\n\n' + table + '\n# End {#end}\n\nEnd.\n'
    wrapped = ('# Body {#body}\n\nExisting prose' + marker_a + marker_b + '. [Old page](#page_a)\n\n'
               '::: {#table-group .table-scroll data-kind="synthetic"}\n\n' + table +
               '\n:::\n\n# End {#end}\n\nEnd.\n')
    inline = baseline.replace('| [A](#end)', '| ' + marker_a + '[A](#end)')
    baseline_table, = pandoc_tables(pandoc_markdown_ast(baseline))
    wrapper_table, = pandoc_tables(pandoc_markdown_ast(wrapped))
    inline_table, = pandoc_tables(pandoc_markdown_ast(inline))
    assert wrapper_table == baseline_table  # Full Table, including every cell, link, and style.
    assert [spec[1]['t'] for spec in baseline_table['c'][2]] == ['ColWidthDefault', 'ColWidthDefault']
    assert [spec[1] for spec in inline_table['c'][2]] == [
        {'t': 'ColWidth', 'c': 0.5}, {'t': 'ColWidth', 'c': 0.5}]
    # Apart from the inserted empty point and the altered widths, every
    # cell/link/style in the negative input is the same as in the baseline.
    removed = []
    def strip_pagepoint(value):
        if isinstance(value, dict):
            return {key: strip_pagepoint(child) for key, child in value.items()}
        if isinstance(value, list):
            result = []
            for child in value:
                if isinstance(child, dict) and child.get('t') == 'Span' and child['c'][0][0] == 'page_a':
                    removed.append(child)
                else:
                    result.append(strip_pagepoint(child))
            return result
        return value
    inline_comparable = copy.deepcopy(inline_table)
    inline_comparable['c'][2] = copy.deepcopy(baseline_table['c'][2])
    assert strip_pagepoint(inline_comparable) == baseline_table
    assert len(removed) == 1 and removed[0]['c'][1] == []

    cover = tmp_path / 'cover.png'
    cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='))
    source_docs, source_payloads = fixture()
    with zipfile.ZipFile(tmp_path / 'original.epub', 'w') as original:
        for name, data in source_payloads.items(): original.writestr(name, data)
        for name, root in source_docs.items(): original.writestr(name, ET.tostring(root))
    manifest = write_config(tmp_path, [
        {'target': 'page_a', 'label': '10', 'before_target': 'table-group'},
        {'target': 'page_b', 'label': '11', 'before_target': 'table-group'},
    ])
    manifest['epub'].update(cover='cover.png', title_page=False, toc_depth=1, bodymatter='body')

    def build(text, name, with_pages):
        entry, output = tmp_path / (name + '.md'), tmp_path / (name + '.epub')
        entry.write_text(text, 'utf-8')
        selected = copy.deepcopy(manifest)
        if not with_pages:
            selected['epub'].pop('page_navigation')
        language = translator.Language('zh-CN', 'source', entry, None, {'epub': output})
        work = translator.Work(ROOT, tmp_path, selected, name, 'Table fixture', 'markdown',
                               None, tmp_path / 'original.epub', 0, (language,))
        translator.build_markdown(work, language, 'epub', output)
        with zipfile.ZipFile(output) as archive:
            payloads = {member: archive.read(member) for member in archive.namelist()}
        docs = {member: ET.fromstring(data) for member, data in payloads.items() if member.endswith('.xhtml')}
        return docs, payloads

    baseline_docs, _ = build(baseline, 'baseline', False)
    docs, payloads = build(wrapped, 'wrapped', True)
    config = pages.load_page_navigation_config(tmp_path, manifest, 'zh-CN')
    result = pages.validate_page_navigation(docs, payloads, config)
    assert [label for label, _, _ in result.targets] == ['10', '11']
    group = next(node for root in docs.values() for node in root.iter() if node.get('id') == 'table-group')
    assert group.tag == H + 'div' and group.get('class') == 'table-scroll' and group.get('data-kind') == 'synthetic'
    member = result.targets[0][1]
    parent = next(node for node in docs[member].iter() if group in list(node))
    index = list(parent).index(group)
    points = list(parent)[index-2:index]
    assert [(node.tag, node.get('id'), node.get(T), node.get('title'), node.get('role')) for node in points] == [
        (H+'span', 'page_a', 'pagebreak', '10', 'doc-pagebreak'),
        (H+'span', 'page_b', 'pagebreak', '11', 'doc-pagebreak')]
    assert all(not list(node) and not node.text and not (node.tail or '').strip() for node in points)
    original_table = next(node for root in baseline_docs.values() for node in root.iter(H + 'table'))
    final_table = next(group.iter(H + 'table'))
    original_table.tail = final_table.tail = None
    assert ET.tostring(original_table) == ET.tostring(final_table)
    lists = [node for root in docs.values() for node in root.iter(H+'nav') if node.get(T) == 'page-list']
    assert len(lists) == 1
    assert [node.text for node in lists[0].iter(H+'a')] == ['10', '11']
    assert not any(not ''.join(node.itertext()).strip() for root in docs.values() for node in root.iter(H+'p'))


@pytest.mark.skipif(not shutil.which('pandoc'),reason='Pandoc required')
def test_native_source_page_class_has_a_real_publisher_box_and_pagelist_jump(tmp_path):
    import os

    from tools import epub_browser_qa as qa
    from tools.browser_runtime import AgentBrowserSession

    output, _ = build_native_markdown_adapter_fixture(
        tmp_path, 'source-page', ordinary_provenance=True)
    unpacked = tmp_path / 'unpacked'
    members = qa.safe_extract(output, unpacked)
    docs = {name: ET.parse(unpacked/name).getroot() for name in members}
    target_member = next(name for name, root in docs.items()
                         if any(e.get('id') == 'page_1' for e in root.iter()))
    nav_member = next(name for name, root in docs.items()
                     if root.find(f'.//{H}nav[@{T}="page-list"]') is not None)
    point = next(e for e in docs[target_member].iter() if e.get('id') == 'page_1')
    parent = next(p for p in docs[target_member].iter() if point in list(p))
    expected = {
        'id': point.get('id'), 'classes': point.get('class'), 'title': point.get('title'),
        'role': point.get('role'), 'parentTag': parent.tag.rsplit('}',1)[-1],
        'index': list(parent).index(point), 'before': parent.text, 'tail': point.tail,
        'paragraph': ''.join(parent.itertext()),
    }
    assert expected == {
        'id': 'page_1', 'classes': 'source-page', 'title': '1', 'role': 'doc-pagebreak',
        'parentTag': 'p', 'index': 0, 'before': None, 'tail': 'Start.', 'paragraph': 'Start.',
    }
    harness = tmp_path / 'publisher-harness.html'
    qa.write_harness(harness, members, 390, 844, 10, qa.default_modes()[0],
                     base_prefix=unpacked.as_uri()+'/')
    probe = r'''
      window.nativeCssProbe = async () => {
        await loadPage(__NAV_URL__);
        const navigation = frame.contentDocument;
        const link = [...navigation.querySelectorAll('nav a[href]')].find((anchor) =>
          decodedFragment(anchor.href) === 'page_1');
        if (!link) throw new Error('Native page-list link missing');
        const clicked = await clickNavigation(link,
          {member: __TARGET_URL__, fragment: 'page_1'});
        const document = frame.contentDocument;
        const point = document.getElementById('page_1');
        const parent = point.parentElement;
        const legacy = document.getElementById('ordinary-provenance');
        const rect = point.getBoundingClientRect();
        return {
          clicked, url:document.location.href,
          mode:pageReaderSettings.mode,
          publisherCssDisabled:pageReaderSettings.publisherCssDisabled,
          publisherStylesRemaining:pageReaderSettings.publisherStyleNodesRemaining,
          native:{id:point.id,classes:point.getAttribute('class'),title:point.title,
            role:point.getAttribute('role'),parentTag:parent.localName,
            index:[...parent.childNodes].filter((node)=>node.nodeType===1).indexOf(point),
            before:point.previousSibling?.textContent || null,
            tail:point.nextSibling?.textContent,paragraph:parent.textContent},
          display:getComputedStyle(point).display,
          rectCount:point.getClientRects().length,
          position:{top:rect.top,bottom:rect.bottom,
            visible:rect.top<document.documentElement.clientHeight&&rect.bottom>=0},
          legacy:{id:legacy.id,classes:legacy.className,text:legacy.textContent,
            role:legacy.getAttribute('role'),display:getComputedStyle(legacy).display,
            rectCount:legacy.getClientRects().length}
        };
      };
    '''.replace('__NAV_URL__',json.dumps((unpacked/nav_member).as_uri())).replace(
        '__TARGET_URL__',json.dumps((unpacked/target_member).as_uri()))
    harness.write_text(harness.read_text('utf-8').replace('window.startQA = () => {',
        probe+'\nwindow.startQA = () => {'),'utf-8')
    keys = ('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC',
            'TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE',
            'AGENT_BROWSER_EXECUTABLE_PATH')
    environment = {key:value for key in keys if (value:=os.getenv(key)) is not None}
    runtime = AgentBrowserSession('native-css-regression','publisher-point',
                                  environment=environment,scratch=tmp_path/'runtime')
    evidence = {}
    try:
        opened = runtime.run(['open',harness.as_uri()],capture=False)
        assert opened.returncode == 0
        viewport = runtime.run(['set','viewport','390','844'])
        assert viewport.returncode == 0, viewport.stderr
        result = runtime.run(['eval','nativeCssProbe()'])
        assert result.returncode == 0, result.stderr
        evidence = qa.parse_json_output(result.stdout)
        assert evidence['mode'] == qa.PUBLISHER_MODE
        assert evidence['publisherCssDisabled'] is False
        assert evidence['publisherStylesRemaining'] > 0
        assert evidence['clicked'] is True
        assert evidence['url'] == (unpacked/target_member).as_uri()+'#page_1'
        assert evidence['native'] == expected
        assert evidence['display'] != 'none'
        assert evidence['rectCount'] > 0
        assert evidence['position']['visible'] is True
        assert evidence['legacy'] == {
            'id': 'ordinary-provenance', 'classes': 'source-page',
            'text': 'Legacy provenance', 'role': None, 'display': 'none', 'rectCount': 0,
        }
        screenshot = runtime.run(['screenshot',str(tmp_path/'publisher-page.png')])
        assert screenshot.returncode == 0, screenshot.stderr
    finally:
        closed = runtime.run(['close'])
        evidence['close_returncode'] = closed.returncode
        (tmp_path/'native-css-browser-evidence.json').write_text(
            json.dumps(evidence,ensure_ascii=False,indent=2),'utf-8')
        assert closed.returncode == 0, closed.stderr
