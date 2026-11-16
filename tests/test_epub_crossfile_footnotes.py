from __future__ import annotations

import base64
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from tools import epub_browser_qa
from tools import epub_footnotes as notes

# isort: split
from tools import translator

ROOT = Path(__file__).resolve().parents[1]
XHTML = "http://www.w3.org/1999/xhtml"
NS = {"h": XHTML}


def document(body):
    return ET.fromstring(f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Notes fixture</title></head><body>{body}</body></html>')


class CrossfileFootnoteTests(unittest.TestCase):
    def documents(self):
        return {
            "EPUB/text/body.xhtml": document('<h1>Body</h1><p>Text <a id="r:1" epub:type="noteref" href="../notes/notes%20file.xhtml#n%3A1">1</a>.</p><p id="after">Continue after returning.</p>'),
            "EPUB/notes/notes file.xhtml": document('<h1>Notes</h1><div id="n:1" epub:type="footnote" role="doc-footnote"><p><span class="source-page" data-page="4"/>1. A <em>single</em> note.</p></div>'),
        }

    def test_promotes_in_place_without_copying_moving_or_losing_content(self):
        docs = self.documents()
        root = docs["EPUB/notes/notes file.xhtml"]
        note = root.find('.//h:div', NS)
        paragraph = note[0]
        before = ET.tostring(paragraph)
        position = list(root.find('h:body', NS)).index(note)
        self.assertEqual(notes.normalize_footnotes(docs), 1)
        self.assertIs(list(root.find('h:body', NS))[position], note)
        self.assertEqual(note.tag, f"{{{XHTML}}}aside")
        self.assertIs(note[0], paragraph)
        self.assertEqual(ET.tostring(paragraph), before)
        self.assertEqual(list(note)[-1][0].get('href'), '../text/body.xhtml#r%3A1')
        normalized = {name: ET.tostring(doc) for name, doc in docs.items()}
        self.assertEqual(notes.normalize_footnotes(docs), 0)
        self.assertEqual({name: ET.tostring(doc) for name, doc in docs.items()}, normalized)

    def test_legacy_local_notes_and_missing_reference_id_remain_usable(self):
        doc = document('<p><a epub:type="noteref" href="#n1">1</a><a id="n1-ref"/></p><aside id="n1" epub:type="footnote"><p>Note.</p></aside>')
        docs = {'EPUB/body.xhtml': doc}
        notes.normalize_footnotes(docs)
        reference = doc.find('.//h:a', NS)
        self.assertEqual(reference.get('id'), 'n1-ref-2')
        self.assertEqual(doc.find('.//h:aside/h:p[@class="footnote-backlink"]/h:a', NS).get('href'), '#n1-ref-2')
        before = ET.tostring(doc)
        translator.add_epub_footnote_backlinks(doc)
        notes.validate_footnotes(docs)
        self.assertEqual(ET.tostring(doc), before)

    def test_wrong_external_escaping_missing_ambiguous_and_non_note_targets_fail(self):
        invalid = [
            'https://example.org/notes.xhtml#n:1', '//example.org/#n:1',
            '../../../notes.xhtml#n:1', '/EPUB/notes/notes%20file.xhtml#n:1',
            '..%2f..%2f..%2fnotes.xhtml#n:1', '..%5cnotes.xhtml#n:1',
            '../notes/notes%20file.xhtml', '../notes/missing.xhtml#n:1',
            '../notes/notes%20file.xhtml#absent', '../notes/notes%20file.xhtml?x=1#n:1',
            '../notes/notes%20file.xhtml#bad%escape', '../notes/notes%20file.xhtml#%FF',
        ]
        for href in invalid:
            with self.subTest(href=href):
                docs = self.documents()
                docs['EPUB/text/body.xhtml'].find('.//h:a', NS).set('href', href)
                with self.assertRaises(notes.FootnoteError): notes.normalize_footnotes(docs)
        for mutation in ('duplicate', 'paragraph'):
            with self.subTest(mutation=mutation):
                docs = self.documents()
                target = docs['EPUB/notes/notes file.xhtml'].find('.//h:div', NS)
                if mutation == 'duplicate': ET.SubElement(docs['EPUB/notes/notes file.xhtml'], 'p', {'id': 'n:1'})
                else: target.tag = f'{{{XHTML}}}p'
                with self.assertRaises(notes.FootnoteError): notes.normalize_footnotes(docs)

    def test_same_fragment_in_other_member_is_not_a_backlink(self):
        for href in ('../text/decoy.xhtml#r%3A1', 'https://example.org/#r:1', '../../../escape.xhtml#r:1'):
            with self.subTest(href=href):
                docs = self.documents()
                notes.normalize_footnotes(docs)
                docs['EPUB/text/decoy.xhtml'] = document('<a id="r:1" epub:type="noteref" href="../notes/notes%20file.xhtml#other"/><aside id="other" epub:type="footnote"/>')
                docs['EPUB/notes/notes file.xhtml'].find('.//h:a', NS).set('href', href)
                with self.assertRaises(notes.FootnoteError): notes.validate_footnotes(docs)
                with self.assertRaises(notes.FootnoteError): notes.normalize_footnotes(docs)

    def test_multiple_references_require_their_own_precise_returns(self):
        docs = self.documents()
        body = docs['EPUB/text/body.xhtml'].find('h:body', NS)
        ET.SubElement(body, f'{{{XHTML}}}a', {'id': 'r:2', notes.EPUB_TYPE: 'noteref', 'href': '../notes/notes%20file.xhtml#n%3A1'}).text = '1 again'
        notes.normalize_footnotes(docs)
        links = list(docs['EPUB/notes/notes file.xhtml'].iter(f'{{{XHTML}}}a'))
        self.assertEqual([item.get('href') for item in links], ['../text/body.xhtml#r%3A1', '../text/body.xhtml#r%3A2'])
        links[1].set('href', links[0].get('href'))
        with self.assertRaises(notes.FootnoteError): notes.validate_footnotes(docs)

    def test_normalize_seam_preserves_legacy_split_repair_but_rejects_network_targets(self):
        scratch = ROOT / '.tmp' / 'tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            directory = Path(directory)
            for href in ('#n%3A1', '//outside.example#n%3A1', '?query#n%3A1', '#n%ZZ', '#%FF'):
                with self.subTest(href=href):
                    docs = self.documents()
                    docs['EPUB/text/body.xhtml'].find('.//h:a', NS).set('href', href)
                    if href in ('#n%ZZ', '#%FF'):
                        docs['EPUB/notes/notes file.xhtml'].find('.//h:div', NS).set('id', 'n%ZZ' if href == '#n%ZZ' else '\ufffd')
                    source, output = directory / 'raw.epub', directory / 'normalized.epub'
                    with zipfile.ZipFile(source, 'w') as archive:
                        for member, doc in docs.items(): archive.writestr(member, ET.tostring(doc))
                    if href != '#n%3A1':
                        with self.assertRaises(translator.CliError):
                            translator.normalize_epub_navigation(source, output)
                    else:
                        translator.normalize_epub_navigation(source, output)
                        with zipfile.ZipFile(output) as archive:
                            normalized = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()}
                        notes.validate_footnotes(normalized)
                        reference = normalized['EPUB/text/body.xhtml'].find('.//h:a', NS)
                        self.assertIn('../notes/notes file.xhtml#', reference.get('href'))

    @unittest.skipUnless(shutil.which('pandoc'), 'pandoc required')
    def test_real_markdown_adapter_and_qa_accept_concentrated_native_notes(self):
        scratch = ROOT / '.tmp' / 'tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            directory = Path(directory)
            entry, cover, output = directory / 'main.md', directory / 'cover.png', directory / 'book.epub'
            cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII='))
            entry.write_text('---\nlang: en\n---\n# Body {#body epub:type="bodymatter"}\n\nText [1](#note-1){#ref-1 epub:type="noteref"}.\n\n# Notes {#notes}\n\n## Chapter one\n\n::: {#note-1 epub:type="footnote" role="doc-footnote"}\n1. A *single* note. [↩](#ref-1){epub:type="backlink"}\n:::\n', encoding='utf-8')
            language = translator.Language('en', 'source', entry, None, {'epub': output})
            work = translator.Work(ROOT, directory, {'epub': {'cover': 'cover.png', 'title_page': False, 'toc_depth': 1}}, 'crossfile-test', 'Concentrated notes fixture', 'markdown', None, directory / 'source.pdf', 1, (language,))
            translator.build_markdown(work, language, 'epub', output)
            evidence = directory / 'qa'
            evidence.mkdir()
            translator.qa_epub(work, language, output, evidence)
            with zipfile.ZipFile(output) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
            docs = {name: ET.fromstring(value) for name, value in payloads.items() if name.endswith('.xhtml')}
            references = [(name, node) for name, doc in docs.items() for node in doc.iter() if node.get(notes.EPUB_TYPE) == 'noteref']
            note_nodes = [(name, node) for name, doc in docs.items() for node in doc.iter() if node.get(notes.EPUB_TYPE) == 'footnote']
            self.assertEqual(len(note_nodes), 1)
            self.assertNotEqual(references[0][0], note_nodes[0][0])
            self.assertIn('single', ''.join(note_nodes[0][1].itertext()))
            for mutation in ('missing-note', 'external-note', 'wrong-return'):
                with self.subTest(mutation=mutation):
                    mutated = {name: ET.fromstring(value) for name, value in payloads.items() if name.endswith('.xhtml')}
                    reference = next(node for doc in mutated.values() for node in doc.iter() if node.get(notes.EPUB_TYPE) == 'noteref')
                    if mutation == 'missing-note': reference.set('href', 'absent.xhtml#note-1')
                    elif mutation == 'external-note': reference.set('href', 'https://example.org/#note-1')
                    else:
                        backlink = next(node for doc in mutated.values() for node in doc.iter() if node.get(notes.EPUB_TYPE) == 'backlink')
                        backlink.set('href', references[0][0].rsplit('/', 1)[-1] + '#body')
                    candidate = directory / (mutation + '.epub')
                    with zipfile.ZipFile(candidate, 'w') as archive:
                        for name, data in payloads.items():
                            archive.writestr(name, ET.tostring(mutated[name]) if name in mutated else data, compress_type=zipfile.ZIP_STORED if name == 'mimetype' else zipfile.ZIP_DEFLATED)
                    with self.assertRaises(translator.CliError): translator.qa_epub(work, language, candidate, evidence)

    def test_normalize_seam_adds_only_one_local_return_for_encoded_reference_id(self):
        scratch = ROOT / '.tmp' / 'tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            directory = Path(directory)
            source, output = directory / 'raw.epub', directory / 'normalized.epub'
            doc = document('<p><a id="r:1" epub:type="noteref" href="#n1">1</a></p><aside id="n1" epub:type="footnote"><p>Note.</p></aside>')
            with zipfile.ZipFile(source, 'w') as archive: archive.writestr('EPUB/local.xhtml', ET.tostring(doc))
            translator.normalize_epub_navigation(source, output)
            with zipfile.ZipFile(output) as archive: result = ET.fromstring(archive.read('EPUB/local.xhtml'))
            backlinks = [node for node in result.iter() if node.get(notes.EPUB_TYPE) == 'backlink']
            self.assertEqual(len(backlinks), 1)
            self.assertEqual(backlinks[0].get('href'), '#r%3A1')
            notes.validate_footnotes({'EPUB/local.xhtml': result})

    def test_real_browser_round_trip_loads_two_documents_and_resumes_original(self):
        scratch = ROOT / '.tmp' / 'tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            directory = Path(directory)
            docs = self.documents()
            notes.normalize_footnotes(docs)
            # Body sorts before Notes, forcing a target prefetch and original-page
            # reload before real clicks (the stale/full-URL regression path).
            docs = {'EPUB/a-body.xhtml': docs['EPUB/text/body.xhtml'],
                    'EPUB/z-notes file.xhtml': docs['EPUB/notes/notes file.xhtml']}
            docs['EPUB/a-body.xhtml'].find('.//h:a', NS).set('href', 'z-notes%20file.xhtml#n%3A1')
            docs['EPUB/z-notes file.xhtml'].find('.//h:a', NS).set('href', 'a-body.xhtml#r%3A1')
            # Long plain text makes the pre-override hash offset observably wrong.
            for doc in docs.values():
                body = doc.find('h:body', NS)
                for _ in range(40):
                    para = ET.Element(f'{{{XHTML}}}p')
                    para.text = 'Reader layout changes the line wrapping and target position. ' * 5
                    body.insert(0, para)
            epub = directory / 'crossfile.epub'
            with zipfile.ZipFile(epub, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
                for member, doc in docs.items(): archive.writestr(member, ET.tostring(doc))
            for mode in (epub_browser_qa.PUBLISHER_MODE, epub_browser_qa.READER_LARGE_PRINT_MODE):
                with self.subTest(mode=mode):
                    result = epub_browser_qa.run_browser_qa(epub, directory / mode, timeout=60, page_timeout=10, screenshots=0, mode=mode)
                    self.assertEqual(result['failures'], [])
                    state = json.loads((directory / mode / mode / 'results.json').read_text(encoding='utf-8'))
                    body = next(item for item in state['results'] if item['footnoteRefCount'])
                    self.assertEqual(body['noterefNavigationChecks'], 1)
                    self.assertEqual(body['backlinkNavigationChecks'], 1)
                    self.assertEqual(len(body['footnoteNavigation']), 1)
                    journey = body['footnoteNavigation'][0]
                    self.assertNotEqual(journey['source'], journey['target'])
                    self.assertEqual(journey['returned'], journey['source'])
                    self.assertGreater(body['textLength'], 20)
                    self.assertTrue(body['url'].endswith('/EPUB/a-body.xhtml'))

                    self.assertTrue(journey['targetPosition']['visible'])
                    self.assertTrue(journey['returnPosition']['visible'])


    def test_empty_page_fragment_is_visible_after_large_print_layout(self):
        scratch = ROOT / '.tmp' / 'tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            directory = Path(directory)
            paragraphs = '<p>Reader line wrapping before the exact page boundary. ' + ('More text. ' * 20) + '</p>'
            body = '<h1>Book start</h1><h2>Heading with fractional default margins</h2>' + paragraphs * 35
            body += '<span id="page-1"></span><p>First text on the requested source page.</p>' + paragraphs * 5 + '<span id="page-2"></span><p>Next source page.</p>' + paragraphs * 5
            epub = directory / 'page-fragment.epub'
            with zipfile.ZipFile(epub, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
                archive.writestr('EPUB/body.xhtml', ET.tostring(document(body)))
            original_write = epub_browser_qa.write_harness

            def probe_harness(*args, **kwargs):
                paths = original_write(*args, **kwargs)
                path = args[0]
                probe = '''<script>
                  window.startQA = () => {
                    (async () => {
                      await loadPage(pages[0] + '#page-1');
                      const first = fragmentPosition(frame.contentDocument.getElementById('page-1'));
                      const previous = frame.contentDocument;
                      await loadPage(pages[0] + '#page-2');
                      const sameDocument = frame.contentDocument === previous;
                      const second = fragmentPosition(frame.contentDocument.getElementById('page-2'));
                      await qaShowPage(pages[0] + '#page-1');
                      const shown = fragmentPosition(frame.contentDocument.getElementById('page-1'));
                      await runQA();
                      state.results[0].fragmentProbe = { first, second, shown, sameDocument };
                    })().catch((error) => { state.errors.push(String(error)); state.done = true; });
                    return true;
                  };
                </script>'''
                path.write_text(path.read_text(encoding='utf-8') + probe, encoding='utf-8')
                return paths

            with mock.patch.object(epub_browser_qa, 'write_harness', side_effect=probe_harness):
                result = epub_browser_qa.run_browser_qa(epub, directory / 'browser', timeout=60, page_timeout=10, screenshots=0, mode=epub_browser_qa.READER_LARGE_PRINT_MODE)
            self.assertEqual(result['failures'], [])
            state = json.loads((directory / 'browser' / epub_browser_qa.READER_LARGE_PRINT_MODE / 'results.json').read_text(encoding='utf-8'))
            probe = state['results'][0]['fragmentProbe']
            self.assertTrue(probe['sameDocument'])
            for key in ('first', 'second', 'shown'):
                with self.subTest(stage=key):
                    position = probe[key]
                    self.assertTrue(position['visible'])
                    self.assertEqual(position['top'], position['bottom'])
                    self.assertGreaterEqual(position['top'], 0)
                    self.assertLess(position['top'], 1)


if __name__ == '__main__':
    unittest.main()
