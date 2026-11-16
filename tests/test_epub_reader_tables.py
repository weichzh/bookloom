from __future__ import annotations

import json
import os
import shutil
import time
import xml.etree.ElementTree as ET

import pytest

from tools import epub_browser_qa as qa
from tools.browser_runtime import AgentBrowserSession

H = '{http://www.w3.org/1999/xhtml}'


@pytest.mark.skipif(
    not (os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser')),
    reason='agent-browser required',
)
def test_reader_tables_keep_native_content_and_scroll_locally(tmp_path):
    headers = ''.join(f'<th id="col-{i}" scope="col">测量{i}</th>' for i in range(12))
    first = '<th scope="row" rowspan="2">样本</th>' + ''.join(
        f'<td>{i}.5</td>' for i in range(11))
    second = ''.join(f'<td>{i}.7</td>' for i in range(10)) + (
        '<td id="last-cell"><a href="#reader-note">2.3</a></td>')
    payload = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Native tables</title>'
        '<style>table{border-collapse:collapse}pre{max-width:100%;overflow-x:auto}'
        'svg{max-width:100%;height:auto}</style></head><body>'
        '<h1>Reader table regression</h1><table id="wide">'
        '<caption>表格题名 <a href="#reader-note">原注</a></caption><thead>'
        '<tr><th scope="colgroup" colspan="6">前组六列</th>'
        '<th scope="colgroup" colspan="6">后组六列</th></tr>'
        '<tr>'+headers+'</tr></thead><tbody><tr>'+first+'</tr><tr>'+second+'</tr>'
        '</tbody></table><table id="narrow"><caption>普通表</caption>'
        '<tbody><tr><th scope="row">A</th><td>1</td></tr></tbody></table>'
        '<pre id="code">'+('0123456789'*10)+'</pre>'
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="40" '
        'viewBox="0 0 900 40"><rect width="900" height="40" fill="navy"/></svg>'
        '<p id="reader-note">原注正文 <a href="#last-cell">返回末列</a></p>'
        '</body></html>'
    )
    page = tmp_path/'table.xhtml'
    page.write_text(payload, encoding='utf-8')
    source = ET.fromstring(payload)
    wide = source.find('.//'+H+'table[@id="wide"]')
    expected_cells = [{
        'tag': n.tag.rsplit('}',1)[-1], 'id': n.get('id'), 'text': ''.join(n.itertext()),
        'colspan': n.get('colspan'), 'rowspan': n.get('rowspan'), 'scope': n.get('scope'),
    } for n in wide.iter() if n.tag in {H+'th',H+'td'}]
    expected_links = [n.get('href') for n in wide.iter(H+'a')]
    keys = ('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA',
            'COMSPEC','TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE',
            'AGENT_BROWSER_EXECUTABLE_PATH')
    environment = {key:value for key in keys if (value:=os.getenv(key)) is not None}
    runtime = AgentBrowserSession(
        'translator-fa3bb6797355', 'reader-tables-'+str(time.time_ns()),
        scratch=tmp_path/'runtime', environment=environment)
    evidence = []

    def command(args, capture=True):
        result = runtime.run(args,capture=capture)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def evaluate(script):
        return qa.parse_json_output(command(['eval',script]))

    try:
        command(['open','about:blank'],capture=False)
        for mode in qa.default_modes():
            name = mode['name']
            width,height = mode['viewport']
            harness = tmp_path/(name+'.html')
            qa.write_harness(harness,['table.xhtml'],width,height,10,mode,
                             base_prefix=tmp_path.as_uri()+'/')
            command(['set','viewport',str(width),str(height)])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()])
            result = evaluate('window.qaShowPage('+json.dumps(page.as_uri()+'#wide')+')')
            detail = evaluate(r'''(()=>{
              const d=frame.contentDocument,w=d.getElementById('wide'),n=d.getElementById('narrow');
              const s=getComputedStyle(w);
              return {cells:[...w.querySelectorAll('th,td')].map(n=>({tag:n.localName,
                id:n.getAttribute('id'),text:n.textContent,colspan:n.getAttribute('colspan'),
                rowspan:n.getAttribute('rowspan'),scope:n.getAttribute('scope')})),
                links:[...w.querySelectorAll('a')].map(a=>a.getAttribute('href')),
                caption:w.querySelector('caption').textContent,display:s.display,
                overflowX:s.overflowX,width:w.clientWidth,scrollWidth:w.scrollWidth,
                cellPadding:parseFloat(getComputedStyle(w.querySelector('td')).paddingInlineStart),
                wordBreak:getComputedStyle(w.querySelector('th')).wordBreak,
                narrowWidth:n.clientWidth,narrowScrollWidth:n.scrollWidth,
                cjkLines:[...w.querySelectorAll('th')].map(c=>{const r=d.createRange();r.selectNodeContents(c);return new Set([...r.getClientRects()].map(b=>b.top.toFixed(1))).size;}),
                numericLines:[...w.querySelectorAll('td')].filter(c=>/^[0-9]+(?:[.][0-9]+)?$/.test(c.textContent)).map(c=>{const r=d.createRange();r.selectNodeContents(c);return new Set([...r.getClientRects()].map(b=>b.top.toFixed(1))).size;}),
                code:d.getElementById('code').textContent};})()''')
            assert detail['cells'] == expected_cells
            assert detail['links'] == expected_links
            assert detail['caption'] == '表格题名 原注'
            assert detail['code'] == '0123456789'*10
            assert result['overflowCount'] == 0, result['overflowElements']
            assert result['readerSettings']['actualColorScheme'] == mode['color_scheme']
            large = mode['disable_publisher_css']
            if large:
                assert result['readerSettings']['publisherCssDisabled'] is True
                assert result['readerSettings']['actualFontSizePx'] == 32
                assert result['readerSettings']['userFontApplied'] is True
                assert result['readerSettings']['mediaFittingApplied'] is True
                assert result['readerSettings']['codeScrollingApplied'] is True
                assert detail['display'] == 'block' and detail['overflowX'] == 'auto'
                assert all(lines == 1 for lines in detail['numericLines'])
                assert detail['wordBreak'] == 'keep-all'
                assert all(lines == 1 for lines in detail['cjkLines'])
                assert detail['cellPadding'] == 8
                assert detail['scrollWidth'] > detail['width']
                assert detail['narrowScrollWidth'] == detail['narrowWidth']
                last = evaluate(r'''(()=>{const d=frame.contentDocument,t=d.getElementById('wide');
                  t.scrollLeft=t.scrollWidth;const c=d.getElementById('last-cell');
                  c.scrollIntoView({block:'center',inline:'nearest'});
                  const r=c.getBoundingClientRect(),b=t.getBoundingClientRect();
                  return {scrollLeft:t.scrollLeft,visible:r.left<b.right&&r.right>b.left};})()''')
                assert last['scrollLeft'] > 0 and last['visible'] is True
                clicked = evaluate(r'''(async()=>{let a=frame.contentDocument.querySelector('#last-cell a');
                  const target=linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href);
                  return await clickNavigation(a,target);})()''')
                assert clicked is True
                returned = evaluate(r'''(async()=>{let a=frame.contentDocument.querySelector('#reader-note a');
                  const target=linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href);
                  return await clickNavigation(a,target);})()''')
                assert returned is True
            else:
                assert detail['display'] == 'table'
                assert result['readerSettings']['publisherCssDisabled'] is False
                assert result['readerSettings']['publisherStyleNodesRemaining'] > 0
            command(['screenshot',str(tmp_path/(name+'.png'))])
            evidence.append({'mode':name,'readerSettings':result['readerSettings'],
                             'overflowWidth':result['overflowWidth'],'table':detail})
    finally:
        closed = runtime.run(['close'])
        (tmp_path/'evidence.json').write_text(json.dumps(
            {'results':evidence,'close':closed.returncode},ensure_ascii=False,indent=2),
            encoding='utf-8')
        assert closed.returncode == 0, closed.stderr
