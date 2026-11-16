from __future__ import annotations

import base64
import json
import os
import posixpath
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import unquote, urldefrag

import pytest

from tools import epub_browser_qa as browser
from tools import epub_semantics as semantics
from tools import translator
from tools.browser_runtime import run_hidden

ROOT = Path(__file__).resolve().parents[1]
FILTER = ROOT / 'formats/pandoc/latex.lua'
H = '{http://www.w3.org/1999/xhtml}'
EP = '{http://www.idpf.org/2007/ops}type'
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII=')
pytestmark = pytest.mark.skipif(not shutil.which('pandoc'), reason='Pandoc required')


def read(source, *, parts=False):
    command = ['pandoc', '--from=latex+raw_tex', '--to=json', '--lua-filter='+str(FILTER)]
    if parts:
        command.append('--metadata=translator-latex-has-part:true')
    return json.loads(translator.run(command, input_text=source).stdout)


def nodes(doc, kind):
    return [n for n in semantics.ast_nodes(doc) if n.get('t') == kind]


def text(inlines):
    return semantics.inline_text(inlines)


def source(parts=False, inline=False):
    return (r'\documentclass{book}\usepackage{graphicx}\usepackage{translator}\begin{document}'
        + (r'\part{Body part}' if parts else '') + r'''
\chapter{Body}\label{body}
Body paragraph.
\begin{verbatim}
\appendix
\end{verbatim}
Literal \verb|\appendix|. % \appendix
\chapter{Second body}\label{body-two}
Still body.
\sourcepage{1}\label{page-anchor}

\begin{quote}Ordinary quotation $x$.

Another quoted paragraph.\end{quote}
''' + ('Before switch. ' if inline else '\n') + r'''\appendix
\chapter{First appendix}\label{app:a}
\section{Section}\label{sec:a}
\subsection{Subsection}\label{sub:a}
\subsubsection{Deep}\label{deep:a}
\begin{figure}\includegraphics{diagram.png}\caption{Diagram}\label{fig:a}\end{figure}
\begin{table}\caption{Values}\label{tab:a}\begin{tabular}{ll}x&y\\1&2\\\end{tabular}\end{table}
\begin{equation}x=1\end{equation}
\begin{equation}x=2\label{eq:a}\end{equation}
\begin{quote}
\epubcodeblock
\small
\phantomsection\label{algorithm}
\textbf{for} $n=1$\\
\quad $x\leftarrow x+1$\hfill\emph{// update}\\
\qquad\texttt{return} $x$ (see \ref{sec:a})
\end{quote}
A note\footnote{See \ref{app:a}, \ref{sec:a}, \ref{deep:a}, \ref{fig:a}, \ref{tab:a}, \eqref{eq:a}.}.
\chapter{Second appendix}\label{app:b}
\section{Section}\label{sec:b}
\chapter{Third appendix}\label{app:c}
See \ref{app:a}, \ref{app:b}, \ref{app:c}, \ref{sec:b}.
\end{document}
''')


@pytest.mark.parametrize('parts,inline', [(False, False), (False, True), (True, False), (True, True)])
def test_appendix_native_state_headers_references_and_labels(parts, inline):
    doc = read(source(parts, inline), parts=parts)
    headers = {h['c'][1][0]: h['c'] for h in nodes(doc, 'Header')}
    expected = {'app:a':'A First appendix', 'sec:a':'A.1 Section',
                'sub:a':'A.1.1 Subsection', 'deep:a':'A.1.1.1 Deep',
                'app:b':'B Second appendix', 'sec:b':'B.1 Section', 'app:c':'C Third appendix'}
    assert {key:text(headers[key][2]) for key in expected} == expected
    assert [headers[k][0] for k in ('app:a','sec:a','sub:a','deep:a')] == [1,2,3,4]
    assert text(headers['body'][2]) == ('1 Body' if parts else 'Body')
    assert text(headers['body-two'][2]) == ('2 Second body' if parts else 'Second body')
    refs = {}
    for n in nodes(doc, 'Link'):
        refs.setdefault(n['c'][2][0], set()).add(text(n['c'][1]))
    for key,value in {'app:a':'A','app:b':'B','app:c':'C','sec:a':'A.1','sec:b':'B.1',
                      'deep:a':'A.1.1.1','fig:a':'A.1','tab:a':'A.1','eq:a':'(A.2)'}.items():
        assert value in refs['#'+key], (key, refs)
    assert any(n['c'][1] == r'\appendix' for n in nodes(doc,'CodeBlock'))
    assert any(n['c'][1] == r'\appendix' for n in nodes(doc,'Code'))
    lineblock = nodes(doc, 'LineBlock')
    assert len(lineblock) == 1 and len(lineblock[0]['c']) == 3
    assert any(n['c'][1] == '  ' for n in nodes(lineblock[0], 'Code'))
    assert any(n['c'][1] == '    ' for n in nodes(lineblock[0], 'Code'))
    assert [n['c'][1] for n in nodes(lineblock[0], 'Math')] == ['n=1', r'x\leftarrow x+1','x']
    assert any(n['c'][0][0] == 'algorithm' for n in nodes(lineblock[0], 'Span'))
    assert len(nodes(doc, 'BlockQuote')) == 1  # The unmarked quotation stays a quotation.


def test_appendix_alphabetic_counter_extends_past_z():
    doc = read(r'\begin{document}\chapter{Body}\appendix' + ''.join(
        r'\chapter{Appendix}\label{app:'+str(n)+'}' for n in range(1,28)) + r'\end{document}')
    header = nodes(doc,'Header')[-1]
    assert text(header['c'][2]) == 'AA Appendix'


def test_marker_only_paragraphs_are_plain_but_mixed_and_real_empty_are_preserved():
    # JSON permits a real empty Para, which the LaTeX reader normally omits itself.
    base = json.loads(translator.run(['pandoc','--from=latex+raw_tex','--to=json'],input_text=r'''
\sourcepage{2}\label{only}

Text \sourcepage{3}\label{mixed} continues.

\label{math} $x$
''').stdout)
    base['blocks'].extend([{'t':'Para','c':[]}, {'t':'Para','c':[{'t':'Code','c':[['',[],[]],' ']}]}])
    doc = json.loads(translator.run(['pandoc','--from=json','--to=json','--lua-filter='+str(FILTER)],
                                    input_text=json.dumps(base)).stdout)
    assert [b['t'] for b in doc['blocks']] == ['Plain','Para','Para','Para','Para']
    assert doc['blocks'][-2] == {'t':'Para','c':[]}
    assert doc['blocks'][-1] == base['blocks'][-1]
    assert [n['c'][0][0] for n in nodes(doc,'Span')] == ['only','mixed','math']
    html = translator.run(['pandoc','--from=json','--to=html5'],input_text=json.dumps(doc)).stdout
    assert '<p><span class="source-page" data-page="2"' not in html
    assert '<span id="only"></span>' in html
    assert '<p>Text ' in html and 'continues.</p>' in html


@pytest.mark.parametrize('body', [
    r'\epubcodeblock Text.',
    r'\begin{quote}Text.\par\epubcodeblock\end{quote}',
    r'\begin{quote}\epubcodeblock\par\epubcodeblock\par Text.\end{quote}',
    r'\begin{quote}\epubcodeblock\sourcepage{3}\label{empty}\end{quote}',
    r'\begin{quote}\epubcodeblock\begin{itemize}\item unsupported\end{itemize}\end{quote}',
])
def test_misplaced_empty_duplicate_or_non_line_code_declarations_fail(body):
    with pytest.raises(translator.CliError, match='epubcodeblock'):
        read(body)


@pytest.mark.skipif(not shutil.which('latex'), reason='LaTeX required')
def test_pdf_helper_nonprinting_and_native_appendix_reference(tmp_path):
    shutil.copyfile(ROOT/'formats/latex/translator.sty',tmp_path/'translator.sty')
    (tmp_path/'main.tex').write_text(r'''\documentclass{book}\usepackage{translator}\begin{document}
\chapter{Body}\appendix\chapter{First}\label{a}\section{Section}\label{s}
\begin{quote}\epubcodeblock\textbf{for} $n=1$\\\quad $x=x+1$\end{quote}
\end{document}''',encoding='utf8')
    result = run_hidden(['latex','-interaction=nonstopmode','-halt-on-error','main.tex'],cwd=tmp_path,
                        capture_output=True,text=True,encoding='utf8',errors='replace',timeout=30,check=False)
    assert result.returncode == 0, result.stdout+result.stderr
    aux = (tmp_path/'main.aux').read_text(encoding='utf8')
    assert r'\newlabel{a}{{A}' in aux and r'\newlabel{s}{{A.1}' in aux


@pytest.mark.skipif(not all(shutil.which(t) for t in ('latex','dvisvgm','uv')), reason='SVG tools required')
def test_actual_adapter_epub_preserves_rich_lines_and_native_appendix_nav(tmp_path, monkeypatch):
    monkeypatch.setattr(translator,'temp_root',lambda *args,**kwargs:tmp_path)
    entry=tmp_path/'main.tex';entry.write_text(source(),encoding='utf8')
    (tmp_path/'diagram.png').write_bytes(PNG);(tmp_path/'cover.png').write_bytes(PNG)
    output=tmp_path/'book.epub'
    translator.build_latex_epub_source(root=ROOT,work_path=tmp_path,entry=entry,output=output,
        title='Native objects',author=None,language='en',identifier='urn:test:latex-reading-objects',
        cover=tmp_path/'cover.png',structure_options=['--epub-title-page=false','--toc-depth=4'],bodymatter='body')
    language=translator.Language('en','source',entry,None,{'epub':output},epub_identifier='urn:test:latex-reading-objects')
    work=translator.Work(ROOT,tmp_path,{'epub':{'title':'Native objects','title_page':False,'toc_depth':4,'bodymatter':'body'}},'latex-objects',
        'Native objects','latex',None,tmp_path/'source.pdf',1,(language,))
    evidence=tmp_path/'qa';evidence.mkdir();translator.qa_epub(work,language,output,evidence)
    def glyph_outlines(svg):
        namespace='{http://www.w3.org/2000/svg}'
        link='{http://www.w3.org/1999/xlink}href'
        document=ET.fromstring(svg)
        paths={n.get('id'):n.get('d')for n in document.iter(namespace+'path')}
        uses=list(document.iter(namespace+'use'))
        if uses: return [paths[n.get(link,'')[1:]]for n in uses]
        foreground=document.find(namespace+"g[@class='math-foreground']")
        assert foreground is not None
        return [n.get('d')for n in foreground.iter(namespace+'path')]
    with zipfile.ZipFile(output)as z:
        docs={n:ET.fromstring(z.read(n))for n in z.namelist()if n.endswith('.xhtml')}
        actual={}
        for name,d in docs.items():
            for image in d.iter(H+'img'):
                if 'math-display'not in image.get('class','').split():continue
                match=re.search(r'x=([12])',image.get('alt',''))
                if match and r'\tag{A.'+match[1]+'}'in image.get('alt',''):
                    member=posixpath.normpath(posixpath.join(posixpath.dirname(name),image.get('src')))
                    actual[match[1]]=glyph_outlines(z.read(member))
        assert set(actual)=={'1','2'}
        for value in ('1','2'):
            basename='visible-number-'+value
            (tmp_path/(basename+'.tex')).write_text(
                r'\documentclass[12pt]{article}\usepackage[T1]{fontenc}\usepackage{amsmath}\pagestyle{empty}\begin{document}'
                +r'$\displaystyle x='+value+r'\qquad\text{(A.'+value+r')}$\end{document}',encoding='utf8')
            translator.run(['latex','-interaction=nonstopmode','-halt-on-error',basename+'.tex'],cwd=tmp_path)
            translator.run(['dvisvgm','--no-fonts','--bbox=min','-o',basename+'.svg',basename+'.dvi'],cwd=tmp_path)
            expected=glyph_outlines((tmp_path/(basename+'.svg')).read_bytes())
            assert actual[value]==expected  # The complete drawn expression, not only alt/tag counts.
    toc=next(n for d in docs.values()for n in d.iter(H+'nav')if n.get(EP)=='toc')
    labels=[' '.join(''.join(a.itertext()).split())for a in toc.iter(H+'a')]
    for value in ['1 Body','2 Second body','A First appendix','A.1 Section','B Second appendix','C Third appendix']:
        assert value in labels, labels
    code=next(n for d in docs.values()for n in d.iter(H+'div')if 'code-example'in n.get('class','').split())
    line=next(n for n in code.iter(H+'div')if 'line-block'in n.get('class','').split())
    assert len(list(line.iter(H+'br')))==2
    assert [n.text for n in line.iter(H+'code')]==['  ','  ','    ','return']
    assert len(list(line.iter(H+'img')))==3
    assert '// update' in ''.join(line.itertext())
    assert len([n for d in docs.values()for n in d.iter(H+'blockquote')])==1
    # No source/label-only paragraph was introduced into the actual HTML.
    for d in docs.values():
        for p in d.iter(H+'p'):
            if not ''.join(p.itertext()).strip() and len(list(p)):
                assert not all(n.tag==H+'span'and len(n)==0 and not ''.join(n.itertext())for n in p)
    ids={(name,n.get('id'))for name,d in docs.items()for n in d.iter()if n.get('id')}
    for name,d in docs.items():
        for a in d.iter(H+'a'):
            href=a.get('href','')
            if '#' not in href or '://'in href:continue
            member,fragment=urldefrag(unquote(href))
            member=posixpath.normpath(posixpath.join(posixpath.dirname(name),member))if member else name
            assert (member,fragment)in ids,(name,href)
    if os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser'):
        result=browser.run_browser_qa(output,tmp_path/'browser',screenshots=0)
        assert result['mode_count']==3 and not result['failures']
        assert not result['coverage']['unchecked_members']


def test_rich_math_identity_rejects_deletion_plain_text_mode_and_formula_changes():
    doc = {'blocks':[{'t':'Div','c':[['',['code-example'],[]],[{'t':'LineBlock','c':[[
        {'t':'Str','c':'A'}, {'t':'Space'}, {'t':'Math','c':[{'t':'InlineMath'},'x_1']},
        {'t':'Space'}, {'t':'Code','c':[['',[],[]],'return']},
    ]]}]]}]}
    original = '<div class="code-example"><div class="line-block">A <img class="math-inline" alt="x_1"/> <code>return</code></div></div>'
    expected = semantics.canonical_code(doc)
    semantics.validate_code_records(expected,semantics.code_from_xhtml([ET.fromstring(original)]))
    for wrong in [
        original.replace('x_1','x_2'),
        original.replace('math-inline','math-display'),
        original.replace('<img class="math-inline" alt="x_1"/>','x_1'),
        original.replace('<img class="math-inline" alt="x_1"/>',''),
        original.replace('alt="x_1"','alt=""'),
        original.replace('class="math-inline"','class="math-inline math-display"'),
    ]:
        with pytest.raises(semantics.EpubSemanticError):
            semantics.validate_code_records(expected,semantics.code_from_xhtml([ET.fromstring(wrong)]))


@pytest.mark.parametrize('operation', [r'\setcounter{section}{4}',
    r'\setcounter{section}{1}\addtocounter{section}{3}',
    r'\setcounter{section}{3}\stepcounter{section}',
    r'\setcounter{section}{3}\refstepcounter{section}'])
def test_appendix_section_counter_operations_remain_shared_state(operation):
    doc=read(r'\begin{document}\appendix\chapter{First}'+operation+
        r'\section{Fifth}\label{fifth}See \ref{fifth}.\end{document}')
    heading=next(x for x in nodes(doc,'Header')if x['c'][1][0]=='fifth')
    assert text(heading['c'][2])=='A.5 Fifth'
    assert any(n['c'][2][0]=='#fifth'and text(n['c'][1])=='A.5'for n in nodes(doc,'Link'))


def test_inline_first_code_marker_matches_standalone_marker():
    body=r'\textbf{for} $n=1$\\\quad $x=x+1$'
    inline=read(r'\begin{quote}\epubcodeblock'+body+r'\end{quote}')
    standalone=read('\\begin{quote}\n\\epubcodeblock\n'+body+'\n\\end{quote}')
    assert semantics.canonical_code(inline)==semantics.canonical_code(standalone)
    assert len(nodes(inline,'LineBlock')[0]['c'])==2


@pytest.mark.parametrize('appendix', [True,False])
def test_ams_automatic_numbers_include_unlabelled_rows_but_exclude_manual_tags(appendix):
    prefix='A'if appendix else'1'
    source=r'\begin{document}'+(r'\appendix'if appendix else'')+r'''\chapter{First}
\begin{equation}x=1\end{equation}
\begin{equation}x=2\label{second}\end{equation}
\begin{equation}x=3\tag{custom}\label{custom}\end{equation}
\begin{equation}x=4\label{after-custom}\end{equation}
\begin{align}x&=5\\x&=6\label{align-second}\\x&=7\notag\\x&=8\tag{X}\label{align-custom}\\x&=9\label{align-last}\end{align}
\begin{equation*}x=10\label{star}\end{equation*}
\begin{equation}x=11\label{last}\end{equation}
See \eqref{second},\eqref{custom},\eqref{after-custom},\eqref{align-second},\eqref{align-custom},\eqref{align-last},\ref{star},\eqref{last}.
\end{document}'''
    doc=read(source)
    refs={n['c'][2][0]:text(n['c'][1])for n in nodes(doc,'Link')}
    expected={'second':f'({prefix}.2)','custom':'(custom)','after-custom':f'({prefix}.3)',
              'align-second':f'({prefix}.5)','align-custom':'(X)','align-last':f'({prefix}.6)',
              'star':prefix,'last':f'({prefix}.7)'}
    assert refs=={'#'+key:value for key,value in expected.items()}
    tags=[value for n in nodes(doc,'Math')for value in re.findall(r'\\tag\{([^{}]+)\}',n['c'][1])]
    assert tags==[f'{prefix}.1',f'{prefix}.2','custom',f'{prefix}.3',f'{prefix}.4',f'{prefix}.5','X',f'{prefix}.6',f'{prefix}.7']


@pytest.mark.skipif(not all(shutil.which(t)for t in ('latex','dvisvgm','uv')),reason='SVG tools required')
def test_nested_manual_tag_preserves_rich_reference_counter_and_drawn_glyph(tmp_path,monkeypatch):
    from tools import gladtex_filter
    source=r'''\documentclass{book}\begin{document}\appendix\chapter{First}\label{body}
\begin{equation}x=1\tag{\textbf{M}}\label{manual}\end{equation}
\begin{equation}x=2\label{automatic}\end{equation}
See \eqref{manual} and \eqref{automatic}.\end{document}'''
    document=read(source)
    formulas=[n['c'][1]for n in nodes(document,'Math')]
    assert formulas[0].count(r'\tag')==1 and r'\tag{\textbf{M}}'in formulas[0]
    assert r'\tag{A.1}'in formulas[1]and r'\tag{A.2}'not in formulas[1]
    refs={n['c'][2][0]:n for n in nodes(document,'Link')}
    assert text(refs['#manual']['c'][1])=='(M)'and len(nodes(refs['#manual'],'Strong'))==1
    assert text(refs['#automatic']['c'][1])=='(A.1)'
    assert gladtex_filter.native_tagged_display(formulas[0])is not None
    assert gladtex_filter.native_tagged_display(formulas[1])is None
    monkeypatch.setattr(translator,'temp_root',lambda *args,**kwargs:tmp_path)
    entry=tmp_path/'main.tex';entry.write_text(source,encoding='utf8')
    (tmp_path/'cover.png').write_bytes(PNG);output=tmp_path/'book.epub'
    translator.build_latex_epub_source(root=ROOT,work_path=tmp_path,entry=entry,output=output,
        title='Nested tag',author=None,language='en',identifier='urn:test:nested-tag',cover=tmp_path/'cover.png',
        structure_options=['--epub-title-page=false','--toc-depth=2'],bodymatter='body')
    language=translator.Language('en','source',entry,None,{'epub':output},epub_identifier='urn:test:nested-tag')
    work=translator.Work(ROOT,tmp_path,{'epub':{'title':'Nested tag','title_page':False,'toc_depth':2,'bodymatter':'body'}},
        'nested-tag','Nested tag','latex',None,tmp_path/'source.pdf',1,(language,))
    evidence=tmp_path/'qa';evidence.mkdir();translator.qa_epub(work,language,output,evidence)
    def outlines(data):
        ns='{http://www.w3.org/2000/svg}';link='{http://www.w3.org/1999/xlink}href'
        root=ET.fromstring(data);paths={n.get('id'):n.get('d')for n in root.iter(ns+'path')}
        uses=list(root.iter(ns+'use'))
        if uses:return [paths[n.get(link,'')[1:]]for n in uses]
        foreground=root.find(ns+"g[@class='math-foreground']")
        assert foreground is not None
        return [n.get('d')for n in foreground.iter(ns+'path')]
    (tmp_path/'bold-M.tex').write_text(
        r'\documentclass[12pt]{article}\usepackage[T1]{fontenc}\pagestyle{empty}\begin{document}\textbf{M}\end{document}',encoding='utf8')
    translator.run(['latex','-interaction=nonstopmode','-halt-on-error','bold-M.tex'],cwd=tmp_path)
    translator.run(['dvisvgm','--no-fonts','--bbox=min','-o','bold-M.svg','bold-M.dvi'],cwd=tmp_path)
    reference=outlines((tmp_path/'bold-M.svg').read_bytes());assert len(reference)==1
    with zipfile.ZipFile(output)as z:
        found=[]
        for name in z.namelist():
            if not name.endswith('.xhtml'):continue
            for image in ET.fromstring(z.read(name)).iter(H+'img'):
                if r'\tag{\textbf{M}}'in image.get('alt',''):
                    member=posixpath.normpath(posixpath.join(posixpath.dirname(name),image.get('src')))
                    svg=z.read(member)
                    assert outlines(svg).count(reference[0])==1
                    assert gladtex_filter.ROW_TAG_RENDER_VERSION.encode()in svg
                    found.append(member)
        assert len(found)==1


def test_appendix_stepcounter_resets_dependent_section_state():
    document=read(r'''\begin{document}\appendix\chapter{First}\section{One}
\subsection{First}\subsection{Second}\stepcounter{section}\subsection{Restart}\label{restart}
See \ref{restart}.\end{document}''')
    target=next(x for x in nodes(document,'Header')if x['c'][1][0]=='restart')
    assert text(target['c'][2])=='A.2.1 Restart'
    assert any(text(x['c'][1])=='A.2.1'for x in nodes(document,'Link'))
