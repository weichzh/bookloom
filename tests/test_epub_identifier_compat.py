from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as E
import zipfile
from pathlib import Path

from tools import epub_semantics as s
from tools.browser_runtime import run_hidden

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / '.tmp' / 'tests'
TEMP.mkdir(parents=True, exist_ok=True)


def header(identifier, level=1):
    return {'t':'Header','c':[level,[identifier,[],[]],[{'t':'Str','c':'Title'}]]}


class IdentifierCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('pandoc'),'Pandoc required')
    def test_real_writer_container_alias_is_bound_to_source_and_first_heading(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root=Path(directory)
            entry, source_json, output=root/'main.md',root/'source.json',root/'book.epub'
            entry.write_text('::: {#_front}\n# Preface {#_preface}\n\n## Detail {#_detail}\n\nText.\n:::\n',encoding='utf8')
            for command in (
                ['pandoc',str(entry),'--to=json','-o',str(source_json)],
                ['pandoc',str(source_json),'--from=json','--to=epub3','--toc','--toc-depth=3','--epub-title-page=false','--metadata=title:Fixture','-o',str(output)],
            ):
                result=run_hidden(command,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
            source=json.loads(source_json.read_text(encoding='utf8'))
            with zipfile.ZipFile(output) as book:
                docs={name:E.fromstring(book.read(name)) for name in book.namelist() if name.endswith('.xhtml')}
            nav_path,toc=next((path,node) for path,dom in docs.items() for node in dom.iter() if node.tag.endswith('}nav') and 'toc' in node.get('{http://www.idpf.org/2007/ops}type','').split())
            actual=s.validate_outline(source,toc,nav_path=nav_path,documents=docs,epub_identifiers=True)
            self.assertEqual(actual[0].identifier,'id__front')
            self.assertEqual(actual[1].parent,'id__front')
            with self.assertRaisesRegex(s.EpubSemanticError,'identifier'):
                s.validate_outline(source,toc,epub_identifiers=True)
            without_container={'blocks':source['blocks'][0]['c'][1]}
            with self.assertRaisesRegex(s.EpubSemanticError,'identifier'):
                s.validate_outline(without_container,toc,nav_path=nav_path,documents=docs,epub_identifiers=True)
            container=next(node for dom in docs.values() for node in dom.iter() if node.get('id')=='id__front')
            container.insert(0,E.fromstring('<p>Wrong landing before the heading</p>'))
            with self.assertRaisesRegex(s.EpubSemanticError,'identifier'):
                s.validate_outline(source,toc,nav_path=nav_path,documents=docs,epub_identifiers=True)

    def test_legacy_toc_only_call_keeps_raw_identifier_contract(self):
        source = {'blocks':[header('_old')]}
        nav = E.fromstring('<nav><ol><li><a href="ch#_old">Title</a></li></ol></nav>')
        s.validate_outline(source,nav)
        with self.assertRaisesRegex(s.EpubSemanticError,'identifier'):
            s.validate_outline(source,nav,epub_identifiers=True)

    def test_serialized_collision_is_rejected(self):
        source = {'blocks':[header('_old'),header('id__old')]}
        nav = E.fromstring('<nav><ol/></nav>')
        with self.assertRaisesRegex(s.EpubSemanticError,'序列化后冲突'):
            s.validate_outline(source,nav,epub_identifiers=True)

    @unittest.skipUnless(shutil.which('pandoc'),'Pandoc required')
    def test_real_epub_writer_ids_parentage_and_targets(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root=Path(directory)
            entry, source_json, output = root/'main.md', root/'source.json', root/'book.epub'
            entry.write_text('# Part {#_part}\n\n## Chapter {#_chapter}\n\nText.\n\n# Extra {#9-extra}\n\n# 中文 {#中文}\n\nText.\n',encoding='utf8')
            for command in (
                ['pandoc',str(entry),'--to=json','-o',str(source_json)],
                ['pandoc',str(source_json),'--from=json','--to=epub3','--toc','--toc-depth=3','--epub-title-page=false','--metadata=title:Fixture','-o',str(output)],
            ):
                result=run_hidden(command,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
            source=json.loads(source_json.read_text(encoding='utf8'))
            with zipfile.ZipFile(output) as book:
                docs={name:E.fromstring(book.read(name)) for name in book.namelist() if name.endswith('.xhtml')}
            nav_path, toc=next((path,node) for path,dom in docs.items() for node in dom.iter() if node.tag.endswith('}nav') and 'toc' in node.get('{http://www.idpf.org/2007/ops}type','').split())
            result=s.validate_outline(source,toc,nav_path=nav_path,documents=docs,epub_identifiers=True)
            self.assertEqual([entry.identifier for entry in result],['id__part','id__chapter','id_9-extra','中文'])
            self.assertEqual(result[1].parent,'id__part')
            self.assertEqual(source['blocks'][0]['c'][1][0],'_part')
            first=next(node for node in toc.iter() if node.tag.endswith('}a'))
            first.set('href',first.get('href').replace('id__part','_part'))
            with self.assertRaisesRegex(s.EpubSemanticError,'identifier'):
                s.validate_outline(source,toc,nav_path=nav_path,documents=docs,epub_identifiers=True)


if __name__=='__main__':
    unittest.main()
