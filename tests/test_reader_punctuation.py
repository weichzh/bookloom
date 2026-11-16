from __future__ import annotations

import html
import json
import shutil
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from tools import epub_browser_qa as qa
from tools.browser_runtime import AgentBrowserSession

ROOT=Path(__file__).resolve().parents[1]
TEMP=ROOT/'.tmp'/'tests'
TEMP.mkdir(parents=True,exist_ok=True)


class ReaderPunctuationTests(unittest.TestCase):
    def test_publisher_spacing_preserves_text_and_real_overflow_gate(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / 'formats' / 'epub' / 'book.css', root / 'book.css')
            title = '纸桥研究：虚构天空与花园（《飞行手册》12–36 章）'
            paragraph = '正文与标点（包括闭括号）保持原样。'
            program = '\tgit merge --no-ff ' + ('long-branch-name-' * 12) + '\n  git status --short'
            common = (
                '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">'
                '<head><title>Publisher spacing fixture</title>'
                '<link rel="stylesheet" type="text/css" href="book.css"/></head>'
                '<body><section><h1 id="title">' + html.escape(title) + '</h1>'
                '<p id="paragraph">' + html.escape(paragraph) + '</p>'
                '<pre><code id="program">' + html.escape(program) + '</code></pre>'
            )
            positive = root / 'publisher.xhtml'
            overwide = root / 'overwide.xhtml'
            normal = root / 'normal.xhtml'
            positive.write_text(common + '</section></body></html>', encoding='utf-8')
            normal.write_text(
                common.replace('</head>', '<style>body, h1 { text-spacing-trim: normal !important; }</style></head>')
                + '</section></body></html>', encoding='utf-8',
            )
            overwide.write_text(
                common + '<div id="wide" style="width:900px">过宽对象</div>'
                '</section></body></html>', encoding='utf-8',
            )
            mode = qa.default_modes()[0]
            harness = root / 'harness.html'
            qa.write_harness(
                harness, [positive.name, normal.name, overwide.name], 390, 844, 10, mode,
                base_prefix=root.as_uri() + '/',
            )
            browser = AgentBrowserSession(
                'reader-punctuation', 'publisher-' + str(time.time_ns()),
                root=ROOT, scratch=root / 'runtime',
            )

            def command(args, capture=True):
                result = browser.run(args, capture=capture)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            def evaluate(script):
                return qa.parse_json_output(command(['eval', script]))

            try:
                command(['open', 'about:blank'], capture=False)
                command(['set', 'viewport', '390', '844'])
                command(['set', 'media', mode['color_scheme']])
                command(['open', harness.as_uri()])
                result = evaluate('window.qaShowPage(' + json.dumps(positive.as_uri()) + ')')
                detail = evaluate(r'''(() => {
                  const d = frame.contentDocument, h = d.getElementById('title');
                  return {title: h.textContent, paragraph: d.getElementById('paragraph').textContent,
                    program: d.getElementById('program').textContent,
                    bodyTrim: getComputedStyle(d.body).textSpacingTrim,
                    headingTrim: getComputedStyle(h).textSpacingTrim,
                    headingClient: h.clientWidth, headingScroll: h.scrollWidth,
                    documentClient: d.documentElement.clientWidth,
                    documentScroll: d.documentElement.scrollWidth};
                })()''')
                self.assertEqual(detail['title'], title)
                self.assertEqual(detail['paragraph'], paragraph)
                self.assertEqual(detail['program'], program)
                self.assertEqual(detail['bodyTrim'], 'space-all')
                self.assertEqual(detail['headingTrim'], 'space-all')
                self.assertLessEqual(detail['headingScroll'], detail['headingClient'] + 1)
                self.assertLessEqual(detail['documentScroll'], detail['documentClient'] + 1)
                self.assertFalse(result['readerSettings']['publisherCssDisabled'])
                self.assertGreater(result['readerSettings']['publisherStyleNodesRemaining'], 0)
                self.assertEqual(
                    qa.result_failures({'errors': [], 'results': [result]},
                                       [positive.as_uri()], 390, 844, mode), [],
                )

                # The same title must reproduce the old closing-punctuation
                # overflow with normal spacing, and the page QA must reject it.
                normal_result = evaluate('window.qaShowPage(' + json.dumps(normal.as_uri()) + ')')
                normal_detail = evaluate("getComputedStyle(frame.contentDocument.getElementById('title')).textSpacingTrim")
                self.assertEqual(normal_detail, 'normal')
                self.assertGreater(normal_result['overflowWidth'], 1)
                normal_failures = qa.result_failures(
                    {'errors': [], 'results': [normal_result]}, [normal.as_uri()], 390, 844, mode,
                )
                self.assertTrue(any('overflowCount=' in item for item in normal_failures), normal_failures)

                wide = evaluate('window.qaShowPage(' + json.dumps(overwide.as_uri()) + ')')
                failures = qa.result_failures({'errors': [], 'results': [wide]},
                                              [overwide.as_uri()], 390, 844, mode)
                self.assertGreater(wide['overflowWidth'], 1)
                self.assertTrue(any('overflowCount=' in failure for failure in failures), failures)
                self.assertTrue(any(item['selector'] == 'div#wide'
                                    for item in wide['overflowElements']))
            finally:
                closed = browser.run(['close'])
                self.assertEqual(closed.returncode, 0, closed.stderr)

    def test_spacing_is_required_only_with_publisher_css_disabled(self):
        actual={'actualColorScheme':'light','publisherCssDisabled':True,
                'userFontApplied':True,'userFontSizeApplied':True,
                'reflowWrapApplied':True,'mediaFittingApplied':True,
                'codeScrollingApplied':True,'fullwidthPunctuationApplied':False}
        state={'errors':[],'results':[{'path':'a.xhtml','viewport':[390,844],
                                      'readerSettings':actual}]}
        failures=qa.result_failures(state,['a.xhtml'],390,844,qa.default_modes()[2])
        self.assertTrue(any('fullwidthPunctuationApplied=false' in item for item in failures))
        actual['fullwidthPunctuationApplied']=True
        for value in (None, 'normal', 'space-all'):
            with self.subTest(actualTextSpacingTrim=value):
                if value is None:
                    actual.pop('actualTextSpacingTrim', None)
                else:
                    actual['actualTextSpacingTrim'] = value
                failures=qa.result_failures(state,['a.xhtml'],390,844,qa.default_modes()[2])
                self.assertEqual(
                    any('fullwidthPunctuationApplied' in item for item in failures),
                    value != 'space-all',
                )
        actual['fullwidthPunctuationApplied']=False
        failures=qa.result_failures(state,['a.xhtml'],390,844,qa.default_modes()[0])
        self.assertFalse(any('fullwidthPunctuationApplied' in item for item in failures))

    def test_real_reader_keeps_fullwidth_closing_punctuation_and_complete_code(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root=Path(directory)
            program='\tgit merge --no-ff '+('long-branch-name-'*12)+'\n  git status --short'
            markup=('<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">'
                    '<head><title>Reader spacing fixture</title>'
                    '<style>body { text-spacing-trim:normal; }</style></head>'
                    '<body><p>合并签名标签（合并标签）</p><pre><code>'
                    +html.escape(program)+'</code></pre></body></html>')
            epub=root/'fixture.epub'
            with zipfile.ZipFile(epub,'w') as archive:
                archive.writestr('mimetype','application/epub+zip',compress_type=zipfile.ZIP_STORED)
                archive.writestr('EPUB/text/body.xhtml',markup)
            result=qa.run_browser_qa(epub,root/'browser',timeout=60,page_timeout=10,
                                     screenshots=0,mode=qa.READER_LARGE_PRINT_MODE)
            self.assertEqual(result['failures'],[])
            state=json.loads((root/'browser'/qa.READER_LARGE_PRINT_MODE/'results.json').read_text(encoding='utf8'))
            record=state['results'][0]
            self.assertEqual(record['readerSettings']['actualTextSpacingTrim'],'space-all')
            self.assertTrue(record['readerSettings']['fullwidthPunctuationApplied'])
            self.assertEqual(record['overflowCount'],0)
            self.assertEqual(record['documentScrollWidth'],390)
            self.assertEqual(record['bodyScrollWidth'],374)
            # run_browser_qa also compares complete code text and whitespace to XHTML.
            self.assertEqual(result['modes'][0]['code_block_count'],1)


if __name__=='__main__':
    unittest.main()
