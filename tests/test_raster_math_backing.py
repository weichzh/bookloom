from __future__ import annotations

import binascii
import json
import os
import shutil
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

import pytest

from tools import epub_browser_qa as qa
from tools import translator
from tools.browser_runtime import AgentBrowserSession

ROOT = Path(__file__).resolve().parents[1]
H = '{http://www.w3.org/1999/xhtml}'


def formula_assets(directory, scale=1):
    width, height = 48*scale, 24*scale
    masks = ['10001/01010/00100/01010/10001/00000/00000',
             '00000/00100/00100/11111/00100/00100/00000',
             '00100/01100/00100/00100/00100/00100/01110']
    pixels = [0]*(width*height)
    for char, mask in enumerate(masks):
        for y, row in enumerate(mask.split('/')):
            for x, bit in enumerate(row):
                if bit == '1':
                    for dy in range(2*scale):
                        for dx in range(2*scale):
                            pixels[(y*2*scale+5*scale+dy)*width+char*14*scale+x*2*scale+2*scale+dx] = 1

    def png(opaque=False):
        raw = b''.join(b'\0'+b''.join(
            b'\0\0\0\xff' if bit else (b'\xff\xff\xff\xff' if opaque else b'\0\0\0\0')
            for bit in pixels[y*width:(y+1)*width]) for y in range(height))
        def chunk(kind, data):
            return struct.pack('>I',len(data))+kind+data+struct.pack('>I',binascii.crc32(kind+data))
        return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))
                +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))

    # Clear before every pixel keeps this tiny GIF's LZW code width at three bits.
    codes = [code for pixel in pixels for code in (4,pixel)]+[5]
    accumulator = bits = 0
    compressed = bytearray()
    for code in codes:
        accumulator |= code << bits
        bits += 3
        while bits >= 8:
            compressed.append(accumulator & 255)
            accumulator >>= 8
            bits -= 8
    if bits: compressed.append(accumulator & 255)
    blocks = b''.join(bytes([len(compressed[i:i+255])])+compressed[i:i+255]
                      for i in range(0,len(compressed),255))
    gif = (b'GIF89a'+struct.pack('<HHBBB',width,height,0x80,0,0)+b'\xff\xff\xff\0\0\0'
           +b'\x21\xf9\x04\x01\0\0\0\0'+b'\x2c'+struct.pack('<HHHHB',0,0,width,height,0)
           +b'\x02'+blocks+b'\0\x3b')
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="24" viewBox="0 0 48 24">'
           b'<style>.math-foreground{stroke:#111;fill:none;stroke-width:2}.math-background{fill:#fff}'
           b'@media(prefers-color-scheme:dark){.math-foreground{stroke:#eee}.math-background{fill:#182226}}</style>'
           b'<rect class="math-background" width="48" height="24"/>'
           b'<path class="math-foreground" d="M3 6L13 18M13 6L3 18M18 12H30M24 6V18M35 8L39 6V18M35 18H43"/></svg>')
    assets = {'black.PNG':png(),'black.GIF':gif,'theme.svg':svg,'theme.SVG':svg,'chart.png':png(True)}
    for name, payload in assets.items(): (directory/name).write_bytes(payload)
    return assets


@pytest.mark.skipif(not shutil.which('pandoc'),reason='Pandoc required')
def test_real_epub_keeps_raster_white_and_svg_dark_backing(tmp_path):
    assets = formula_assets(tmp_path)
    entry = tmp_path/'main.md'
    entry.write_text(
        '---\nlang: en\n---\n# Formula backing {#body}\n\n'
        'PNG ![x+1](black.PNG){#png-inline .math-inline} and GIF '
        '![x+1](black.GIF){#gif-inline .math-inline}.\n\n'
        '![x+1](black.PNG){#png-display .math-display}[]{.image-tail}\n\n'
        '![x+1](black.GIF){#gif-display .math-display}[]{.image-tail}\n\n'
        'SVG ![x+1](theme.svg){#svg-inline .math-inline} and '
        '![x+1](theme.SVG){#svg-upper .math-inline}.\n\n'
        '![Caption with ![q=20](black.GIF){#gif-caption .math-inline}](chart.png){#fig-test}\n',
        encoding='utf-8')
    output = tmp_path/'book.epub'
    language = translator.Language('en','source',entry,None,{'epub':output})
    work = translator.Work(ROOT,tmp_path,{'epub':{'cover':'chart.png','title_page':False,
        'toc_depth':1,'split_level':1,'bodymatter':'body'}},'raster-backing-fixture',
        'Raster backing fixture','markdown',None,tmp_path/'source.pdf',1,(language,))
    translator.build_markdown(work,language,'epub',output)
    evidence = tmp_path/'qa';evidence.mkdir()
    translator.qa_epub(work,language,output,evidence)
    for name,payload in assets.items(): assert (tmp_path/name).read_bytes()==payload
    unpacked = tmp_path/'unpacked'
    members = qa.safe_extract(output,unpacked)
    docs = {name:ET.parse(unpacked/name).getroot() for name in members}
    member = next(name for name,doc in docs.items()if any(n.get('id')=='png-inline'for n in doc.iter()))
    doc = docs[member]
    expected = {n.get('id'):{'src':n.get('src'),'alt':n.get('alt')} for n in doc.iter(H+'img') if n.get('id')}
    assert set(expected)=={'png-inline','gif-inline','png-display','gif-display','svg-inline','svg-upper','gif-caption'}
    assert doc.find('.//'+H+'figcaption/'+H+'img[@id="gif-caption"]') is not None
    with zipfile.ZipFile(output):
        for n in doc.iter(H+'img'):
            member_path = (unpacked/member).parent/n.get('src')
            assert member_path.read_bytes() in assets.values()
    keys=('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC',
          'TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE','AGENT_BROWSER_EXECUTABLE_PATH')
    env={key:value for key in keys if(value:=os.getenv(key))is not None}
    runtime=AgentBrowserSession('translator-fa3bb6797355','raster-math-'+str(time.time_ns()),
                                scratch=tmp_path/'runtime',environment=env)
    results=[]
    def command(args,capture=True):
        r=runtime.run(args,capture=capture);assert r.returncode==0,r.stderr;return r.stdout
    def evaluate(code): return qa.parse_json_output(command(['eval',code]))
    try:
        command(['open','about:blank'],False)
        for mode in qa.default_modes():
            name=mode['name'];width,height=mode['viewport'];harness=tmp_path/(name+'.html')
            qa.write_harness(harness,members,width,height,10,mode,base_prefix=unpacked.as_uri()+'/')
            command(['set','viewport',str(width),str(height)])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()])
            q=evaluate('window.qaShowPage('+json.dumps((unpacked/member).as_uri())+')')
            assert q['brokenImages']==0 and q['imageCount']==8 and q['formulaCount']==7,q
            assert q['overflowCount']==0,q['overflowElements']
            assert q['readerSettings']['actualColorScheme']==mode['color_scheme']
            images=evaluate(r'''(()=>{const d=frame.contentDocument;return [...d.querySelectorAll('img.math-inline,img.math-display')].map(i=>({id:i.id,src:i.getAttribute('src'),alt:i.alt,background:getComputedStyle(i).backgroundColor,width:i.width,height:i.height,loaded:i.complete&&i.naturalWidth>0,caption:Boolean(i.closest('figcaption'))}));})()''')
            for image in images:
                assert {k:image[k]for k in ['src','alt']}==expected[image['id']]
                assert image['loaded'] and image['width']>0 and image['height']>0
                if mode['disable_publisher_css']:
                    assert image['background']=='rgba(0, 0, 0, 0)'
                else:
                    dark_svg=mode['color_scheme']=='dark' and image['id'].startswith('svg-')
                    assert image['background']==('rgb(24, 34, 38)'if dark_svg else'rgb(255, 255, 255)'),image
            assert next(i for i in images if i['id']=='gif-caption')['caption'] is True
            ordinary=evaluate("getComputedStyle(frame.contentDocument.querySelector('figure > img')).backgroundColor")
            assert ordinary=='rgba(0, 0, 0, 0)'
            matches=evaluate(r'''(()=>{const d=frame.contentDocument,i=d.createElement('img');i.className='math-inline';const selectors=[];function walk(rules){for(const r of rules){if(r.selectorText?.includes('img.math-inline')&&r.selectorText.includes('.svg'))selectors.push(r.selectorText);if(r.cssRules)walk(r.cssRules);}}for(const sheet of d.styleSheets)walk(sheet.cssRules);if(!selectors.length)return null;return ['f.svg','f.SVG','f.sVg','f.png'].map(src=>{i.src=src;return i.matches(selectors.join(','));});})()''')
            if not mode['disable_publisher_css']: assert matches==[True,True,True,False]
            command(['screenshot',str(tmp_path/(name+'.png'))])
            results.append({'mode':name,'images':images,'readerSettings':q['readerSettings'],'overflowWidth':q['overflowWidth']})
        default=results[0]['images'];dark=results[1]['images']
        assert [(i['id'],i['width'],i['height'])for i in default]==[(i['id'],i['width'],i['height'])for i in dark]
    finally:
        closed=runtime.run(['close'])
        (tmp_path/'browser-evidence.json').write_text(json.dumps({'result':results,'close':closed.returncode},ensure_ascii=False,indent=2),encoding='utf-8')
        assert closed.returncode==0,closed.stderr


@pytest.mark.skipif(
    not (os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser')),
    reason='agent-browser required',
)
def test_reader_formula_em_sizes_survive_css_removal_and_repeat_navigation(tmp_path):
    formula_assets(tmp_path, scale=10)
    markup = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Formula sizes</title>'
        '<style>img{max-width:100%;height:auto}.math-display{display:block}</style>'
        '</head><body><h1 id="start">Formula sizes</h1>'
        '<p>PNG <img id="png" class="math-inline" src="black.PNG" alt="x+1" '
        'style="width:3em"/> GIF <img id="gif" class="math-inline" src="black.GIF" '
        'alt="x+1" style="width:2em"/></p>'
        '<p>SVG <img id="svg" class="math-inline" src="theme.svg" alt="x+1" '
        'style="height:1.5em;width:auto;vertical-align:-0.25em"/></p>'
        '<p><img id="long" class="math-display" src="black.PNG" alt="x+1" '
        'style="width:40em"/></p>'
        '<figure><img id="ordinary" src="chart.png" alt="Ordinary image" '
        'style="width:3em"/><figcaption>Caption <img id="caption" class="math-inline" '
        'src="black.PNG" alt="x+1" style="width:3em"/></figcaption></figure>'
        '<p><img id="native" class="math-inline" src="black.PNG" alt="x+1" '
        'width="48" height="24"/> '
        '<img id="pixels" class="math-inline" src="black.PNG" alt="x+1" '
        'style="width:50px"/></p><p id="end"><a href="#start">Return</a></p>'
        '</body></html>'
    )
    page = tmp_path/'sizes.xhtml'
    page.write_text(markup, encoding='utf-8')
    keys = ('PATH','SystemRoot','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA',
            'COMSPEC','TRANSLATOR_AGENT_BROWSER','TRANSLATOR_BROWSER_EXECUTABLE',
            'AGENT_BROWSER_EXECUTABLE_PATH')
    env = {key:value for key in keys if (value:=os.getenv(key)) is not None}
    runtime = AgentBrowserSession(
        'translator-fa3bb6797355','formula-sizes-'+str(time.time_ns()),
        scratch=tmp_path/'runtime',environment=env,
    )
    results = []
    def command(args, capture=True):
        result = runtime.run(args,capture=capture)
        assert result.returncode==0,result.stderr
        return result.stdout
    def evaluate(code): return qa.parse_json_output(command(['eval',code]))
    def image_geometry():
        return evaluate(r'''(()=>{const d=frame.contentDocument;return [...d.images].map(i=>({id:i.id,src:i.getAttribute('src'),alt:i.alt,width:i.width,height:i.height,naturalWidth:i.naturalWidth,naturalHeight:i.naturalHeight,baseline:getComputedStyle(i).verticalAlign,relative:i.hasAttribute('data-qa-math-size')}));})()''')
    try:
        command(['open','about:blank'],False)
        for mode in qa.default_modes():
            name=mode['name'];width,height=mode['viewport'];harness=tmp_path/(name+'.html')
            qa.write_harness(harness,['sizes.xhtml'],width,height,10,mode,base_prefix=tmp_path.as_uri()+'/')
            command(['set','viewport',str(width),str(height)])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()])
            result=evaluate('window.qaShowPage('+json.dumps(page.as_uri()+'#start')+')')
            assert result['brokenImages']==0 and result['overflowCount']==0,result
            assert result['readerSettings']['actualColorScheme']==mode['color_scheme']
            images=image_geometry();by_id={i['id']:i for i in images}
            if mode['disable_publisher_css']:
                assert result['readerSettings']['publisherCssDisabled'] is True
                assert result['readerSettings']['userFontApplied'] is True
                assert result['readerSettings']['userFontSizeApplied'] is True
                assert result['readerSettings']['mathImagesWithRelativeSizing']==5
                for ident,expected_width in [('png',96),('gif',64),('svg',96),('caption',96)]:
                    assert abs(by_id[ident]['width']-expected_width)<=1,by_id[ident]
                    assert abs(by_id[ident]['height']-expected_width/2)<=1,by_id[ident]
                assert by_id['svg']['baseline']=='-8px'
                assert by_id['long']['width']<=374 and by_id['long']['relative']
                assert by_id['native']['width']==48 and not by_id['native']['relative']
                assert by_id['pixels']['width']==374 and not by_id['pixels']['relative']
                assert by_id['ordinary']['width']>96 and not by_id['ordinary']['relative']
            else:
                assert result['readerSettings']['mathImagesWithRelativeSizing']==0
                assert not any(i['relative']for i in images)
                assert by_id['png']['width']==48 and by_id['gif']['width']==32
            # Same-document positioning and a real anchor click both reapply reader mode.
            repeated=evaluate('window.qaShowPage('+json.dumps(page.as_uri()+'#end')+')')
            assert repeated['overflowCount']==0
            evaluate("frame.contentDocument.querySelector('#end a').click(); true")
            command(['wait','--fn','frame.contentWindow.location.hash === "#start"'])
            assert image_geometry()==images
            command(['screenshot',str(tmp_path/(name+'.png'))])
            results.append({'mode':name,'images':images,'settings':result['readerSettings']})
    finally:
        closed=runtime.run(['close'])
        (tmp_path/'sizing-evidence.json').write_text(json.dumps(
            {'results':results,'close':closed.returncode},ensure_ascii=False,indent=2),encoding='utf-8')
        assert closed.returncode==0,closed.stderr
