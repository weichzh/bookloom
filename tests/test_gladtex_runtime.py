from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import gladtex_filter as gf
from tools.browser_runtime import hidden_process_options

ALIGN = r"\begin{align}x&=1\tag{1.1}\\y&=2\tag{1.2}\end{align}"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 40"><path id="g0" d="M0 0L1 1"/><use href="#g0"/></svg>'
NS = "{http://www.w3.org/2000/svg}"


def formula(suffix):
    return ALIGN.replace("x&=1", f"x&={suffix}")


def item(path):
    return {"true": {"path": path, "pos": {"width": 80, "height": 40, "depth": 0}}}


def fake_modules():
    cc = SimpleNamespace(concurrent=object())
    original = SimpleNamespace(Popen=lambda *args, **kw: (args, kw), PIPE=object(), TimeoutExpired=TimeoutError)
    return cc, SimpleNamespace(subprocess=original)


def test_runtime_is_module_local_bounded_and_hides_subprocess(monkeypatch):
    cc, image = fake_modules()
    original_concurrent, original_subprocess = cc.concurrent, image.subprocess
    original_stdlib_pool = concurrent.futures.ThreadPoolExecutor
    original_stdlib_popen = subprocess.Popen
    monkeypatch.setattr(gf, "hidden_process_options", lambda: {"creationflags": 123, "startupinfo": "hidden"})
    with gf.gladtex_runtime(cc, image):
        assert cc.concurrent is not original_concurrent
        assert cc.concurrent.futures.as_completed is concurrent.futures.as_completed
        with cc.concurrent.futures.ThreadPoolExecutor(max_workers=60) as pool:
            assert pool._max_workers == 8
            assert pool.submit(lambda: 17).result() == 17
        with cc.concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            assert pool._max_workers == 3
        assert image.subprocess.PIPE is original_subprocess.PIPE
        assert image.subprocess.TimeoutExpired is TimeoutError
        args, kwargs = image.subprocess.Popen(["dvisvgm", "in.dvi"], cwd="tmp", stdout="pipe")
        assert args == (["dvisvgm", "in.dvi"],)
        assert kwargs == {"creationflags": 123, "startupinfo": "hidden", "cwd": "tmp", "stdout": "pipe"}
        assert concurrent.futures.ThreadPoolExecutor is original_stdlib_pool
        assert subprocess.Popen is original_stdlib_popen
    assert cc.concurrent is original_concurrent and image.subprocess is original_subprocess


@pytest.mark.parametrize("error", [ValueError("test"), SystemExit(91), KeyboardInterrupt()])
def test_runtime_restores_bindings_on_exception(error):
    cc, image = fake_modules()
    original_concurrent, original_subprocess = cc.concurrent, image.subprocess
    with pytest.raises(type(error)) as caught, gf.gladtex_runtime(cc, image):
        raise error
    assert caught.value is error
    assert cc.concurrent is original_concurrent and image.subprocess is original_subprocess


def test_partial_checkpoint_only_current_valid_finished_objects(tmp_path):
    root = tmp_path / "formulas"; root.mkdir()
    old, preserved, fresh, missing, broken, outside, unrelated = map(formula, range(7))
    oldsvg, presvg = root / "eqn000.svg", root / "eqn001.svg"
    oldsvg.write_bytes(SVG); presvg.write_bytes(SVG)
    cache_path = root / "gladtex.cache"
    cache_path.write_text(json.dumps({"GladTeX__cache__version":"2.0",old:item(oldsvg.name),preserved:item(presvg.name)}),encoding="utf8")
    gf.stamp_row_tag_svgs(cache_path,root,[(preserved,True)],lambda x:x)
    already=presvg.read_bytes()
    formulas=[(x,True) for x in (old,preserved,fresh,missing,broken,outside)]
    # Like the real entry, old or broken row-tag SVGs are invalidated before Main.
    assert gf.cache_statistics(cache_path,root,formulas,lambda x:x)==(1,5)
    assert not oldsvg.exists() and old not in json.loads(cache_path.read_text(encoding="utf8"))
    external=tmp_path/"outside.svg";external.write_bytes(SVG)
    freshpath=root/"eqn002.svg";freshpath.write_bytes(SVG)
    brokenpath=root/"eqn003.svg";brokenpath.write_text("<svg",encoding="utf8")
    unrelatedpath=root/"eqn004.svg";unrelatedpath.write_bytes(SVG)
    original_glyph=ET.fromstring(SVG).find(NS+"path").attrib
    cc,image=fake_modules();original_concurrent,original_subprocess=cc.concurrent,image.subprocess
    failure=SystemExit(91)
    class PartiallySuccessfulMain:
        def run(self, arguments):
            assert cc.concurrent is not original_concurrent and image.subprocess is not original_subprocess
            cache=json.loads(cache_path.read_text(encoding="utf8"))
            cache.update({fresh:item(freshpath.name),missing:item("not-created.svg"),broken:item(brokenpath.name),outside:item("../outside.svg"),unrelated:item(unrelatedpath.name)})
            cache_path.write_text(json.dumps(cache),encoding="utf8")
            raise failure
    with pytest.raises(SystemExit) as caught:
        gf.run_gladtex_with_checkpoint(PartiallySuccessfulMain(),["gladtex"],cc,image,cache_path,root,formulas,lambda x:x)
    assert caught.value is failure
    assert cc.concurrent is original_concurrent and image.subprocess is original_subprocess
    assert gf.valid_svg(freshpath,require_row_tags=True)
    assert ET.parse(freshpath).getroot().find(NS+"path").attrib == original_glyph
    assert presvg.read_bytes()==already
    assert external.read_bytes()==SVG and unrelatedpath.read_bytes()==SVG
    assert brokenpath.read_text(encoding="utf8")=="<svg" and not (root/"not-created.svg").exists()
    assert not oldsvg.exists()
    hits,misses=gf.cache_statistics(cache_path,root,formulas,lambda x:x)
    assert (hits,misses)==(2,4)
    assert external.read_bytes()==SVG


@pytest.mark.parametrize("box", ["a b c d", "0 0 NaN 1", "0 0 inf 1", "0 0 0 1", "0 0 -1 1"])
def test_partial_checkpoint_rejects_invalid_viewbox_without_writing(tmp_path, box):
    image=tmp_path/"eqn000.svg"
    original=SVG.replace(b'0 0 80 40',box.encode('ascii'))
    image.write_bytes(original)
    cache=tmp_path/"gladtex.cache"
    cache.write_text(json.dumps({ALIGN:item(image.name)}),encoding='utf8')
    assert gf.valid_svg(image), "The pre-existing broad check alone accepts four malformed tokens"
    gf.stamp_row_tag_svgs(cache,tmp_path,[(ALIGN,True)],lambda x:x,partial=True)
    assert image.read_bytes()==original


@pytest.mark.parametrize("bad", ["missing", "invalid", "outside"])
def test_successful_checkpoint_remains_strict(tmp_path,bad):
    root=tmp_path/"cache";root.mkdir();cache=root/"gladtex.cache"
    if bad=="missing":path="missing.svg"
    elif bad=="invalid":path="bad.svg";(root/path).write_text("<svg",encoding="utf8")
    else:path="../outside.svg";(tmp_path/"outside.svg").write_bytes(SVG)
    cache.write_text(json.dumps({ALIGN:item(path)}),encoding="utf8")
    with pytest.raises(ValueError):gf.stamp_row_tag_svgs(cache,root,[(ALIGN,True)],lambda x:x)


@pytest.mark.parametrize("original", [SystemExit(91), KeyboardInterrupt(), ValueError("original")])
def test_checkpoint_failure_warns_without_replacing_original(tmp_path,monkeypatch,capsys,original):
    cc,image=fake_modules();oc,op=cc.concurrent,image.subprocess
    class FailingMain:
        def run(self,arguments):raise original
    def broken_checkpoint(*args,**kwargs):raise RuntimeError("checkpoint I/O failed")
    monkeypatch.setattr(gf,"stamp_row_tag_svgs",broken_checkpoint)
    with pytest.raises(type(original)) as caught:
        gf.run_gladtex_with_checkpoint(FailingMain(),[],cc,image,tmp_path/"gladtex.cache",tmp_path,[(ALIGN,True)],lambda x:x)
    assert caught.value is original
    assert "WARNING:" in capsys.readouterr().err
    assert cc.concurrent is oc and image.subprocess is op


def test_absent_partial_cache_is_a_noop_and_strict_success_requires_entry(tmp_path):
    cache=tmp_path/"gladtex.cache"
    gf.stamp_row_tag_svgs(cache,tmp_path,[(ALIGN,True)],lambda x:x,partial=True)
    assert not cache.exists()
    with pytest.raises(FileNotFoundError):gf.stamp_row_tag_svgs(cache,tmp_path,[(ALIGN,True)],lambda x:x)
    cache.write_text('{}',encoding='utf8')
    with pytest.raises(KeyError):gf.stamp_row_tag_svgs(cache,tmp_path,[(ALIGN,True)],lambda x:x)


def nodes(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from nodes(v)
    elif isinstance(value,list):
        for v in value:yield from nodes(v)


def signature_rows(root, digits):
    box=[float(v) for v in root.get('viewBox').split()]
    uses=[]
    for node in root.iter(NS+'use'):
        ref=node.get('{http://www.w3.org/1999/xlink}href','')
        m=re.fullmatch(r'#g\d+-(\d+)',ref)
        if m:uses.append((int(m.group(1)),float(node.get('x','0')),float(node.get('y','0'))))
    out=[]
    for i in range(len(uses)-len(digits)+1):
        seq=uses[i:i+len(digits)]
        if [v[0] for v in seq]==digits and max(v[2] for v in seq)-min(v[2] for v in seq)<.02 and all(a[1]<b[1] for a,b in pairwise(seq)) and box[0]+box[2]-seq[-1][1]<20:
            out.append(seq[0][2])
    return out


@pytest.mark.skipif(not all(shutil.which(t) for t in ('latex','dvisvgm')) or __import__('importlib').util.find_spec('gleetex') is None,reason='Pinned GladTeX/TeX tools required')
def test_actual_failed_batch_is_reusable_without_changing_row_tag_glyphs(tmp_path):
    # Exercise the real library exception, executor shutdown, partial stamp and
    # a later cache hit. Both original tag rows are checked via SVG glyph IDs.
    doc={'pandoc-api-version':[1,23,1,2],'meta':{},'blocks':[{'t':'Para','c':[{'t':'Math','c':[{'t':'DisplayMath'},x]}]} for x in (ALIGN,r'\DefinitelyUnknownCommandForTest')]}
    (tmp_path/'input.json').write_text(json.dumps(doc),encoding='utf8')
    env=os.environ.copy();env.update(TEMP=str(tmp_path),TMP=str(tmp_path),PYTHONDONTWRITEBYTECODE='1',PYTHONWARNINGS='ignore::RuntimeWarning')
    command=[sys.executable,str(Path(gf.__file__).resolve()),'input.json','output.json','.']
    run=subprocess.run(command,cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=60,check=False,**hidden_process_options())
    assert run.returncode != 0
    cache=json.loads((tmp_path/'gladtex.cache').read_text(encoding='utf8'))
    assert ALIGN in cache and cache[ALIGN]['true']['path']
    image=tmp_path/cache[ALIGN]['true']['path'];assert gf.valid_svg(image,require_row_tags=True)
    root=ET.parse(image).getroot()
    first=signature_rows(root,[40,49,46,49,41]);second=signature_rows(root,[40,49,46,50,41])
    assert len(first)==len(second)==1 and first[0]<second[0]
    doc['blocks'].pop();(tmp_path/'input.json').write_text(json.dumps(doc),encoding='utf8')
    retry=subprocess.run(command,cwd=tmp_path,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=30,check=False,**hidden_process_options())
    assert retry.returncode == 0, retry.stderr
    assert 'svg_math_cache_hits=1' in retry.stdout and 'svg_math_cache_misses=0' in retry.stdout
    output=json.loads((tmp_path/'output.json').read_text(encoding='utf8'));images=[v for v in nodes(output) if v.get('t')=='Image'];assert len(images)==1
    alt=images[0]['c'][1][0]['c'];assert '1.1' in alt and '1.2' in alt
