"""Browser regression for relative display sizing and local horizontal scroll.

All images are synthetic geometry fixtures, not a book formula or artwork.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import epub_browser_qa as qa
from tools import translator
from tools.browser_runtime import AgentBrowserSession

BASE=ROOT/'.tmp/translator'
BASE.mkdir(parents=True,exist_ok=True)

MEASURE=r'''(() => {
 const d=frame.contentDocument,w=frame.contentWindow;
 const val=id=>{const i=d.getElementById(id),s=getComputedStyle(i),r=i.getBoundingClientRect();
   const px=x=>parseFloat(x)||0;
   return {width:r.width,height:r.height,
     contentWidth:r.width-px(s.paddingLeft)-px(s.paddingRight),
     contentHeight:r.height-px(s.paddingTop)-px(s.paddingBottom),
     naturalWidth:i.naturalWidth,naturalHeight:i.naturalHeight,
     fontSize:px(s.fontSize),maxWidth:s.maxWidth};};
 const im=d.getElementById('long-img'),box=im.closest('.display-formula'),cs=getComputedStyle(box);
 const short=d.getElementById('short-img').closest('.display-formula');
 return {long:val('long-img'),short:val('short-img'),inline:val('inline-img'),
   source:val('source-img'),ordinary:val('ordinary-img'),
   scroll:{client:box.clientWidth,width:box.scrollWidth,left:box.scrollLeft,
      overflowX:cs.overflowX,display:cs.display,maxWidth:cs.maxWidth},
   shortScroll:{client:short.clientWidth,width:short.scrollWidth},
   viewport:[w.innerWidth,w.innerHeight],documentWidth:d.documentElement.scrollWidth,
   bodyWidth:d.body.scrollWidth};})()'''

GLYPHS=r'''(() => {
 const d=frame.contentDocument,w=frame.contentWindow,i=d.getElementById('long-img');
 const box=i.closest('.display-formula'),style=getComputedStyle(i),px=x=>parseFloat(x)||0;
 const v=[0,0,697.49,619.31];
 function visible(point,atEnd){
   box.scrollLeft=atEnd?box.scrollWidth:0;
   let r=i.getBoundingClientRect(),cw=r.width-px(style.paddingLeft)-px(style.paddingRight),
       ch=r.height-px(style.paddingTop)-px(style.paddingBottom);
   const absoluteY=w.scrollY+r.top+px(style.paddingTop)+(point[2]-v[1])/v[3]*ch;
   w.scrollTo({top:Math.max(0,absoluteY-250),left:0,behavior:'instant'});
   r=i.getBoundingClientRect();const clip=box.getBoundingClientRect();
   const x0=r.left+px(style.paddingLeft)+(point[0]-v[0])/v[2]*cw;
   const x1=r.left+px(style.paddingLeft)+(point[1]-v[0])/v[2]*cw;
   const y=r.top+px(style.paddingTop)+(point[2]-v[1])/v[3]*ch;
   return {scrollLeft:box.scrollLeft,x0,x1,y,clipLeft:clip.left,clipRight:clip.right,
     visible:x0>=clip.left-1 && x1<=clip.right+1 && y>=0 && y<w.innerHeight};
 }
 return {lastStroke:visible([32,38,607],false),rightMarker:visible([659,691,307],true)};
})()'''

def violations(m):
    out=[];v=m['long'];target=61.931*v['fontSize']
    if abs(v['contentHeight']-target)>max(1.5,target*.003):out.append('relative em height changed')
    if abs(v['contentWidth']/v['contentHeight']-v['naturalWidth']/v['naturalHeight'])>.004:out.append('original aspect ratio changed')
    if v['maxWidth']!='none':out.append('display image shrunk to max-width')
    if m['scroll']['overflowX']!='auto':out.append('local display scroll disabled')
    if m['scroll']['display']!='block':out.append('display container not block')
    if m['documentWidth']>m['viewport'][0]+1 or m['bodyWidth']>m['viewport'][0]+1:out.append('whole page horizontal overflow')
    if m['shortScroll']['width']>m['shortScroll']['client']+1:out.append('short display scrolls unnecessarily')
    if any(m[k]['maxWidth']=='none' for k in ('inline','source','ordinary')):out.append('nonwrapped or ordinary image no longer fitted')
    return out

@unittest.skipUnless(shutil.which('agent-browser') or os.getenv('TRANSLATOR_AGENT_BROWSER'),'agent-browser required')
class DisplayFormulaScrollTests(unittest.TestCase):
 def test_real_reader_scroll_sizing_fragment_reentry_and_negatives(self):
  with tempfile.TemporaryDirectory(prefix='display-scroll-',dir=BASE) as td:
   work=Path(td)
   long=work/'wide.svg'
   long.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="697.49" height="619.31" viewBox="0 0 697.49 619.31">'
      '<path d="M10 20h610v2H10z M10 250h620v2H10z"/>'
      '<path id="last-stroke" d="M32 604h6v6h-6z"/>'
      '<path id="right-marker" d="M659 300h32v15h-32z"/></svg>',encoding='utf8')
   short=work/'short.svg'
   short.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="45" viewBox="0 0 80 45"><path d="M4 10h50v3H4z M4 30h50v3H4z"/></svg>',encoding='utf8')
   ordinary=work/'ordinary.svg'
   ordinary.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="160" viewBox="0 0 900 160"><path d="M5 5h890v150H5z"/></svg>',encoding='utf8')
   translator.normalize_formula_svg(long);translator.normalize_formula_svg(short)
   links=''.join('<link rel="stylesheet" type="text/css" href="'+p.as_uri()+'"/>' for p in
                 (ROOT/'formats/epub/book.css',ROOT/'formats/epub/latex.css'))
   page=work/'display.xhtml'
   page.write_text('<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN"><head><title>Synthetic display geometry</title>'+links+'</head><body>'
      '<p><a id="go-long" href="#long">长式</a> <a id="go-short" href="#short">短式</a></p>'
      '<div id="long" class="display-formula"><span class="display-formula">'
      '<img id="long-img" class="math-display" src="wide.svg" style="vertical-align:-30.565em;margin:0;height:61.931em;width:auto" alt="合成宽式"/></span></div>'
      '<div id="short" class="display-formula"><img id="short-img" class="math-display" src="short.svg" style="height:2em;width:auto" alt="合成短式"/></div>'
      '<p>行内 <img id="inline-img" class="math-inline" src="short.svg" style="height:1.4em;width:auto" alt="合成行内式"/> 后续文字。</p>'
      '<p>来源图：</p><img id="source-img" class="math-display" src="wide.svg" alt="未包装来源公式图"/>'
      '<figure><img id="ordinary-img" src="ordinary.svg" alt="普通图"/><figcaption>普通图</figcaption></figure>'
      '</body></html>',encoding='utf8')
   runtime=AgentBrowserSession('display-scroll-regression','fixture-'+str(time.time_ns()),root=ROOT,scratch=work/'runtime')
   def cmd(args,capture=True):
    r=runtime.run(args,capture=capture,timeout=45);self.assertEqual(r.returncode,0,r.stderr);return r.stdout
   def ev(script):return qa.parse_json_output(cmd(['eval',script]))
   try:
    cmd(['open','about:blank'],capture=False)
    modes=[]
    for base in qa.default_modes():
     for width,height in ((390,844),(1280,900)):
      modes.append({**base,'name':base['name']+'-'+str(width),'viewport':[width,height]})
    for width,height in ((390,844),(1280,900)):
     modes.append(qa.mode_definition('publisher-200-'+str(width),width=width,height=height,
          color_scheme='light',disable_publisher_css=False,user_font_family=None,user_font_size_px=32))
    for mode in modes:
     with self.subTest(mode=mode['name']):
      width,height=mode['viewport'];harness=work/(mode['name']+'.html')
      qa.write_harness(harness,[page.name],width,height,15,mode,base_prefix=work.as_uri()+'/')
      cmd(['set','viewport',str(width),str(height)]);cmd(['set','media',mode['color_scheme']]);cmd(['open',harness.as_uri()])
      url=page.as_uri()+'#long';r=ev('window.qaShowPage('+json.dumps(url)+')')
      self.assertEqual(qa.result_failures({'errors':[],'results':[r]},[url],width,height,mode),[])
      before=ev(MEASURE);self.assertEqual(violations(before),[],before)
      g=ev(GLYPHS);self.assertTrue(g['lastStroke']['visible'] and g['rightMarker']['visible'],g)
      if before['scroll']['width']>before['scroll']['client']+1:self.assertGreater(g['rightMarker']['scrollLeft'],0)
      if mode['disable_publisher_css']:
       self.assertTrue(r['readerSettings']['mediaFittingApplied']);self.assertTrue(r['readerSettings']['mathDisplayScrollingApplied'])
      clicked=ev(r'''(async()=>{const d=frame.contentDocument;
       for(const id of ['go-short','go-long','go-short','go-long']){
        const a=d.getElementById(id),target=linkIdentity(a.getAttribute('href'),frame.contentWindow.location.href);
        if(!await clickNavigation(a,target))return false;
       }return true;})()''')
      self.assertTrue(clicked)
      after=ev(MEASURE);self.assertEqual(violations(after),[],after)
      for k in ('contentWidth','contentHeight','fontSize'):self.assertLess(abs(before['long'][k]-after['long'][k]),.6)
      g=ev(GLYPHS);self.assertTrue(g['lastStroke']['visible'] and g['rightMarker']['visible'],g)
      r=ev('window.qaShowPage('+json.dumps(url)+')')
      self.assertEqual(qa.result_failures({'errors':[],'results':[r]},[url],width,height,mode),[])
    mode=qa.default_modes()[0];width,height=mode['viewport'];harness=work/'negative.html'
    qa.write_harness(harness,[page.name],width,height,15,mode,base_prefix=work.as_uri()+'/')
    cmd(['set','viewport',str(width),str(height)]);cmd(['set','media','light']);cmd(['open',harness.as_uri()])
    ev('window.qaShowPage('+json.dumps(page.as_uri()+'#long')+')')
    ev("(()=>{frame.contentDocument.getElementById('long-img').style.setProperty('max-width','100%','important');return true})()")
    self.assertTrue(violations(ev(MEASURE)),'the old max-width shrink must fail')
    ev("(()=>{let i=frame.contentDocument.getElementById('long-img');i.style.removeProperty('max-width');i.closest('.display-formula').style.setProperty('overflow-x','hidden','important');return true})()")
    self.assertIn('local display scroll disabled',violations(ev(MEASURE)))
   finally:
    closed=runtime.run(['close']);self.assertEqual(closed.returncode,0,closed.stderr)

if __name__=='__main__':unittest.main()




def test_reader_checks_display_scrolling_without_changing_ordinary_media_gate():
    mode = qa.default_modes()[2]
    reader = {
        "actualColorScheme": "light", "publisherCssDisabled": True,
        "userFontApplied": True, "userFontSizeApplied": True,
        "reflowWrapApplied": True, "actualTextSpacingTrim": "space-all",
        "fullwidthPunctuationApplied": True, "mediaFittingApplied": True,
        "codeScrollingApplied": True, "mathDisplayScrollingApplied": True,
    }
    item = {"path": "body.xhtml", "viewport": [390, 844], "formulaCount": 1, "readerSettings": reader}
    def failures():
        return qa.result_failures({"errors": [], "results": [item]}, ["body.xhtml"], 390, 844, mode)
    assert failures() == []
    reader["mathDisplayScrollingApplied"] = False
    assert failures() == ["body.xhtml: mathDisplayScrollingApplied=false"]
    reader["mathDisplayScrollingApplied"] = True
    reader["mediaFittingApplied"] = False
    assert failures() == ["body.xhtml: mediaFittingApplied=false"]
    # Existing non-math results have no newly introduced prerequisite.
    reader["mediaFittingApplied"] = True
    del reader["mathDisplayScrollingApplied"]
    item["formulaCount"] = 0
    assert failures() == []
