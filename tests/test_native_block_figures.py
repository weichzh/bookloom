from __future__ import annotations

import base64
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_semantics, translator

ROOT = Path(translator.__file__).resolve().parents[1]
FILTER = Path(__file__).resolve().parents[1] / "formats/pandoc/semantics.lua"
CSS = Path(__file__).resolve().parents[1] / "formats/epub/book.css"
READER = "markdown+fenced_divs+bracketed_spans+footnotes-raw_html"
H = "{http://www.w3.org/1999/xhtml}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc required")

SOURCE = r'''# 例章 {#chapter}

:::: {#fig-rules .native-figure .special data-figure-body="blocks" data-origin="PDF360"}

**语义域** [正文链接](#chapter)。[]{.source-page data-page="360"}

$$
\frac{E_1 \to E'_1}{E_1+E_2 \to E'_1+E_2}
$$

```text
if x != 0:
  run(x)
```

::: {.code-example}
| `var c`^global:0^`:Nat;`
|   `x := 1;`
:::

- 一条规则
- 另一条规则

| 名称 | 值 |
|:-----|:---|
| a    | 1  |

::: {#body-container .group}
包含 [链接](#chapter) 的语义块。[]{#body-page epub:type="pagebreak" title="5" role="doc-pagebreak"}
:::

::: {#caption-rule .figure-caption}
图 7.8 表达式规则。[]{#caption-page epub:type="pagebreak" title="6" role="doc-pagebreak"}
:::

::::

![图 4.1 积与和](sample.png){#fig-image alt="图 4.1：积与和的交换图"}
'''


def read(source: str = SOURCE, target: str = "json", reader: str = READER) -> str:
    return translator.run([
        "pandoc", "--from=" + reader, "--to=" + target,
        "--lua-filter=" + str(FILTER),
    ], input_text=source).stdout


def nodes(value):
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def figures(document):
    return {x["c"][0][0]: x for x in nodes(document) if x["t"] == "Figure"}


def assert_figure_dom(document, namespace=H):
    named = {x.get("id"): x for x in document.iter(namespace + "figure")}
    assert set(named) == {"fig-rules", "fig-image"}
    rules, image = named["fig-rules"], named["fig-image"]
    assert rules.get("data-origin") == "PDF360"
    assert rules.get("data-figure-body") == "blocks"
    assert "special" in rules.get("class", "").split()
    assert len(rules.findall(namespace + "figcaption")) == 1
    caption = rules.find(namespace + "figcaption")
    assert "图 7.8 表达式规则" in "".join(caption.itertext())
    assert caption.get("aria-hidden") != "true"
    assert caption.find(".//" + namespace + "span[@id='caption-page']") is not None
    assert rules.find(".//" + namespace + "span[@id='body-page']") is not None
    assert rules.find(".//" + namespace + "div[@id='body-container']") is not None
    assert rules.find(".//" + namespace + "a").get("href").endswith("#chapter")
    assert len(list(rules.iter(namespace + "sup"))) == 1
    assert image.find(".//" + namespace + "img").get("alt") == "图 4.1：积与和的交换图"
    assert len(image.findall(namespace + "figcaption")) == 1


def test_blocks_mode_real_reader_and_writer_preserve_block_content():
    document = json.loads(read())
    by_id = figures(document)
    assert set(by_id) == {"fig-rules", "fig-image"}
    attr, caption, body = by_id["fig-rules"]["c"]
    assert attr == ["fig-rules", ["native-figure", "special"],
                    [["data-figure-body", "blocks"], ["data-origin", "PDF360"]]]
    assert [block["t"] for block in body] == [
        "Para", "Para", "CodeBlock", "Div", "BulletList", "Table", "Div"
    ]
    assert [block["t"] for block in caption[1]] == ["Div"]
    maths = [x["c"][1] for x in nodes(body) if x["t"] == "Math"]
    assert maths == [r"\frac{E_1 \to E'_1}{E_1+E_2 \to E'_1+E_2}"]
    assert [x["c"][1] for x in nodes(body) if x["t"] == "CodeBlock"] == [
        "if x != 0:\n  run(x)"
    ]
    html = read(target="html5")
    root = ET.fromstring('<html xmlns:epub="http://www.idpf.org/2007/ops"><body>' + html + '</body></html>')
    assert_figure_dom(root, "")
    assert '<span class="source-page" data-page="360"></span>' in html
    assert "if x != 0:\n  run(x)" in html


def test_blocks_mode_svg_code_and_pagepoint_seam(tmp_path):
    entry = tmp_path / "main.md"
    entry.write_text(SOURCE, encoding="utf-8")
    (tmp_path / "sample.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
    ))
    output = tmp_path / "figure.epub"
    source = ["pandoc", str(entry), "--from=" + READER,
              "--lua-filter=" + str(FILTER), "--resource-path=" + str(tmp_path)]
    source_ast = json.loads(translator.run([*source, "--to=json"]).stdout)
    translator.build_svg_math_epub(
        root=ROOT, work_path=tmp_path, entry=entry, source_command=source,
        writer_options=["--metadata=title:Block figure", "--metadata=lang:zh-CN",
                        "--toc", "--toc-depth=2", "--epub-title-page=false",
                        "--css=" + str(CSS)],
        source_cwd=ROOT, output=output, toc_depth=2, bodymatter="chapter",
    )
    with zipfile.ZipFile(output) as archive:
        docs = [ET.fromstring(archive.read(n)) for n in archive.namelist()
                if n.endswith(".xhtml") and not n.endswith("nav.xhtml")]
    doc = next(d for d in docs if any(e.get("id") == "fig-rules" for e in d.iter()))
    assert_figure_dom(doc)
    figure = next(e for e in doc.iter(H + "figure") if e.get("id") == "fig-rules")
    math = [e for e in figure.iter(H + "img") if "math-display" in e.get("class", "").split()]
    assert len(math) == 1 and math[0].get("alt") == r"\frac{E_1 \to E'_1}{E_1+E_2 \to E'_1+E_2}"
    assert figure.find(".//" + H + "pre").find(H + "code").text == "if x != 0:\n  run(x)"
    assert next(e for e in figure.iter(H + "span") if e.get("id") == "body-page").get(EPUB_TYPE) == "pagebreak"
    assert next(e for e in figure.iter(H + "span") if e.get("id") == "caption-page").get(EPUB_TYPE) == "pagebreak"
    epub_semantics.validate_code_records(epub_semantics.canonical_code(source_ast),
                                         epub_semantics.code_from_xhtml(docs))


def wrap(body: str, *, attributes: str = 'data-figure-body="blocks"', caption: str = "Caption") -> str:
    tail = f'::: {{.figure-caption}}\n\n{caption}\n\n:::' if caption is not None else ""
    return f'::::: {{.native-figure {attributes}}}\n\n{body}\n\n{tail}\n\n:::::\n'


@pytest.mark.parametrize("body,caption,message", [
    ("Some content", None, "exactly one figure-caption"),
    ("Some content", "", "must not be empty"),
    ("Some content", " ", "must not be empty"),
    ("Some content", '[]{.source-page data-page="1"}', "must not be empty"),
    ("", "Caption", "blocks body must be meaningful"),
    (" ", "Caption", "blocks body must be meaningful"),
    ('[]{.source-page data-page="1"}', "Caption", "blocks body must be meaningful"),
    ('[]{#page epub:type="pagebreak" title="1"}', "Caption", "blocks body must be meaningful"),
    ("```text\n   \n```", "Caption", "blocks body must be meaningful"),
    ("![Nested](sample.png)", "Caption", "nested Figure"),
])
def test_invalid_block_bodies_and_captions_fail_in_reader_and_writer(body, caption, message):
    source = wrap(body, caption=caption)
    for target in ("json", "html5"):
        with pytest.raises(translator.CliError, match=message):
            read(source, target)


def test_generated_raw_source_page_does_not_block_meaningful_body():
    source = wrap('Text.[]{.source-page data-page="7"}')
    assert "Figure" in read(source, "json")
    assert '<span class="source-page" data-page="7"></span>' in read(source, "html5")


def test_horizontal_rule_is_a_meaningful_body():
    document = json.loads(read(wrap('---')))
    figure = next(iter(figures(document).values()))
    assert [x['t'] for x in figure['c'][2]] == ['HorizontalRule']
    assert '<hr' in read(wrap('---'), 'html5')


@pytest.mark.parametrize('attributes', [
    'hidden="hidden"', 'hidden="false"', 'aria-hidden="TrUe"',
    'style="DISPLAY: none !important"', 'style="visibility: collapse"',
    'style="content-visibility: hidden"', 'style="opacity: 0.0%"',
])
@pytest.mark.parametrize('where', ['figure', 'caption'])
def test_explicit_hiding_on_figure_or_caption_is_rejected(attributes, where):
    source = wrap('Body', attributes='data-figure-body="blocks" ' + attributes
                  if where == 'figure' else 'data-figure-body="blocks"')
    if where == 'caption':
        source = source.replace('::: {.figure-caption}', '::: {.figure-caption ' + attributes + '}')
    for target in ('json', 'html5'):
        with pytest.raises(translator.CliError, match='cannot hide caption content'):
            read(source, target)


@pytest.mark.parametrize('caption', [
    'Visible [hidden text]{hidden="hidden"}.',
    'Visible [hidden link](#anchor){aria-hidden="true"}.',
    'Visible [`hidden code`]{style="display:none"}.',
    'Visible ![hidden image](sample.png){style="visibility:hidden"}.',
    ':::: {.container style="opacity:0"}\n\nHidden container.\n\n::::',
])
def test_explicit_hiding_of_meaningful_nested_caption_content_is_rejected(caption):
    with pytest.raises(translator.CliError, match='cannot hide caption content'):
        read(wrap('Body', caption=caption))


def test_hidden_empty_source_point_is_not_visible_caption_content():
    source = wrap('Body', caption='Caption.[]{#page .source-page data-page="7" '
                                  'hidden="hidden" aria-hidden="true" style="display:none"}')
    assert 'Figure' in read(source, 'json')
    assert '<figcaption>' in read(source, 'html5')
    assert 'Figure' in read(wrap('Body', caption='[Caption]{aria-hidden="false" style="display:block"}'))


@pytest.mark.parametrize('where', ['head', 'head_row', 'head_cell', 'body', 'body_row', 'body_cell'])
def test_hidden_nonempty_table_parts_in_caption_are_rejected(where):
    source = wrap('Body', caption='| column |\n|--------|\n| value  |')
    document = json.loads(translator.run(['pandoc', '--from=' + READER, '--to=json'],
                                         input_text=source).stdout)
    table = next(x for x in nodes(document) if x['t'] == 'Table')['c']
    head, body = table[3], table[4][0]
    attrs = {'head': head[0], 'head_row': head[1][0][0],
             'head_cell': head[1][0][1][0][0], 'body': body[0],
             'body_row': body[3][0][0], 'body_cell': body[3][0][1][0][0]}[where]
    attrs[2].append(['hidden', 'hidden'])
    with pytest.raises(translator.CliError, match='cannot hide caption content'):
        translator.run(['pandoc', '--from=json', '--to=html5', '--lua-filter=' + str(FILTER)],
                       input_text=json.dumps(document))


def test_unknown_mode_and_legacy_missing_image_rejected():
    with pytest.raises(translator.CliError, match="data-figure-body must be blocks"):
        read(wrap("Visible body", attributes='data-figure-body="something-else"'))
    with pytest.raises(translator.CliError, match="body requires an image"):
        read(wrap("Visible body", attributes=""))


@pytest.mark.parametrize("body", [
    'Text with <kbd>raw inline</kbd>.',
    '<script>alert(1)</script>',
])
def test_blocks_mode_rejects_authored_raw_nodes(body):
    with pytest.raises(translator.CliError, match="blocks mode does not support Raw"):
        read(wrap(body), reader="markdown+fenced_divs+bracketed_spans+footnotes+raw_html")


def test_blocks_mode_rejects_raw_caption_too():
    with pytest.raises(translator.CliError, match="blocks mode does not support RawInline"):
        read(wrap("Visible body", caption='A <kbd>raw</kbd> caption'),
             reader="markdown+fenced_divs+bracketed_spans+footnotes+raw_html")


def test_source_unit_cannot_be_hidden_inside_block_figure():
    source = (':::: {.native-figure .source-unit data-unit="1" data-figure-body="blocks"}\n\n'
              'Body\n\n::: {.figure-caption}\n\nCaption\n\n:::\n\n::::\n')
    with pytest.raises(translator.CliError, match="source-unit Div is metadata only"):
        read(source)


@pytest.mark.parametrize("body", [
    'Body\n\n::: {.figure-caption}\n\nCaption one\n\n:::\n\n::: {.figure-caption}\n\nCaption two\n\n:::',
    'Body\n\n::: {.figure-caption}\n\nCaption\n\n:::\n\nAfter caption',
])
def test_caption_is_unique_direct_and_last(body):
    source = '::::: {.native-figure data-figure-body="blocks"}\n\n' + body + '\n\n:::::\n'
    with pytest.raises(translator.CliError, match="exactly one figure-caption|must be the final block"):
        read(source)


def test_nested_caption_is_rejected_in_blocks_mode():
    body = (':::: {.body-wrapper}\n\nInner\n\n'
            '::: {.figure-caption}\n\nHidden caption\n\n:::\n\n::::')
    with pytest.raises(translator.CliError, match="exactly one figure-caption"):
        read(wrap(body))


def test_nested_explicit_blocks_figure_is_rejected():
    inner = (':::: {.native-figure data-figure-body="blocks"}\n\nInner body\n\n'
             '::: {.figure-caption}\n\nInner caption\n\n:::\n\n::::')
    with pytest.raises(translator.CliError, match="nested Figure"):
        read(wrap(inner))


def test_legacy_image_mode_still_accepts_native_spans_and_raw_html():
    source = wrap('[![Alt](sample.png)]{.source-image}\n\nText <kbd>raw</kbd>.', attributes="")
    document = json.loads(read(source, reader="markdown+fenced_divs+bracketed_spans+footnotes+raw_html"))
    assert len(figures(document)) == 1


def test_legacy_image_mode_does_not_gain_new_visibility_preconditions():
    source = wrap('[![Alt](sample.png)]{.source-image}', attributes='hidden="hidden"')
    source = source.replace('::: {.figure-caption}', '::: {.figure-caption aria-hidden="true"}')
    assert len(figures(json.loads(read(source)))) == 1


def test_wide_block_figure_math_is_readable_and_locally_scrollable(tmp_path):
    """The block override preserves aspect and does not alter image figures."""
    from tools import epub_browser_qa
    from tools.browser_runtime import AgentBrowserSession

    shutil.copyfile(CSS, tmp_path / "book.css")
    (tmp_path / "wide.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="39" '
        'viewBox="0 0 600 39"><text x="0" y="27">wide rule</text></svg>',
        encoding="utf-8",
    )
    page = tmp_path / "reader.xhtml"
    page.write_text(
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<link rel="stylesheet" href="book.css"/></head><body>'
        '<figure id="block" class="native-figure" data-figure-body="blocks">'
        '<p>规则</p><div class="display-formula"><span class="display-formula">'
        '<img class="math-display" src="wide.svg" width="600" height="39" '
        'style="height:3.914em;width:auto" alt="wide rule"/></span></div>'
        '<figcaption>图 7.8</figcaption></figure>'
        '<figure id="control"><img src="wide.svg" alt="image control"/>'
        '<figcaption>图 4.1</figcaption></figure></body></html>',
        encoding="utf-8",
    )
    browser = AgentBrowserSession("native-block-figure-test", "fit", root=ROOT,
                                  scratch=tmp_path / "runtime")
    try:
        assert browser.run(["open", "about:blank"], capture=False).returncode == 0
        assert browser.run(["set", "viewport", "390", "844"]).returncode == 0
        assert browser.run(["open", page.as_uri()]).returncode == 0
        result = browser.run(["eval", r'''(() => {
          const figure=document.querySelector('#block'), image=figure.querySelector('img'),
                control=document.querySelector('#control');
          function measure() {
            const ancestors=[];
            for (let node=image.parentElement; node && node!==document.documentElement;
                 node=node.parentElement) {
              const overflow=getComputedStyle(node).overflowX;
              if (['auto','scroll','hidden','clip'].includes(overflow))
                ancestors.push({node,overflow});
            }
            const ports=ancestors.filter(({node,overflow}) =>
              ['auto','scroll'].includes(overflow) && node.scrollWidth>node.clientWidth+1);
            function endpoint(side) {
              for (const {node} of ports)
                node.scrollLeft=side==='left' ? 0 : node.scrollWidth;
              const rect=image.getBoundingClientRect(), style=getComputedStyle(image);
              const edge=side==='left'
                ? rect.left+parseFloat(style.borderLeftWidth)+parseFloat(style.paddingLeft)
                : rect.right-parseFloat(style.borderRightWidth)-parseFloat(style.paddingRight);
              const clips=ancestors.map(({node,overflow}) => {
                const bounds=node.getBoundingClientRect(), left=bounds.left+node.clientLeft;
                return {overflow,left,right:left+node.clientWidth};
              });
              clips.push({overflow:'viewport',left:0,right:window.innerWidth});
              return {edge,clips,visible:clips.every(({left,right}) => edge>=left-1 && edge<=right+1),
                      scrolls:ports.map(({node}) => node.scrollLeft)};
            }
            const rect=image.getBoundingClientRect(), style=getComputedStyle(image),
                  width=rect.width-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight),
                  height=rect.height-parseFloat(style.paddingTop)-parseFloat(style.paddingBottom);
            return {page:document.documentElement.scrollWidth,
                    align:getComputedStyle(figure).textAlign,
                    controlAlign:getComputedStyle(control).textAlign,
                    controlMax:getComputedStyle(control.querySelector('img')).maxWidth,
                    naturalRatio:image.naturalWidth/image.naturalHeight,ratio:width/height,
                    ports:ports.map(({node,overflow}) => ({overflow,width:node.scrollWidth,client:node.clientWidth})),
                    left:endpoint('left'),right:endpoint('right')};
          }
          const positive=measure();
          for (const node of figure.querySelectorAll('.display-formula'))
            node.style.setProperty('overflow-x','hidden','important');
          return {positive,hidden:measure()};
        })()'''])
        assert result.returncode == 0, result.stderr
        measured = epub_browser_qa.parse_json_output(result.stdout)
        actual = measured["positive"]
        assert actual["page"] == 390
        assert actual["align"] in ("start", "left")
        assert actual["controlAlign"] == "center" and actual["controlMax"] == "100%"
        assert actual["ports"] and all(port["width"] > port["client"] for port in actual["ports"])
        assert actual["left"]["visible"] and actual["right"]["visible"]
        assert all(value == 0 for value in actual["left"]["scrolls"])
        assert any(value > 0 for value in actual["right"]["scrolls"])
        assert not measured["hidden"]["ports"]
        assert actual["ratio"] == pytest.approx(actual["naturalRatio"], rel=0.02)
    finally:
        browser.run(["close"], capture=False)
