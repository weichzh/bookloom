from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools import epub_browser_qa as qa
from tools.browser_runtime import AgentBrowserSession

ROOT = Path(__file__).resolve().parents[1]
# Invented data preserves long-word, @-key, and hyphen-wrapping coverage.
TABLE='''| ITEM NO. | PartNo | Vendor | Parameter | Value | QTY. |
| --- | --- | --- | --- | --- | --- |
| 1 | ZX-4186 | SyntheticMechanical Gears Limited | Measure@Sketch9 | 1432.75 | 1 |
| 2 | ZX-5279 | SyntheticMechanical Bearing Limited | Bend Radius@Sketch9 | 2470 | [末项](#fixture-end) |

: 数据题名 {#ID}
'''
EIGHT='''| 序号 | 型号 | Vendor | Parameter | 长度 | 宽度 | 高度 | 数量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | TOKEN | SyntheticMechanical Gears Limited | Measure@Sketch9 | 123.45 | 678.90 | 98.76 | 1 |

: 八列数据 {#ID}
'''
MERGED='''<div class="table-scroll"><table id="merged">
<caption>合并数据 <a href="#fixture-end">原注</a></caption><thead>
<tr><th scope="colgroup" colspan="2">前两列</th><th scope="col">末列</th></tr></thead>
<tbody><tr><th scope="row" rowspan="2">两行</th><td>A</td><td>B</td></tr>
<tr><td>C</td><td><a href="#fixture-end">末项</a></td></tr></tbody></table></div>'''

def convert(input_text,source,target,filter=False):
    cmd=['pandoc','-f',source,'-t',target]
    if filter:cmd+=['--lua-filter='+str(ROOT/'formats/pandoc/semantics.lua')]
    result=subprocess.run(cmd,input=input_text,text=True,encoding='utf-8',capture_output=True,
                          check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return result.stdout

def render_fixture():
    old=TABLE.replace('#ID','#wide-old')
    new=TABLE.replace('#ID','#wide-new')
    source=(':::: {#figure-old .native-figure data-figure-body="blocks"}\n\n'+old+
      '\n::: {.figure-caption}\n原生图注\n:::\n\n::::\n\n'
      ':::: {#figure-new .native-figure data-figure-body="blocks"}\n\n'
      '::: {.table-scroll}\n\n'+new+'\n:::\n\n'
      '::: {.figure-caption}\n原生图注\n:::\n\n::::\n\n'
      '| 普通表 | 值 |\n| --- | --- |\n| A | 1 |\n\n: 普通题名 {#ordinary}\n\n'
      '::: {.table-scroll}\n\n'+EIGHT.replace('TOKEN','ZX-4186').replace('#ID','#eight-negative')+'\n:::\n\n'
      '::: {.table-scroll}\n\n'+EIGHT.replace('TOKEN','[ZX-4186]{.table-token}').replace('#ID','#eight-positive')+'\n:::\n')
    ast=convert(source,'markdown+fenced_divs+bracketed_spans+footnotes-raw_html','json',True)
    markdown_body=convert(ast,'json','html5')
    merged_ast=convert(MERGED,'html','json')
    merged_body=convert(merged_ast,'json','html5')
    return ast,merged_ast,markdown_body+merged_body+'<p id="fixture-end">原注正文</p>'

@pytest.mark.skipif(not shutil.which('pandoc'),reason='Pandoc required')
def test_data_table_semantics_survive_pandoc():
    ast,merged_ast,body=render_fixture()
    parsed=json.loads(ast);merged=json.loads(merged_ast)
    assert any(x['t']=='Figure' for x in parsed['blocks'])
    assert merged['blocks'][0]['t']=='Div' and merged['blocks'][0]['c'][1][0]['t']=='Table'
    doc=ET.fromstring('<body>'+body+'</body>')
    old=doc.find('.//table[@id="wide-old"]');new=doc.find('.//table[@id="wide-new"]')
    assert old is not None and new is not None
    def cells(t):
        return [(n.tag,''.join(n.itertext()),n.get('rowspan'),n.get('colspan'),
                 tuple(a.get('href') for a in n.iter('a'))) for n in t.iter() if n.tag in {'th','td'}]
    assert cells(old)==cells(new)
    assert old.find('caption').text==new.find('caption').text=='数据题名'
    figure=doc.find('.//figure[@id="figure-new"]')
    assert figure.find('./div[@class="table-scroll"]/table[@id="wide-new"]') is not None
    assert len(figure.findall('./figcaption'))==1
    assert ''.join(figure.find('./figcaption').itertext()).strip()=='原生图注'
    table=doc.find('.//table[@id="merged"]')
    assert table.find('./caption/a').get('href')=='#fixture-end'
    assert table.find('.//th[@colspan="2"]').get('scope')=='colgroup'
    assert table.find('.//th[@rowspan="2"]').get('scope')=='row'
    assert table.find('.//td/a').get('href')=='#fixture-end'
    assert doc.find('./table[@id="ordinary"]') is not None
    negative=doc.find('.//table[@id="eight-negative"]');positive=doc.find('.//table[@id="eight-positive"]')
    assert cells(negative)==cells(positive)
    assert negative.find('.//span[@class="table-token"]') is None
    assert positive.find('.//span[@class="table-token"]').text=='ZX-4186'

@pytest.mark.skipif(not (shutil.which('pandoc') and (os.getenv('TRANSLATOR_AGENT_BROWSER')
  or shutil.which('agent-browser'))),reason='Pandoc and agent-browser required')
def test_data_table_words_and_local_scroll_in_three_modes(tmp_path):
    _,_,body=render_fixture()
    css=(ROOT/'formats/epub/book.css').read_text(encoding='utf-8')
    page=tmp_path/'native-data.xhtml'
    page.write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Native tables</title><style>'+
        css+'</style></head><body>'+body+'</body></html>',encoding='utf-8')
    keys=('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC',
          'TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE','AGENT_BROWSER_EXECUTABLE_PATH')
    env={key:value for key in keys if (value:=os.getenv(key))}
    runtime=AgentBrowserSession('native-data-table-regression','native-data-'+str(time.time_ns()),
                                root=ROOT,scratch=tmp_path/'runtime',environment=env)
    trace=[]
    def command(args,capture=True):
        result=runtime.run(args,capture=capture)
        assert result.returncode==0,result.stderr
        return result.stdout
    def evaluate(script):return qa.parse_json_output(command(['eval',script]))
    try:
        command(['open','about:blank'],capture=False)
        for definition in qa.default_modes():
          for width in (390,1920):
            mode={**definition,'viewport':[width,844]};name=mode['name'];height=844
            harness=tmp_path/(name+str(width)+'.html')
            qa.write_harness(harness,[page.name],width,height,10,mode,base_prefix=tmp_path.as_uri()+'/')
            command(['set','viewport',str(width),str(height)])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()])
            result=evaluate('window.qaShowPage('+json.dumps(page.as_uri())+')')
            detail=evaluate(r'''(()=>{
              const d=frame.contentDocument,old=d.querySelector('#wide-old'),t=d.querySelector('#wide-new');
              const c=t.parentElement,normal=d.querySelector('#ordinary'),word=(node,needle)=>{
                const w=d.createTreeWalker(node,NodeFilter.SHOW_TEXT);let n;
                while(n=w.nextNode()){const at=n.data.indexOf(needle);if(at>=0){
                  const r=d.createRange();r.setStart(n,at);r.setEnd(n,at+needle.length);
                  return new Set([...r.getClientRects()].map(b=>b.top.toFixed(1))).size;}}
                throw Error('missing word '+needle);};
              const cells=t=>[...t.querySelectorAll('th,td')].map(n=>({tag:n.localName,text:n.textContent,
                colspan:n.getAttribute('colspan'),rowspan:n.getAttribute('rowspan'),
                links:[...n.querySelectorAll('a')].map(x=>x.getAttribute('href'))}));
              const oldWords=['SyntheticMechanical','Measure@Sketch9'].map(x=>word(old,x));
              const newWords=['SyntheticMechanical','Measure@Sketch9','Radius@Sketch9'].map(x=>word(t,x));
              const negative=d.querySelector('#eight-negative'),positive=d.querySelector('#eight-positive');
              const parameter=t.querySelector('tbody tr:last-child td:nth-child(4)');
              const last=t.querySelector('tbody tr:last-child td:last-child a');
              last.scrollIntoView({block:'center',inline:'nearest'});
              const ports=[];for(let n=last.parentElement;n;n=n.parentElement){const s=getComputedStyle(n);
                if(['auto','scroll','hidden','clip','overlay'].includes(s.overflowX))ports.push(n);}
              for(const p of ports)p.scrollLeft=p.scrollWidth;
              const r=d.createRange(),text=last.firstChild;r.setStart(text,text.length-1);r.setEnd(text,text.length);
              const edge=r.getBoundingClientRect(),clips=[{left:0,right:d.documentElement.clientWidth}];
              for(const p of ports){const b=p.getBoundingClientRect();clips.push({left:b.left+p.clientLeft,right:b.left+p.clientLeft+p.clientWidth});}
              const lastContained=clips.every(x=>edge.left>=x.left&&edge.right<=x.right);
              const m=d.querySelector('#merged');
              return {oldWords,newWords,oldCells:cells(old),newCells:cells(t),
                eightNegative:word(negative,'ZX-4186'),eightPositive:word(positive,'ZX-4186'),
                eightNegativeCells:cells(negative),eightPositiveCells:cells(positive),
                figureCaption:t.closest('figure').querySelector('figcaption').textContent.trim(),
                captions:[old.querySelector('caption').textContent,t.querySelector('caption').textContent],
                ports:ports.map(x=>({client:x.clientWidth,scroll:x.scrollWidth,left:x.scrollLeft})),lastContained,
                parameter:{phraseLines:word(t,'Bend Radius@Sketch9'),
                  whiteSpace:getComputedStyle(parameter).whiteSpace,wordBreak:getComputedStyle(parameter).wordBreak,
                  overflowWrap:getComputedStyle(parameter).overflowWrap},
                wrapper:{client:c.clientWidth,scroll:c.scrollWidth,display:getComputedStyle(c).display},
                normal:{parent:normal.parentElement.className,display:getComputedStyle(normal).display,
                  wordBreak:getComputedStyle(normal).wordBreak,overflowWrap:getComputedStyle(normal).overflowWrap,
                  client:normal.clientWidth,scroll:normal.scrollWidth},
                merged:{colspan:m.querySelector('[colspan]').getAttribute('colspan'),
                  rowspan:m.querySelector('[rowspan]').getAttribute('rowspan'),
                  scopeCol:m.querySelector('[colspan]').getAttribute('scope'),
                  scopeRow:m.querySelector('[rowspan]').getAttribute('scope'),
                  caption:m.querySelector('caption').textContent,
                  links:[...m.querySelectorAll('a')].map(a=>a.getAttribute('href'))},
                pageWidth:d.documentElement.scrollWidth,viewWidth:d.documentElement.clientWidth};})()''')
            assert result['overflowCount']==0,result['overflowElements']
            assert detail['oldCells']==detail['newCells']
            assert detail['eightNegativeCells']==detail['eightPositiveCells']
            assert detail['eightPositive']==1
            assert detail['figureCaption']=='原生图注' and detail['captions']==['数据题名','数据题名']
            assert detail['newWords']==[1,1,1]
            assert detail['parameter']['whiteSpace']=='normal'
            assert detail['parameter']['wordBreak']=='keep-all'
            assert detail['parameter']['overflowWrap']=='normal'
            assert detail['lastContained'] and detail['pageWidth']<=detail['viewWidth']
            assert detail['normal']['parent']!='table-scroll'
            assert detail['normal']['scroll']==detail['normal']['client']
            assert detail['merged']=={'colspan':'2','rowspan':'2','scopeCol':'colgroup','scopeRow':'row',
                                     'caption':'合并数据 原注','links':['#fixture-end','#fixture-end']}
            if width==390:
                assert any(x['scroll']>x['client']+1 and x['left']>0 for x in detail['ports'])
                assert detail['parameter']['phraseLines']>1  # Spaces remain valid break opportunities.
                assert detail['eightNegative']>1  # A literal hyphen still breaks under keep-all.
                if name=='publisher-default':assert any(x>1 for x in detail['oldWords'])
            else:
                assert detail['wrapper']['client']==detail['wrapper']['scroll']
                assert all(x['scroll']<=x['client']+1 for x in detail['ports'])
                assert detail['parameter']['phraseLines']==1
            if not mode['disable_publisher_css']:
                assert detail['normal']['wordBreak']=='normal'
                assert detail['normal']['overflowWrap']=='anywhere'
            snapshot=command(['snapshot','-c'])
            assert '- table "合并数据 原注"' in snapshot
            assert '- columnheader "前两列"' in snapshot and '- rowheader "两行"' in snapshot
            assert '- cell "Measure@Sketch9"' in snapshot and '- link "原注"' in snapshot
            clicked=evaluate(r'''(async()=>{const a=frame.contentDocument.querySelector('#merged caption a');
              const target=linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href);
              return await clickNavigation(a,target);})()''')
            assert clicked is True
            trace.append({'mode':name,'width':width,'detail':detail,'snapshot':snapshot})
    finally:
        closed=runtime.run(['close'])
        (tmp_path/'native-data-results.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
        assert closed.returncode==0,closed.stderr
