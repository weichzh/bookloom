from __future__ import annotations

import base64
import copy
import json
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_semantics as semantics
from tools import translator

H = "{http://www.w3.org/1999/xhtml}"
T = "{http://www.idpf.org/2007/ops}type"
ROOT = Path(__file__).resolve().parents[1]


def ast():
    return {"blocks": [{"t": "Header", "c": [1, ["chapter", [], []], [
        {"t": "Str", "c": "Heading"}, {"t": "Space"},
        {"t": "Superscript", "c": [{"t": "Str", "c": "2"}]}, {"t": "Space"},
        {"t": "Link", "c": [["ref56", [], [["epub:type", "noteref"]]],
                                  [{"t": "Superscript", "c": [{"t": "Str", "c": "56"}]}], ["#note56", ""]]},
    ]]}]}


def toc(label='Heading <sup>2</sup> <sup>56</sup>', href="body.xhtml#chapter"):
    return ET.fromstring(f'<nav xmlns="{H[1:-1]}"><ol><li><a href="{href}">{label}</a></li></ol></nav>')


def test_source_noteref_is_not_title_text_but_ordinary_superscript_is():
    assert semantics.outline_from_ast(ast())[0].label == "Heading 2"
    assert semantics.inline_text(ast()["blocks"][0]["c"][2], include_noterefs=True) == "Heading 2 56"


def test_controlled_nav_cleanup_is_idempotent_and_keeps_href_and_body_unchanged():
    nav = toc()
    body = ET.fromstring(f'<html xmlns="{H[1:-1]}" xmlns:epub="{T[1:-5]}"><body><section id="chapter">'
                        '<h1>Heading <sup>2</sup> <a id="ref56" href="#note56" epub:type="noteref"><sup>56</sup></a></h1>'
                        '<p id="note56">Complete note.</p></section></body></html>')
    before = ET.tostring(body)
    assert semantics.normalize_noteref_labels(ast(), nav) == 1
    assert semantics.normalize_noteref_labels(ast(), nav) == 0
    assert next(nav.iter(H + "a")).get("href") == "body.xhtml#chapter"
    assert ET.tostring(body) == before
    semantics.validate_outline(ast(), nav, nav_path="nav.xhtml", documents={"body.xhtml": body})
    heading = next(body.iter(H + "h1"))
    next(heading.iter(H + "a")).attrib.pop(T)
    with pytest.raises(semantics.EpubSemanticError, match="正文标题注引用数量与源标题不一致"):
        semantics.validate_outline(ast(), nav, nav_path="nav.xhtml", documents={"body.xhtml": body})


@pytest.mark.parametrize("label,href", [('Wrong 56', 'body.xhtml#chapter'),
                                         ('Heading 2 56', 'https://outside.test/#chapter')])
def test_cleanup_rejects_wrong_original_label_or_external_target(label, href):
    nav = toc(label, href)
    before = ET.tostring(nav)
    with pytest.raises(semantics.EpubSemanticError):
        semantics.normalize_noteref_labels(ast(), nav)
    assert ET.tostring(nav) == before


def test_cleanup_does_not_erase_native_pagepoint_clone():
    nav = toc()
    anchor = next(nav.iter(H + "a"))
    ET.SubElement(anchor, H + "span", {T: "pagebreak", "id": "page1"})
    before = ET.tostring(nav)
    with pytest.raises(semantics.EpubSemanticError, match="不能删除"):
        semantics.normalize_noteref_labels(ast(), nav)
    assert ET.tostring(nav) == before


def test_ordinary_link_or_digits_are_never_cleaned_as_noterefs():
    document = ast()
    document["blocks"][0]["c"][2][-1]["c"][0][2] = []
    nav = toc()
    before = ET.tostring(nav)
    assert semantics.normalize_noteref_labels(document, nav) == 0
    assert ET.tostring(nav) == before
    assert semantics.outline_from_ast(document)[0].label == "Heading 2 56"


def test_ncx_and_numbered_source_labels_follow_the_same_title():
    ncx = ET.fromstring('<ncx><navMap><navPoint><navLabel><text>1 Heading 2 56</text></navLabel>'
                        '<content src="body.xhtml#chapter"/></navPoint></navMap></ncx>')
    assert semantics.normalize_noteref_labels(ast(), ncx, number_sections=True, ncx=True) == 1
    assert ncx.find('.//text').text == "1 Heading 2"
    assert ncx.find('.//content').get('src') == "body.xhtml#chapter"


def test_heading_note_removal_keeps_text_after_the_reference():
    heading = ET.fromstring(f'<h2 xmlns:epub="{T[1:-5]}">Before <a epub:type="noteref">56</a> after <sup>2</sup>.</h2>')
    assert semantics.heading_text(heading) == "Before  after 2."


@pytest.mark.parametrize("mutation", ["label", "id", "target", "missing"])
def test_heading_note_gate_preserves_source_callout_identity_and_label(mutation):
    header = ET.fromstring(f'<h1 xmlns:epub="{T[1:-5]}">Heading <sup>2</sup> '
                          '<a id="ref56" href="notes.xhtml#note56" epub:type="noteref"><sup>56</sup></a></h1>')
    reference = next(header.iter('a'))
    semantics.validate_heading_noterefs(ast()['blocks'][0], header)
    if mutation == 'label': reference.find('sup').text = '57'
    elif mutation == 'id': reference.set('id', 'other-ref')
    elif mutation == 'target': reference.set('href', 'notes.xhtml#other-note')
    else: reference.attrib.pop(T)
    with pytest.raises(semantics.EpubSemanticError, match='正文标题注引用'):
        semantics.validate_heading_noterefs(ast()['blocks'][0], header)


def test_source_heading_noteref_without_explicit_id_keeps_generated_id_contract():
    document = ast()
    document['blocks'][0]['c'][2][-1]['c'][0][0] = ''
    header = ET.fromstring(f'<h1 xmlns:epub="{T[1:-5]}">Heading <sup>2</sup> '
                          '<a id="generated-ref" href="notes.xhtml#note56" epub:type="noteref">56</a></h1>')
    semantics.validate_heading_noterefs(document['blocks'][0], header)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_real_writer_keeps_original_heading_note_but_cleans_nav_and_ncx(tmp_path):
    entry, cover, output = tmp_path / 'main.md', tmp_path / 'cover.png', tmp_path / 'book.epub'
    cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='))
    entry.write_text('# Part {#part}\n\n## 7.4.3 Problem[^56^](#note56){#ref56 epub:type="noteref"} {#problem}\n\n'
                     'Unchanged body.\n\n## Notes {#notes .unlisted}\n\n'
                     '::: {#note56 epub:type="footnote" role="doc-footnote"}\n56. Complete original note.\n\n'
                     '[↩](#ref56){epub:type="backlink"}\n:::\n', encoding='utf-8')
    language = translator.Language('en', 'source', entry, None, {'epub': output})
    work = translator.Work(ROOT, tmp_path, {'epub': {'cover': 'cover.png', 'title_page': False,
                            'toc_depth': 2, 'bodymatter': 'part'}}, 'heading-note', 'Fixture', 'markdown',
                           None, tmp_path / 'source.pdf', 1, (language,))
    translator.build_markdown(work, language, 'epub', output)
    with zipfile.ZipFile(output) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml')}
        ncx = next(ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.ncx'))
    nav = next(node for doc in docs.values() for node in doc.iter(H + 'nav') if node.get(T) == 'toc')
    assert [e.label for e in semantics.outline_from_nav(nav)] == ['Part', '7.4.3 Problem']
    assert '7.4.3 Problem56' not in ''.join(ncx.itertext())
    assert '7.4.3 Problem' in ''.join(ncx.itertext())
    references = [node for doc in docs.values() for node in doc.iter(H + 'a') if node.get(T) == 'noteref']
    assert len(references) == 1 and references[0].get('id') == 'ref56'
    assert ''.join(references[0].itertext()) == '56'
    assert any(references[0] in list(heading.iter()) for doc in docs.values() for heading in doc.iter(H + 'h2'))
    qa = tmp_path / 'qa'; qa.mkdir()
    translator.qa_epub(work, language, output, qa)
    with zipfile.ZipFile(output) as archive:
        payloads = {name: archive.read(name) for name in archive.namelist()}
    for name, doc in docs.items():
        reference = next((node for node in doc.iter(H + 'a') if node.get('id') == 'ref56'), None)
        if reference is not None:
            next(reference.iter(H + 'sup')).text = '57'
            payloads[name] = ET.tostring(doc)
    wrong = tmp_path / 'wrong-body-label.epub'
    with zipfile.ZipFile(wrong, 'w') as archive:
        for name, value in payloads.items(): archive.writestr(name, value)
    with pytest.raises(translator.CliError, match='正文标题注引用'):
        translator.qa_epub(work, language, wrong, qa)

# Native Pandoc Note in a title: the writer numbers all native Notes per split.

_NATIVE_TEX = r'''\documentclass{book}
\begin{document}
\chapter{Alpha}
An ordinary note\footnote{Before title.}.
\section[First]{First\textsuperscript{2}\footnote{Original \emph{word}, 27 and arrow ↩.} and tail\footnote{Second complete note.}}
Body\footnote{Middle.}.
\subsection[Sub]{Sub\footnote{Subsection note.}}
\section[Second]{Second\footnote{Next section note.}}
More\footnote{After.}.
\chapter{Beta}
Before second chapter title\footnote{Other note.}.
\section[Third]{Third\footnote{Final note.}}
\end{document}
'''


def _native_documents(path):
    opf_ns = '{http://www.idpf.org/2007/opf}'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        documents = {n: ET.fromstring(archive.read(n)) for n in names if n.endswith('.xhtml')}
        opf_name = next(n for n in names if n.endswith('.opf'))
        opf = ET.fromstring(archive.read(opf_name))
    manifest = {item.get('id'): posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), item.get('href')))
                for item in opf.iter(opf_ns + 'item')}
    spine = [manifest[item.get('idref')] for item in opf.iter(opf_ns + 'itemref')]
    return documents, spine


def _native_toc(documents):
    return next(node for node in documents['EPUB/nav.xhtml'].iter(H + 'nav') if node.get(T) == 'toc')


def _native_validate(source, documents, spine, split):
    return semantics.validate_outline(
        source, _native_toc(documents), toc_depth=3, nav_path='EPUB/nav.xhtml',
        documents=documents, spine_paths=spine, number_sections=True, epub_identifiers=True,
        navigation_math_labels=True, native_note_split_level=split,
    )


@pytest.fixture(scope='module', params=[1, 2], ids=['split-1', 'split-2'])
def native_book(request, tmp_path_factory):
    split = request.param
    work = tmp_path_factory.mktemp(f'native-note-{split}')
    entry, cover, output, expanded, source_json = (work/'main.tex', work/'cover.png', work/'book.epub',
                                                    work/'expanded.tex', work/'source.json')
    entry.write_text(_NATIVE_TEX, encoding='utf-8')
    cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='))
    source_text = translator.latex_epub_source_text(entry, work)
    expanded.write_text(source_text, encoding='utf-8')
    command = translator.latex_epub_pandoc_command(root=ROOT, work_path=work, entry=entry, expanded=expanded)
    translator.run([*command, '--to=json', '-o', str(source_json)], cwd=work)
    source = json.loads(source_json.read_text(encoding='utf-8'))
    translator.build_latex_epub_source(
        root=ROOT, work_path=work, entry=entry, output=output, title='Native title notes',
        author='Author', language='en', identifier='urn:test:native-title-notes', cover=cover,
        structure_options=['--toc-depth=3', '--epub-title-page=false'], toc_depth=3, split_level=split,
    )
    documents, spine = _native_documents(output)
    return split, source, documents, spine, work


def _native_heading(documents, identifier):
    for name, root in documents.items():
        target = next((node for node in root.iter() if node.get('id') == identifier), None)
        if target is not None:
            heading = next((node for node in target if node.tag in {H+'h1', H+'h2', H+'h3'}), None)
            if heading is not None:
                return name, heading
    raise AssertionError(f'heading not found: {identifier}')


def _native_ref_and_note(documents, identifier='first2-and-tail'):
    name, heading = _native_heading(documents, identifier)
    ref = next(node for node in heading.iter(H+'a') if node.get(T) == 'noteref')
    note_id = ref.get('href')[1:]
    note = next(node for node in documents[name].iter(H+'aside') if node.get('id') == note_id)
    return name, heading, ref, note


def test_native_note_real_writer_numbering_and_bound_content(native_book):
    split, source, documents, spine, _ = native_book
    expected = semantics.native_heading_notes(source, split)
    assert [n for _, n in expected['first2-and-tail']] == ([2, 3] if split == 1 else [1, 2])
    assert [n for _, n in expected['third']] == ([2] if split == 1 else [1])
    _native_validate(source, documents, spine, split)
    _name, heading, ref, note = _native_ref_and_note(documents)
    n = 2 if split == 1 else 1
    assert (ref.get('id'), ref.get('href'), ''.join(ref.itertext())) == (f'fnref{n}', f'#fn{n}', str(n))
    assert any(node.tag == H+'sup' and ''.join(node.itertext()) == '2' for node in heading.iter())
    assert '27' in ''.join(note.itertext()) and '↩' in ''.join(note.itertext())
    assert any(node.get(T) == 'backlink' for node in note.iter(H+'a'))
    labels = [entry.label for entry in semantics.outline_from_nav(_native_toc(documents))]
    assert any(label.endswith('First2 and tail') for label in labels)
    assert all('First2' + str(n) not in label for label in labels)


@pytest.mark.parametrize('mutation', ['label', 'ref-id', 'target', 'existing-target', 'missing', 'content', 'truncate', 'position', 'arrow', 'number', 'superscript'])
def test_native_heading_note_rejects_real_writer_corruption(native_book, mutation):
    split, source, original, spine, _ = native_book
    documents = {name: ET.fromstring(ET.tostring(doc)) for name, doc in original.items()}
    _name, heading, ref, note = _native_ref_and_note(documents)
    if mutation == 'label':
        ref.text = '999'
    elif mutation == 'ref-id':
        ref.set('id', 'different-ref')
    elif mutation == 'target':
        ref.set('href', '#fn999')
    elif mutation == 'existing-target':
        other = [node for node in heading if node.tag == H+'a' and node.get(T) == 'noteref'][1]
        ref.set('href', other.get('href'))
    elif mutation == 'missing':
        index = list(heading).index(ref)
        if index: heading[index-1].tail = (heading[index-1].tail or '') + (ref.tail or '')
        else: heading.text = (heading.text or '') + (ref.tail or '')
        heading.remove(ref)
    elif mutation == 'content':
        note.find(H+'p').text += ' CHANGED'
    elif mutation == 'truncate':
        note.find(H+'p').text = 'Original '
        for child in list(note.find(H+'p')): note.find(H+'p').remove(child)
    elif mutation == 'position':
        second = [node for node in heading if node.tag == H+'a' and node.get(T) == 'noteref'][1]
        index = list(heading).index(ref)
        if index: heading[index-1].tail = (heading[index-1].tail or '') + (ref.tail or '')
        else: heading.text = (heading.text or '') + (ref.tail or '')
        ref.tail = None
        heading.remove(ref)
        heading.insert(list(heading).index(second), ref)
    elif mutation in {'arrow', 'number'}:
        old, new = ('↩', '') if mutation == 'arrow' else ('27', '28')
        found = False
        for node in note.iter():
            for attribute in ('text', 'tail'):
                value = getattr(node, attribute)
                if value and old in value:
                    setattr(node, attribute, value.replace(old, new, 1)); found = True; break
            if found: break
        assert found
    else:
        sup = next(node for node in heading.iter(H+'sup') if ''.join(node.itertext()) == '2')
        sup.text = '3'
    with pytest.raises(semantics.EpubSemanticError):
        _native_validate(source, documents, spine, split)


def test_native_note_requires_writer_context_without_affecting_legacy_calls(native_book):
    _split, source, documents, _spine, _ = native_book
    with pytest.raises(semantics.EpubSemanticError, match='实际 writer'):
        semantics.validate_outline(source, _native_toc(documents), number_sections=True)
    assert semantics.outline_from_ast(ast())[0].label == 'Heading 2'


def test_writer_split_option_follows_actual_pandoc_option():
    assert translator.native_note_writer_split_level([]) == 1
    assert translator.native_note_writer_split_level(['--split-level=2']) == 2
    assert translator.native_note_writer_split_level(['--split-level', '1', '--split-level=2']) == 2
    with pytest.raises(translator.CliError): translator.native_note_writer_split_level(['--split-level=0'])
    for options in (['--metadata=title:override'], ['--metadata', 'title=override'],
                    ['-M', 'title:override'], ['-Mtitle=override']):
        assert translator.native_note_writer_title_overridden(options)
    assert not translator.native_note_writer_title_overridden(['--metadata=author:Title'])


def test_native_and_explicit_link_notes_keep_separate_identities(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    header = next(node for node in source['blocks'] if node.get('t') == 'Header'
                  and node['c'][1][0] == 'first2-and-tail')
    header['c'][2].insert(0, {'t': 'Link', 'c': [['manual-ref', [], [['epub:type', 'noteref']]],
                                               [{'t': 'Str', 'c': '77'}], ['#manual-note', '']]})
    manual = {'t': 'Div', 'c': [['manual-note', [], [['epub:type', 'footnote']]], [
        {'t': 'Para', 'c': [{'t': 'Str', 'c': 'Explicit note.'}, {'t': 'Space'},
                            {'t': 'Link', 'c': [['', [], [['epub:type', 'backlink']]],
                                                [{'t': 'Str', 'c': '↩'}], ['#manual-ref', '']]}]}]]}
    source['blocks'].insert(source['blocks'].index(header) + 1, manual)
    beta = next(node for node in source['blocks'] if node.get('t') == 'Header'
                and node['c'][1][0] == 'beta')
    nested = {'t': 'Div', 'c': [['nested-wrapper', [], []], [
        {'t': 'Header', 'c': [2, ['nested-heading', [], []], [
            {'t': 'Str', 'c': 'Nested'},
            {'t': 'Note', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': 'Nested note.'}]}]},
        ]]},
    ]]}
    source['blocks'].insert(source['blocks'].index(beta), nested)
    source_json, output = work/'mixed.json', work/'mixed.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=work/'main.tex', source_cwd=work, output=output,
        source_command=['pandoc', str(source_json), '--from=json'], toc_depth=3,
        writer_options=['--toc', '--toc-depth=3', '--epub-title-page=false',
                        '--number-sections', f'--split-level={split}', '--metadata=title:Mixed'],
    )
    documents, spine = _native_documents(output)
    _native_validate(source, documents, spine, split)
    _name, heading = _native_heading(documents, 'first2-and-tail')
    refs = [node for node in heading.iter(H+'a') if node.get(T) == 'noteref']
    n = 2 if split == 1 else 1
    assert [(node.get('id'), ''.join(node.itertext())) for node in refs] == [
        ('manual-ref', '77'), (f'fnref{n}', str(n)), (f'fnref{n+1}', str(n+1))]
    assert [number for _, number in semantics.native_heading_notes(source, split)['nested-heading']] == ([8] if split == 1 else [1])
    labels = [entry.label for entry in semantics.outline_from_nav(_native_toc(documents))]
    assert any(label.endswith('First2 and tail') for label in labels)
    assert all('77First' not in label for label in labels)
    assert any(node.tag == H+'sup' and ''.join(node.itertext()) == '2' for node in heading.iter())


@pytest.fixture(scope='module')
def complex_native_book(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    header = next(node for node in source['blocks'] if node.get('t') == 'Header'
                  and node['c'][1][0] == 'first2-and-tail')
    note = next(node for node in header['c'][2] if node.get('t') == 'Note')
    note['c'] = [
        {'t': 'Para', 'c': [{'t': 'Str', 'c': 'First'}, {'t': 'Space'},
                            {'t': 'Emph', 'c': [{'t': 'Str', 'c': 'emphasis'}]},
                            {'t': 'Str', 'c': ' ↩'}]},
        {'t': 'Para', 'c': [{'t': 'Str', 'c': 'Second.'}]},
        {'t': 'BlockQuote', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': 'Quoted text'}]}]},
        {'t': 'BulletList', 'c': [[{'t': 'Plain', 'c': [{'t': 'Str', 'c': 'Alpha'}]}],
                                  [{'t': 'Plain', 'c': [{'t': 'Str', 'c': 'Beta'}]}]]},
        {'t': 'CodeBlock', 'c': [['', [], []], 'a = 1\nb = 2']},
    ]
    source_json, output = work/'complex.json', work/'complex.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=work/'main.tex', source_cwd=work, output=output,
        source_command=['pandoc', str(source_json), '--from=json'], toc_depth=3,
        writer_options=['--toc', '--toc-depth=3', '--epub-title-page=false',
                        '--number-sections', f'--split-level={split}', '--metadata=title:Complex'],
    )
    documents, spine = _native_documents(output)
    return split, source, documents, spine


@pytest.mark.parametrize('mutation', ['keep', 'delete-arrow', 'alter-quote', 'alter-list', 'alter-code'])
def test_native_note_full_structured_content_and_literal_arrow(complex_native_book, mutation):
    split, source, original, spine = complex_native_book
    documents = {name: ET.fromstring(ET.tostring(doc)) for name, doc in original.items()}
    _, _, _, aside = _native_ref_and_note(documents)
    if mutation == 'delete-arrow':
        for node in aside.iter():
            if node.tail and '↩' in node.tail: node.tail = node.tail.replace('↩', '', 1); break
    elif mutation == 'alter-quote':
        next(aside.iter(H+'blockquote')).find(H+'p').text = 'Wrong quote'
    elif mutation == 'alter-list':
        next(aside.iter(H+'li')).text = 'Wrong item'
    elif mutation == 'alter-code':
        next(aside.iter(H+'code')).text = 'a = 2\nb = 2'
    if mutation == 'keep':
        _native_validate(source, documents, spine, split)
    else:
        with pytest.raises(semantics.EpubSemanticError, match='完整注文'):
            _native_validate(source, documents, spine, split)


def test_native_note_below_toc_depth_is_still_bound(native_book):
    split, source, original, spine, _ = native_book
    documents = {name: ET.fromstring(ET.tostring(doc)) for name, doc in original.items()}
    nav = _native_toc(documents)
    for parent in list(nav.iter()):
        for child in list(parent):
            if child.tag == H+'li' and any(node.tag == H+'a' and node.get('href', '').endswith('#sub') for node in child):
                parent.remove(child)
    kwargs = {
        "toc_depth": 2, "nav_path": 'EPUB/nav.xhtml', "documents": documents, "spine_paths": spine,
        "number_sections": True, "epub_identifiers": True, "navigation_math_labels": True,
        "native_note_split_level": split,
    }
    semantics.validate_outline(source, nav, **kwargs)
    _, heading = _native_heading(documents, 'sub')
    reference = next(node for node in heading.iter(H+'a') if node.get(T) == 'noteref')
    reference.set('id', 'moved-or-corrupted')
    with pytest.raises(semantics.EpubSemanticError, match='writer 注号'):
        semantics.validate_outline(source, nav, **kwargs)


def test_native_note_list_start_and_style_are_preserved(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    header = next(node for node in source['blocks'] if node.get('t') == 'Header'
                  and node['c'][1][0] == 'first2-and-tail')
    note = next(node for node in header['c'][2] if node.get('t') == 'Note')
    note['c'].append({'t': 'OrderedList', 'c': [[3, {'t': 'LowerAlpha'}, {'t': 'Period'}], [
        [{'t': 'Plain', 'c': [{'t': 'Str', 'c': 'First item'}]}],
        [{'t': 'Plain', 'c': [{'t': 'Str', 'c': 'Second item'}]}],
    ]]})
    source_json, output = work/'ordered.json', work/'ordered.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=work/'main.tex', source_cwd=work, output=output,
        source_command=['pandoc', str(source_json), '--from=json'], toc_depth=3,
        writer_options=['--toc', '--toc-depth=3', '--epub-title-page=false',
                        '--number-sections', f'--split-level={split}', '--metadata=title:Ordered'],
    )
    documents, spine = _native_documents(output)
    _native_validate(source, documents, spine, split)
    _, _, _, aside = _native_ref_and_note(documents)
    ordered = next(aside.iter(H+'ol'))
    assert ordered.get('start') == '3' and ordered.get('type') == 'a'
    ordered.set('start', '4')
    with pytest.raises(semantics.EpubSemanticError, match='完整注文'):
        _native_validate(source, documents, spine, split)


def test_writer_chunk_counter_obeys_opaque_containers(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    note = lambda text: {'t': 'Note', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': text}]}]}
    header = lambda ident: {'t': 'Header', 'c': [2, [ident, [], []],
                                                [{'t': 'Str', 'c': ident}, note(ident + '-note')]]}
    para = lambda text: {'t': 'Para', 'c': [{'t': 'Str', 'c': text}, note(text + '-note')]}
    div = lambda ident, blocks, classes=(): {'t': 'Div', 'c': [[ident, list(classes), []], blocks]}
    before = next(i for i, node in enumerate(source['blocks'])
                  if node.get('t') == 'Header' and node['c'][0] == 2)
    source['blocks'][before:before] = [
        {'t': 'BlockQuote', 'c': [header('quoted-heading'), para('quoted-body')]},
        div('prose-div', [para('div-prose'), header('prose-heading')]),
        {'t': 'BulletList', 'c': [[header('list-heading'), para('list-body')]]},
        div('outer-div', [div('inner-div', [header('deep-heading'), para('deep-body')])]),
        div('two-div', [header('first-inner'), para('first-body'), header('second-inner')]),
        div('column-div', [header('column-heading')], ['column']),
        div('section-div', [header('section-heading'), para('section-body')]),
    ]
    source_json, output = work/'containers.json', work/'containers.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.run(['pandoc', str(source_json), '--from=json', '--to=epub3', '--standalone',
                    '--toc', '--number-sections', '--epub-title-page=false', f'--split-level={split}',
                    '--metadata=title:Containers', '-o', str(output)], cwd=work)
    documents, _ = _native_documents(output)
    expected = semantics.native_heading_notes(source, split)
    identifiers = ('quoted-heading', 'prose-heading', 'list-heading', 'deep-heading',
                   'first-inner', 'second-inner', 'column-heading', 'section-heading', 'first2-and-tail')
    for identifier in identifiers:
        heads = []
        for doc in documents.values():
            for node in doc.iter():
                if node.get('id') != identifier:
                    continue
                if node.tag in {H+'h1', H+'h2', H+'h3'}:
                    heads.append(node)
                else:
                    heads.extend(child for child in node if child.tag in {H+'h1', H+'h2', H+'h3'})
        assert len(heads) == 1, identifier
        refs = [node for node in heads[0].iter(H+'a') if node.get(T) == 'noteref']
        assert [(ref.get('id'), ref.get('href'), ''.join(ref.itertext())) for ref in refs] == [
            (f'fnref{n}', f'#fn{n}', str(n)) for _, n in expected[identifier]]
    if split == 2:
        assert [n for _, n in expected['quoted-heading']] == [2]
        assert [n for _, n in expected['section-heading']] == [1]


@pytest.mark.parametrize('override', [False, True], ids=['source-title', 'writer-title'])
def test_writer_implicit_h1_counts_title_note_before_first_header(native_book, override):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    note = {'t': 'Note', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': 'Title note'}]}]}
    source['meta']['title'] = {'t': 'MetaInlines', 'c': [{'t': 'Str', 'c': 'Synthetic title'}, note]}
    source['blocks'] = [
        {'t': 'Para', 'c': [{'t': 'Str', 'c': 'First paragraph'}]},
        {'t': 'Header', 'c': [2, ['first-source', [], []], [
            {'t': 'Str', 'c': 'Source heading'}, copy.deepcopy(note)]]},
    ]
    source_json, output = work/'implicit-h1.json', work/'implicit-h1.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.run(['pandoc', str(source_json), '--from=json', '--to=epub3', '--standalone',
                    '--epub-title-page=false', f'--split-level={split}',
                    *(['--metadata=title:Override'] if override else []), '-o', str(output)], cwd=work)
    documents, _ = _native_documents(output)
    _, heading = _native_heading(documents, 'first-source')
    reference = next(node for node in heading.iter(H+'a') if node.get(T) == 'noteref')
    expected = semantics.native_heading_notes(source, split, title_overridden=override)['first-source'][0][1]
    assert (reference.get('id'), reference.get('href'), ''.join(reference.itertext())) == (
        f'fnref{expected}', f'#fn{expected}', str(expected))
    assert expected == (2 if split == 1 and not override else 1)


def test_writer_counts_only_rendered_image_and_citation_inlines(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    note = lambda text: {'t': 'Note', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': text}]}]}
    image = {'t': 'Image', 'c': [['', [], []], [{'t': 'Str', 'c': 'alt'}, note('alt-note')],
                                  [str(work/'cover.png'), '']]}
    citation = {'t': 'Cite', 'c': [[{'citationId': 'x', 'citationPrefix': [note('hidden-prefix')],
                                    'citationSuffix': [], 'citationMode': {'t': 'NormalCitation'},
                                    'citationNoteNum': 0, 'citationHash': 0}],
                                   [{'t': 'Str', 'c': 'Visible'}, note('visible-note')]]}
    before = next(i for i, node in enumerate(source['blocks'])
                  if node.get('t') == 'Header' and node['c'][0] == 2)
    source['blocks'][before:before] = [{'t': 'Para', 'c': [image]}, {'t': 'Para', 'c': [citation]}]
    source_json, output = work/'hidden-inlines.json', work/'hidden-inlines.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.run(['pandoc', str(source_json), '--from=json', '--to=epub3', '--standalone',
                    '--epub-title-page=false', f'--split-level={split}', '-o', str(output)], cwd=work)
    documents, _ = _native_documents(output)
    _, heading = _native_heading(documents, 'first2-and-tail')
    refs = [node for node in heading.iter(H+'a') if node.get(T) == 'noteref']
    expected = [number for _, number in semantics.native_heading_notes(source, split)['first2-and-tail']]
    assert [(ref.get('id'), ref.get('href'), ''.join(ref.itertext())) for ref in refs] == [
        (f'fnref{n}', f'#fn{n}', str(n)) for n in expected]
    assert expected == ([3, 4] if split == 1 else [1, 2])


@pytest.mark.parametrize('kind', ['image', 'citation'])
def test_unrendered_source_note_inside_title_is_not_silently_accepted(native_book, kind):
    split, original, _, _, _ = native_book
    source = copy.deepcopy(original)
    header = next(node for node in source['blocks'] if node.get('t') == 'Header' and node['c'][0] == 1)
    note = {'t': 'Note', 'c': [{'t': 'Para', 'c': [{'t': 'Str', 'c': 'Not rendered'}]}]}
    if kind == 'image':
        hidden = {'t': 'Image', 'c': [['', [], []], [note], ['cover.png', '']]}
    else:
        hidden = {'t': 'Cite', 'c': [[{'citationId': 'x', 'citationPrefix': [note],
                                      'citationSuffix': [], 'citationMode': {'t': 'NormalCitation'},
                                      'citationNoteNum': 0, 'citationHash': 0}], [{'t': 'Str', 'c': 'Visible'}]]}
    header['c'][2].append(hidden)
    with pytest.raises(semantics.EpubSemanticError, match='不会由 EPUB writer 保留'):
        semantics.native_heading_notes(source, split)


@pytest.fixture(scope='module')
def linked_native_book(native_book):
    split, original, _, _, work = native_book
    source = copy.deepcopy(original)
    header = next(node for node in source['blocks'] if node.get('t') == 'Header'
                  and node['c'][1][0] == 'first2-and-tail')
    note = next(node for node in header['c'][2] if node.get('t') == 'Note')
    note['c'].append({'t': 'Para', 'c': [
        {'t': 'Link', 'c': [['', [], []], [{'t': 'Str', 'c': 'External source'}],
                            ['https://example.org/source?a=1&b=2', '']]},
        {'t': 'Space'},
        {'t': 'Link', 'c': [['', [], []], [{'t': 'Str', 'c': 'Other chapter'}], ['#third', '']]},
    ]})
    source_json, output = work/'linked-notes.json', work/'linked-notes.epub'
    source_json.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
    translator.build_svg_math_epub(
        root=ROOT, work_path=work, entry=work/'main.tex', source_cwd=work, output=output,
        source_command=['pandoc', str(source_json), '--from=json'], toc_depth=3,
        writer_options=['--toc', '--toc-depth=3', '--epub-title-page=false',
                        '--number-sections', f'--split-level={split}', '--metadata=title:Linked'],
    )
    documents, spine = _native_documents(output)
    return split, source, documents, spine


@pytest.mark.parametrize('mutation', ['keep', 'external-target', 'internal-valid-target', 'remove-link'])
def test_native_note_source_hyperlink_targets_are_preserved(linked_native_book, mutation):
    split, source, original, spine = linked_native_book
    documents = {name: ET.fromstring(ET.tostring(doc)) for name, doc in original.items()}
    _, _, _, aside = _native_ref_and_note(documents)
    links = [node for node in aside.iter(H+'a') if node.get(T) != 'backlink']
    assert len(links) == 2
    if mutation == 'external-target':
        links[0].set('href', 'https://example.org/other?a=1&b=2')
    elif mutation == 'internal-valid-target':
        links[1].set('href', '#first2-and-tail')
    elif mutation == 'remove-link':
        links[0].tag = H+'span'
        links[0].attrib.pop('href')
    if mutation == 'keep':
        _native_validate(source, documents, spine, split)
    else:
        with pytest.raises(semantics.EpubSemanticError, match='正文链接'):
            _native_validate(source, documents, spine, split)
