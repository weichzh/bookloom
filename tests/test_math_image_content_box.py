from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from tools import epub_browser_qa as qa
from tools import translator
from tools.browser_runtime import AgentBrowserSession

ROOT = Path(translator.__file__).resolve().parents[1]


@pytest.mark.skipif(not (os.getenv('TRANSLATOR_AGENT_BROWSER') or shutil.which('agent-browser')),
                    reason='agent-browser required')
def test_formula_content_box_preserves_em_size_and_local_scroll(tmp_path):
    """Content, rather than padded border box, must receive the declared em height."""
    css = (ROOT / 'formats/epub/book.css').read_text(encoding='utf8')
    old = (
        css + '\nimg.math-inline, img.math-display { box-sizing: border-box !important; }\n')
    (tmp_path/'new.css').write_text(css, encoding='utf8')
    (tmp_path/'old.css').write_text(old, encoding='utf8')
    (tmp_path/'small.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="10" viewBox="0 0 40 10">'
        '<style>path{stroke:#111}@media(prefers-color-scheme:dark){path{stroke:#eee}}</style>'
        '<path d="M1 5H39 M1 1V9 M39 1V9" fill="none"/></svg>', encoding='utf8')
    (tmp_path/'wide.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="24" viewBox="0 0 2000 24">'
        '<style>path{stroke:#111}@media(prefers-color-scheme:dark){path{stroke:#eee}}</style>'
        '<path d="M2 12H1998 M2 2V22 M1998 2V22" stroke-width="3" fill="none"/></svg>', encoding='utf8')
    body = '''<h1>数学图盒模型</h1>
      <div><img id="h08" class="math-display" src="small.svg" style="height:0.8em;width:auto" alt="a ∼ a"/></div>
      <div><img id="h11" class="math-display" src="small.svg" style="height:1.1em;width:auto" alt="short"/></div>
      <div><img id="h20" class="math-display" src="small.svg" style="height:2em;width:auto" alt="tall"/></div>
      <p>inline <img id="inline" class="math-inline" src="small.svg" style="height:1.1em;width:auto" alt="inline"/> text.</p>
      <p><img id="inlinewide" class="math-inline" src="wide.svg" style="height:1.1em;width:auto" alt="inline wide"/></p>
      <div class="source-formula-image"><img id="ordinarywide" class="math-display" src="wide.svg" style="height:1.6em;width:auto" alt="ordinary wide"/></div>
      <figure id="block" class="native-figure" data-figure-body="blocks"><p>规则：</p><div class="display-formula"><span class="display-formula"><img id="figurewide" class="math-display" src="wide.svg" style="height:1.6em;width:auto" alt="wide rule"/></span></div><figcaption>原生图注</figcaption></figure>
      <figure><img id="ordinary" src="small.svg" width="40" height="10" alt="ordinary image"/><figcaption>图像对照</figcaption></figure>'''
    for name in ('old','new'):
        (tmp_path/(name+'.xhtml')).write_text(
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/>'
            '<link rel="stylesheet" href="'+name+'.css"/></head><body>'+body+'</body></html>', encoding='utf8')

    runtime=AgentBrowserSession('math-image-content-box','size-'+str(time.time_ns()),root=ROOT,scratch=tmp_path/'runtime')
    trace=[]
    def command(args,capture=True):
        r=runtime.run(args,capture=capture)
        assert r.returncode==0,r.stderr
        return r.stdout
    def evaluate(js): return qa.parse_json_output(command(['eval',js]))
    def visible_paint(ident,name,dark):
        # Inspect what the browser actually drew, not the box in which an SVG may letterbox.
        bounds=evaluate("""(()=>{let i=frame.contentDocument.getElementById(ID);i.scrollIntoView({block:'center'});
            let r=i.getBoundingClientRect(),s=getComputedStyle(i),f=frame.getBoundingClientRect();
            return {left:f.left+r.left+parseFloat(s.paddingLeft),right:f.left+r.right-parseFloat(s.paddingRight),
              top:f.top+r.top+parseFloat(s.paddingTop),bottom:f.top+r.bottom-parseFloat(s.paddingBottom)};})()""".replace('ID',json.dumps(ident)))
        shot=tmp_path/(name+'-'+ident+'-paint.png');command(['screenshot',str(shot)])
        js=r'''(async()=>{let im=new Image();im.src=URL;await im.decode();let c=document.createElement('canvas');
          c.width=im.naturalWidth;c.height=im.naturalHeight;let x=c.getContext('2d');x.drawImage(im,0,0);
          let d=x.getImageData(0,0,c.width,c.height).data,b=BOUNDS,sx=c.width/innerWidth,sy=c.height/innerHeight;
          let L=Math.max(0,Math.floor(b.left*sx)),R=Math.min(c.width,Math.ceil(b.right*sx));
          let T=Math.max(0,Math.floor(b.top*sy)),B=Math.min(c.height,Math.ceil(b.bottom*sy));
          let xmin=R,xmax=-1,ymin=B,ymax=-1;
          for(let y=T;y<B;y++)for(let xx=L;xx<R;xx++){let k=(y*c.width+xx)*4;
            let ink=DARK ? d[k]>190&&d[k+1]>190&&d[k+2]>190 : d[k]<245&&d[k+1]<245&&d[k+2]<245;
            if(ink){xmin=Math.min(xmin,xx);xmax=Math.max(xmax,xx);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y)}}
          return {width:xmax-xmin+1,height:ymax-ymin+1,available:R-L,left:xmin-L,right:R-1-xmax};})()'''
        js=js.replace('URL',json.dumps(shot.as_uri())).replace('BOUNDS',json.dumps(bounds)).replace('DARK','true' if dark else 'false')
        painted=evaluate(js)
        # Original stroke spans x=2..1998 and y=2..22: expected painted width/height ~100.
        assert painted['width']>=painted['available']-6 and painted['height']>0,painted
        assert 65 <= painted['width']/painted['height'] <= 140,painted
        return painted
    try:
        command(['open','about:blank'],False)
        modes=qa.default_modes()
        zoom=qa.mode_definition('publisher-200',width=390,height=844,color_scheme='light',
            disable_publisher_css=False,user_font_family=None,user_font_size_px=32)
        cases=[('old',modes[0],390),('new',modes[0],390),('new',modes[1],1280),
               ('new',modes[2],390),('new',zoom,390)]
        for candidate,definition,width in cases:
            mode={**definition,'viewport':[width,844]}
            harness=tmp_path/(candidate+'-'+mode['name']+'.html')
            qa.write_harness(harness,[candidate+'.xhtml'],width,844,10,mode,base_prefix=tmp_path.as_uri()+'/')
            command(['set','viewport',str(width),'844'])
            command(['set','media',mode['color_scheme']])
            command(['open',harness.as_uri()])
            shown=evaluate('window.qaShowPage('+json.dumps((tmp_path/(candidate+'.xhtml')).as_uri())+')')
            assert shown['brokenImages']==0,shown
            info=evaluate(r'''(()=>{let d=frame.contentDocument;
              let ids=['h08','h11','h20','inline','inlinewide','ordinarywide','figurewide','ordinary'];
              let imgs=Object.fromEntries(ids.map(id=>{let i=d.getElementById(id),s=getComputedStyle(i),r=i.getBoundingClientRect();
                const h=r.height-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom),w=r.width-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight);
                return [id,{h,w,outerH:r.height,outerW:r.width,font:parseFloat(s.fontSize),padTop:parseFloat(s.paddingTop),padBottom:parseFloat(s.paddingBottom),
                  box:s.boxSizing,bg:s.backgroundColor,ratio:w/h,naturalRatio:i.naturalWidth/i.naturalHeight,loaded:i.complete&&i.naturalWidth>0,
                  relative:i.hasAttribute('data-qa-math-size'),maxWidth:s.maxWidth}];}));
              let f=d.getElementById('figurewide').closest('.display-formula');f.scrollLeft=f.scrollWidth;
              let ir=d.getElementById('figurewide').getBoundingClientRect(),fr=f.getBoundingClientRect(),is=getComputedStyle(d.getElementById('figurewide'));
              return {imgs,pageWidth:d.documentElement.scrollWidth,viewport:d.documentElement.clientWidth,
                scroll:{client:f.clientWidth,width:f.scrollWidth,left:f.scrollLeft,overflow:getComputedStyle(f).overflowX,
                  lastVisible:ir.right-parseFloat(is.paddingRight)<=fr.right+1}};})()''')
            trace.append({'candidate':candidate,'mode':mode['name'],'data':info})
            if mode['name']=='publisher-default':
                for ident in ('h08','inlinewide','ordinarywide'):
                    evaluate("frame.contentDocument.getElementById('"+ident+"').scrollIntoView({block:'center'}); true")
                    command(['screenshot',str(tmp_path/(candidate+'-'+ident+'.png'))])
            assert all(x['loaded'] for x in info['imgs'].values()),info
            assert info['pageWidth']<=info['viewport']+1,info
            i=info['imgs']['h08']
            if candidate=='old':
                assert i['box']=='border-box'
                assert i['h']==pytest.approx((0.8-0.7)*i['font'],abs=0.7),i
            else:
                for ident,em in [('h08',.8),('h11',1.1),('h20',2.0),('inline',1.1)]:
                    x=info['imgs'][ident]
                    assert x['h']==pytest.approx(em*x['font'],abs=0.8),(candidate,mode['name'],ident,x)
                    assert x['ratio']==pytest.approx(x['naturalRatio'],rel=.02),(mode['name'],ident,x)
                x=info['imgs']['figurewide']
                if not mode['disable_publisher_css']:
                    assert x['ratio']==pytest.approx(x['naturalRatio'],rel=.02),(mode['name'],x)
                for ident in ('inlinewide','ordinarywide'):
                    x=info['imgs'][ident]
                    assert x['outerW']<=info['viewport']+1,(mode['name'],ident,x)
                if not mode['disable_publisher_css']:
                    info['painted']={ident:visible_paint(ident,candidate+'-'+mode['name'],mode['color_scheme']=='dark')
                                     for ident in ('inlinewide','ordinarywide')}
                if not mode['disable_publisher_css']:
                    assert info['scroll']['overflow']=='auto' and info['scroll']['width']>info['scroll']['client']
                    assert info['scroll']['left']>0 and info['scroll']['lastVisible'],info['scroll']
                    assert info['imgs']['ordinary']['bg']=='rgba(0, 0, 0, 0)'
                    expected='rgb(24, 34, 38)' if mode['color_scheme']=='dark' else 'rgb(255, 255, 255)'
                    assert info['imgs']['h08']['bg']==expected,info['imgs']['h08']
                else:
                    assert info['imgs']['h08']['relative']
                if mode['user_font_size_px']==32:
                    assert info['imgs']['h08']['font']==pytest.approx(32,abs=.1)
            if candidate=='new' and mode['name'] in ('publisher-default','reader-dark'):
                command(['screenshot',str(tmp_path/(mode['name']+'.png'))])
    finally:
        closed=runtime.run(['close'])
        (tmp_path/'geometry.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        assert closed.returncode==0,closed.stderr
