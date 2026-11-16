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
from tools import epub_semantics as semantics
from tools.browser_runtime import AgentBrowserSession

ROOT = Path(__file__).resolve().parents[1]
SOURCE = """::: {.code-table}

| 操作说明 | NumPy | MATLAB |
| --- | --- | --- |
| 定义二维数组，说明文字可以正常换行 | `np.array([[1., 2., 3.], [4., 5., 6.]])` | `[ 1 2 3; 4 5 6 ]` |
| 保留原切片及其末字符 | `a[np.ix_([1, 3, 4], [0, 2])]` | `a([2,4,5],[1,3])` [定位](#after) |

: 代码对照表

:::

正文中的 `np.array([1, 2])` 仍是普通行内代码。

| 普通表 | 值 |
| --- | --- |
| A | 1 |

::: {.code-example}
| `macro(x)`
| `macro(y)`
:::

# 后续 {#after}
"""


def render_fixture() -> tuple[dict, str]:
    parsed = subprocess.run(
        ["pandoc", "-f", "markdown+fenced_divs+fancy_lists", "-t", "json"],
        input=SOURCE,
        text=True, encoding="utf-8", capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    rendered = subprocess.run(
        ["pandoc", "-f", "json", "-t", "html"],
        input=parsed.stdout, text=True, encoding="utf-8",
        capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    document = json.loads(parsed.stdout)
    return document, rendered.stdout


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_code_table_preserves_native_table_without_weakening_rich_contract(tmp_path):
    document, body = render_fixture()
    wrapper = ET.fromstring("<body>" + body + "</body>")
    table_container = wrapper.find("div[@class='code-table']")
    assert table_container is not None
    table = table_container.find("table")
    assert table is not None and table.find("caption").text == "代码对照表"
    assert [node.text for node in table.iter("code")] == [
        "np.array([[1., 2., 3.], [4., 5., 6.]])",
        "[ 1 2 3; 4 5 6 ]",
        "a[np.ix_([1, 3, 4], [0, 2])]",
        "a([2,4,5],[1,3])",
    ]
    assert table.find(".//a").get("href") == "#after"
    records = semantics.canonical_code(document)
    assert len(records) == 1 and records[0].kind == "code-example"
    semantics.validate_code_records(
        records, semantics.code_from_xhtml([wrapper])
    )
    for node in document["blocks"]:
        if node["t"] == "Div" and "code-table" in node["c"][0][1]:
            node["c"][0][1] = ["code-example"]
    with pytest.raises(semantics.EpubSemanticError, match="LineBlock"):
        semantics.canonical_code(document)


@pytest.mark.skipif(
    not (shutil.which("pandoc") and
         (os.getenv("TRANSLATOR_AGENT_BROWSER") or shutil.which("agent-browser"))),
    reason="Pandoc and agent-browser required",
)
def test_code_table_readability_and_local_scroll_in_three_modes(tmp_path):
    _, body = render_fixture()
    css = (ROOT / "formats/epub/book.css").read_text("utf-8")
    page = tmp_path / "code-table.xhtml"
    page.write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>Code tables</title><style>' + css + '</style></head><body>'
        + body + '</body></html>', encoding="utf-8"
    )
    keys = (
        "PATH", "SystemRoot", "WINDIR", "USERPROFILE", "LOCALAPPDATA",
        "APPDATA", "COMSPEC", "TRANSLATOR_AGENT_BROWSER",
        "TRANSLATOR_BROWSER_EXECUTABLE", "AGENT_BROWSER_EXECUTABLE_PATH",
    )
    environment = {key: value for key in keys if (value := os.getenv(key))}
    runtime = AgentBrowserSession(
        "translator-fa3bb6797355", "code-table-" + str(time.time_ns()),
        scratch=tmp_path / "runtime", environment=environment,
    )
    evidence = []

    def command(arguments, capture=True):
        result = runtime.run(arguments, capture=capture)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def evaluate(script):
        return qa.parse_json_output(command(["eval", script]))

    try:
        command(["open", "about:blank"], capture=False)
        for mode in qa.default_modes():
            width, height = mode["viewport"]
            harness = tmp_path / (mode["name"] + ".html")
            qa.write_harness(
                harness, [page.name], width, height, 10, mode,
                base_prefix=tmp_path.as_uri() + "/",
            )
            command(["set", "viewport", str(width), str(height)])
            command(["set", "media", mode["color_scheme"]])
            command(["open", harness.as_uri()])
            result = evaluate(
                "window.qaShowPage(" + json.dumps(page.as_uri()) + ")"
            )
            detail = evaluate(r"""(() => {
              const d=frame.contentDocument, c=d.querySelector('.code-table');
              const t=c.querySelector('table'), codes=[...t.querySelectorAll('code')];
              const label=t.querySelector('td'), labelStyle=getComputedStyle(label);
              const lines=n=>{const r=d.createRange();r.selectNodeContents(n);
                return new Set([...r.getClientRects()].map(b=>b.top.toFixed(1))).size;};
              const normal=d.querySelector('body > p code');
              const ports=[c,t].filter(n=>getComputedStyle(n).overflowX==='auto'
                && n.scrollWidth>n.clientWidth+1);
              const last=codes.at(-1); last.scrollIntoView({inline:'end',block:'nearest'});
              for(const port of [...ports].reverse()) port.scrollLeft=port.scrollWidth;
              const r=d.createRange(), text=last.firstChild;
              r.setStart(text,text.length-1);r.setEnd(text,text.length);
              const edge=r.getBoundingClientRect();
              const clips=[{left:0,right:d.documentElement.clientWidth}];
              for(let n=last.parentElement;n;n=n.parentElement){
                const s=getComputedStyle(n);
                if(['auto','scroll','hidden','clip','overlay'].includes(s.overflowX)){
                  const b=n.getBoundingClientRect();
                  clips.push({left:b.left+n.clientLeft,
                    right:b.left+n.clientLeft+n.clientWidth});
                }
              }
              const fullyContained=box=>clips.every(c=>box.left>=c.left&&box.right<=c.right);
              const clipLeft=Math.max(...clips.map(c=>c.left));
              const clipRight=Math.min(...clips.map(c=>c.right));
              return {code:codes.map(n=>n.textContent), lines:codes.map(lines),
                codeWhiteSpace:codes.map(n=>getComputedStyle(n).whiteSpace),
                descriptionWhiteSpace:getComputedStyle(t.querySelector('td')).whiteSpace,
                descriptionWidth:t.querySelector('td').getBoundingClientRect().width,
                descriptionContentWidth:label.clientWidth-parseFloat(labelStyle.paddingLeft)
                  -parseFloat(labelStyle.paddingRight),
                descriptionFontSize:parseFloat(labelStyle.fontSize),
                descriptionWordBreak:labelStyle.wordBreak,
                descriptionLines:lines(label),
                ordinaryColumnMinWidth:getComputedStyle(d.querySelector('body > table th')).minWidth,
                ordinaryWhiteSpace:getComputedStyle(normal).whiteSpace,
                tables:d.querySelectorAll('table').length,
                caption:t.querySelector('caption').textContent,
                ports:ports.map(n=>({clientWidth:n.clientWidth,scrollWidth:n.scrollWidth,
                  scrollLeft:n.scrollLeft})),
                lastCharacterReachable:fullyContained(edge),
                lastCharacterBounds:{left:edge.left,right:edge.right},clipBounds:clips,
                partialGlyphsRejected:!fullyContained({left:clipRight-2,right:clipRight+2})
                  &&!fullyContained({left:clipLeft-2,right:clipLeft+2}),
                pageWidth:d.documentElement.scrollWidth,viewWidth:d.documentElement.clientWidth};
            })()""")
            evidence.append({"mode": mode["name"], "detail": detail})
            assert result["overflowCount"] == 0, result["overflowElements"]
            assert detail["tables"] == 2 and detail["caption"] == "代码对照表"
            assert detail["lines"] == [1, 1, 1, 1]
            assert detail["codeWhiteSpace"] == ["pre"] * 4
            assert detail["descriptionWhiteSpace"] == "normal"
            assert detail["descriptionWordBreak"] == "normal"
            assert detail["descriptionContentWidth"] >= 4 * detail["descriptionFontSize"] - 1
            assert detail["ordinaryColumnMinWidth"] in ("0px", "auto")
            assert detail["ordinaryWhiteSpace"] == "normal"
            assert detail["lastCharacterReachable"]
            assert detail["partialGlyphsRejected"]
            if width == 390:
                assert detail["ports"]
                assert detail["descriptionLines"] > 1
            assert detail["code"] == [
                "np.array([[1., 2., 3.], [4., 5., 6.]])",
                "[ 1 2 3; 4 5 6 ]",
                "a[np.ix_([1, 3, 4], [0, 2])]",
                "a([2,4,5],[1,3])",
            ]
            if mode["disable_publisher_css"]:
                assert result["readerSettings"]["codeScrollingApplied"] is True
    finally:
        closed = runtime.run(["close"])
        (tmp_path / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert closed.returncode == 0, closed.stderr
