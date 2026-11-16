"""Public, synthetic regression for opted-in math-bearing data tables."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from tools import epub_browser_qa as qa
from tools.browser_runtime import AgentBrowserSession

ROOT = qa.ROOT

SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="40"
 viewBox="0 0 1200 40"><path d="M1 20H1199 M5 2V38 M1195 2V38"
 stroke="#111" fill="none"/></svg>'''

BODY = '''<div class="table-scroll"><table id="wide"><caption>Mathematical data</caption>
<tbody><tr><td><img id="direct-math" class="math-inline" src="math.svg"
 alt="direct synthetic formula" width="1200" height="40"
 style="vertical-align:0em;height:2em;width:auto;" /></td>
<td><p><img id="first-math" class="math-inline" src="math.svg"
 alt="first-child synthetic formula" width="1200" height="40"
 style="vertical-align:0em;height:2em;width:auto;" /></p></td>
<td><img id="ordinary" src="ordinary.svg" alt="ordinary image" width="60" height="20" /></td>
<td><span id="end">LAST-COLUMN</span></td></tr></tbody></table></div>
<p><img id="outside-math" class="math-inline" src="math.svg"
 alt="unwrapped synthetic formula" width="1200" height="40"
 style="vertical-align:0em;width:4em;height:auto;" /></p>'''

DETAIL = r'''(() => {
  const d=frame.contentDocument, w=frame.contentWindow;
  const img=id=>{
    const n=d.getElementById(id), s=w.getComputedStyle(n);
    const width=parseFloat(s.width), height=parseFloat(s.height);
    const ratio=n.naturalWidth/n.naturalHeight;
    return {width,height,ratio,maxWidth:s.maxWidth,fontSize:parseFloat(s.fontSize),
      aspectError:Math.abs(width/height/ratio-1)};
  };
  const last=d.getElementById('end'), ports=[];
  last.scrollIntoView({block:'center',inline:'nearest'});
  for(let n=last.parentElement;n;n=n.parentElement){
    const s=w.getComputedStyle(n);
    if(['auto','scroll','overlay'].includes(s.overflowX)) ports.push(n);
  }
  for(const p of ports) p.scrollLeft=p.scrollWidth;
  const text=last.firstChild, range=d.createRange();
  range.setStart(text,text.length-1);range.setEnd(text,text.length);
  const edge=range.getBoundingClientRect();
  const clips=[{left:0,right:d.documentElement.clientWidth}];
  for(const p of ports){const r=p.getBoundingClientRect();
    clips.push({left:r.left+p.clientLeft,right:r.left+p.clientLeft+p.clientWidth});}
  return JSON.stringify({direct:img('direct-math'),first:img('first-math'),
    ordinary:img('ordinary'),outside:img('outside-math'),
    ports:ports.map(p=>({client:p.clientWidth,scroll:p.scrollWidth,left:p.scrollLeft})),
    lastVisible:clips.every(c=>edge.left>=c.left-1&&edge.right<=c.right+1),
    pageWidth:d.documentElement.scrollWidth,viewport:d.documentElement.clientWidth});
})()'''

SHRINK_NEGATIVE = r'''(() => {
  const d=frame.contentDocument,n=d.getElementById('direct-math');
  n.style.setProperty('max-width','24px','important');
  const s=frame.contentWindow.getComputedStyle(n);
  const actual=parseFloat(s.width),expected=2*parseFloat(s.fontSize)*n.naturalWidth/n.naturalHeight;
  n.style.removeProperty('max-width');
  return JSON.stringify({actual,expected});
})()'''

HIDDEN_NEGATIVE = r'''(() => {
  const d=frame.contentDocument,w=frame.contentWindow;
  const table=d.getElementById('wide'),wrapper=table.parentElement;
  for(const p of [table,wrapper]) p.style.setProperty('overflow-x','hidden','important');
  const ports=[];
  for(let n=d.getElementById('end').parentElement;n;n=n.parentElement){
    const s=w.getComputedStyle(n);
    if(['auto','scroll','overlay'].includes(s.overflowX)
       && n.scrollWidth>n.clientWidth+1) ports.push(n.tagName);
  }
  return JSON.stringify({userScrollable:ports.length>0,ports});
})()'''


@pytest.mark.skipif(
    not (os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser')),
    reason='agent-browser required',
)
def test_table_math_preserves_scale_and_local_scroll_in_reader_modes(tmp_path: Path) -> None:
    (tmp_path/'math.svg').write_text(SVG,encoding='utf-8')
    (tmp_path/'ordinary.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="20" '
        'viewBox="0 0 60 20"><rect width="60" height="20"/></svg>',encoding='utf-8')
    css=(ROOT/'formats/epub/book.css').read_text(encoding='utf-8')
    page=tmp_path/'synthetic-table.xhtml'
    page.write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>Synthetic math table</title><style>'+css+'</style></head><body>'+BODY+
        '</body></html>',encoding='utf-8')
    keys=('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC',
          'TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE','AGENT_BROWSER_EXECUTABLE_PATH')
    env={key:value for key in keys if (value:=os.getenv(key))}
    session=AgentBrowserSession('table-math-regression','math-'+str(time.time_ns()),
                                root=ROOT,scratch=tmp_path/'runtime',environment=env)
    trace=[]
    def command(args,*,capture=True,timeout=30):
        result=session.run(args,capture=capture,timeout=timeout)
        assert result.returncode==0,result.stderr
        return result.stdout
    def evaluate(script):
        value=qa.parse_json_output(command(['eval',script],timeout=60))
        return json.loads(value) if isinstance(value,str) else value
    try:
        command(['open','about:blank'],capture=False)
        # The production dark-mode default is desktop width.  Keep all three
        # reader styles at a genuinely narrow viewport for this regression.
        for definition in qa.default_modes():
            mode={**definition,'viewport':[390,844]}
            width,height=mode['viewport']
            harness=tmp_path/(mode['name']+'.html')
            qa.write_harness(harness,[],width,height,30,mode,
                             base_prefix=tmp_path.as_uri()+'/',available_members=[page.name])
            command(['set','viewport',str(width),str(height)])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()],capture=False)
            result=evaluate('window.qaShowPage('+json.dumps(page.as_uri())+')')
            detail=evaluate(DETAIL)
            shrink=evaluate(SHRINK_NEGATIVE)
            hidden=evaluate(HIDDEN_NEGATIVE)
            trace.append({'mode':mode['name'],'result':result,'detail':detail,
                          'shrink_negative':shrink,'hidden_negative':hidden})
            assert result['overflowCount']==0,result['overflowElements']
            assert result['brokenImages']==0 and result['formulasWithoutAlt']==0
            assert result['tableMathImageCount']==2
            if mode['disable_publisher_css']:
                settings=result['readerSettings']
                assert settings['publisherCssDisabled'] and settings['userFontSizeApplied']
                assert settings['tableMathScrollingApplied'] and settings['mediaFittingApplied']
                assert settings['mathImagesWithRelativeSizing']==3
                state={'done':True,'errors':[],'results':[result]}
                assert qa.result_failures(state,[result['path']],width,height,mode)==[]
                wrong={**result,'readerSettings':{**settings,'tableMathScrollingApplied':False}}
                failure=result['path']+': tableMathScrollingApplied=false'
                assert failure in qa.result_failures(
                    {'results':[wrong]},[result['path']],width,height,mode)
                wrong={**wrong,'tableMathImageCount':0}
                assert failure not in qa.result_failures(
                    {'results':[wrong]},[result['path']],width,height,mode)
            for key in ('direct','first'):
                image=detail[key]
                assert image['maxWidth']=='none' and image['aspectError']<0.01,image
                assert abs(image['width']/(2*image['fontSize']*image['ratio'])-1)<0.01,image
            assert detail['ordinary']['maxWidth']!='none'
            assert detail['outside']['maxWidth']!='none'
            assert detail['lastVisible'] and detail['pageWidth']<=detail['viewport']+1,detail
            assert any(p['scroll']>p['client']+1 and p['left']>0 for p in detail['ports']),detail
            assert shrink['actual']<0.1*shrink['expected'],shrink
            assert not hidden['userScrollable'],hidden
    finally:
        (tmp_path/'table-math-results.json').write_text(
            json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
        closed=session.run(['close'])
        assert closed.returncode==0,closed.stderr
