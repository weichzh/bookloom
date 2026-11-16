from __future__ import annotations

import base64
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import epub_page_navigation as pages
from tools import translator


def package(
    body: str, *, split: str = ""
) -> tuple[dict[str, ET.Element], dict[str, bytes]]:
    payloads = {
        "META-INF/container.xml": (
            b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            b'<rootfiles><rootfile full-path="EPUB/package.opf" '
            b'media-type="application/oebps-package+xml"/></rootfiles></container>'
        ),
        "EPUB/package.opf": (
            f'<package xmlns="{pages.OPF}" version="3.0">'
            f'<metadata xmlns:dc="{pages.DC}"><dc:title>Test</dc:title></metadata>'
            '<manifest><item id="nav" href="navigation.xhtml" properties="nav" '
            'media-type="application/xhtml+xml"/><item id="ch" href="text/one.xhtml" '
            'media-type="application/xhtml+xml"/>'
            + (
                '<item id="two" href="text/two.xhtml" media-type="application/xhtml+xml"/>'
                if split
                else ""
            )
            + '</manifest><spine><itemref idref="nav"/><itemref idref="ch"/>'
            + ('<itemref idref="two"/>' if split else "")
            + "</spine></package>"
        ).encode(),
    }
    documents = {
        "EPUB/navigation.xhtml": ET.fromstring(
            f'<html xmlns="{pages.XHTML}" xmlns:epub="{pages.EPUB}"><head><title>Test</title></head><body>'
            '<nav epub:type="toc"><h1>Contents</h1><ol><li><a href="text/one.xhtml#chapter">Chapter</a></li></ol></nav>'
            "</body></html>"
        ),
        "EPUB/text/one.xhtml": ET.fromstring(
            f'<html xmlns="{pages.XHTML}" xmlns:epub="{pages.EPUB}"><head><title>Test</title></head><body>'
            f'<h1 id="chapter">Chapter</h1>{body}</body></html>'
        ),
    }
    if split:
        documents["EPUB/text/two.xhtml"] = ET.fromstring(
            f'<html xmlns="{pages.XHTML}"><head><title>Two</title></head><body>{split}</body></html>'
        )
    return documents, payloads


def config(*locations: pages.PageLocation) -> pages.PageNavigationConfig:
    return pages.PageNavigationConfig(
        "urn:sha256:" + "a" * 64,
        "Test, verified PDF edition",
        "zh-CN",
        tuple(locations),
    )


def marker(number: int) -> str:
    return f'<span class="source-page" data-page="{number}" />'


H = "{" + pages.XHTML + "}"
TYPE = pages.EPUB_TYPE


def item(n, *, position="marker", target=None, after=None, reason=None):
    return pages.PageLocation(
        n, str(n), "printed", position, target=target, after_page=after, reason=reason
    )


def point_order(docs):
    return [
        int(node.get("data-page"))
        for name in ("EPUB/text/one.xhtml", "EPUB/text/two.xhtml")
        if name in docs
        for node in docs[name].iter()
        if node.get(TYPE) == "pagebreak"
    ]


def page_links(docs):
    nav = next(
        node
        for node in docs["EPUB/navigation.xhtml"].iter()
        if node.get(TYPE) == "page-list"
    )
    return [(node.text, node.get("href")) for node in nav.iter(H + "a")]


def setup_single(*, deferred=True):
    body = (
        '<p id="p">开始<em>'
        + marker(1)
        + '前<span class="source-page" id="old-page-2" data-page="2"/>续'
        + marker(3)
        + '结尾</em>。<a href="#old-page-2">原页链接</a></p>'
        + '<figure id="figure"><img src="image.png" alt="figure"/>'
        "<figcaption>原图注</figcaption></figure><p>" + marker(4) + "后文</p>"
    )
    docs, payloads = package(body)
    if deferred:
        settings = config(
            item(1),
            item(
                2,
                position="before-target",
                target="figure",
                after=3,
                reason="图页延后至完整段落后",
            ),
            item(3),
            item(4),
        )
    else:
        settings = config(
            item(1),
            item(2, position="before-target", target="figure"),
            item(3),
            item(4),
        )
    return docs, payloads, settings


def test_single_figure_stays_after_unbroken_paragraph_while_pagelist_stays_source_order():
    docs, payloads, settings = setup_single()
    root = docs["EPUB/text/one.xhtml"]
    before = "".join(root.itertext())
    result = pages.apply_page_navigation(docs, payloads, settings)
    assert "".join(root.itertext()) == before
    para = next(node for node in root.iter() if node.get("id") == "p")
    assert "".join(para.itertext()) == "开始前续结尾。原页链接"
    assert point_order(docs) == [1, 3, 2, 4]
    assert [x[0] for x in page_links(docs)] == ["1", "2", "3", "4"]
    assert page_links(docs)[1][1].endswith("text/one.xhtml#old-page-2")
    assert [x[0] for x in result.targets] == [1, 2, 3, 4]
    assert result.targets[1][2] == "old-page-2"
    assert (
        next(node for node in root.iter(H + "a") if node.text == "原页链接").get("href")
        == "#old-page-2"
    )
    assert pages.validate_page_navigation(docs, payloads, settings) == result
    # An existing caller without configuration still applies the strict legacy rule.
    with pytest.raises(pages.PageNavigationError):
        pages.validate_page_navigation(docs, payloads)


def test_two_deferred_objects_share_anchor_preserve_source_order_and_table_figure():
    body = (
        '<p id="p">前' + marker(1) + marker(2) + marker(3) + marker(4) + "续。</p>"
        '<figure id="fig"><img src="x" alt="x"/><figcaption>甲图注</figcaption></figure>'
        '<table id="tab"><caption>乙表题</caption><tbody><tr><td>格值</td></tr></tbody></table>'
        "<p>" + marker(5) + "后</p>"
    )
    docs, payloads = package(body)
    root = docs["EPUB/text/one.xhtml"]
    before = "".join(root.itertext())
    settings = config(
        item(1),
        item(2, position="before-target", target="fig", after=4, reason="图页"),
        item(3, position="before-target", target="tab", after=4, reason="表页"),
        item(4),
        item(5),
    )
    result = pages.apply_page_navigation(docs, payloads, settings)
    assert "".join(root.itertext()) == before
    assert point_order(docs) == [1, 4, 2, 3, 5]
    assert [x[0] for x in page_links(docs)] == ["1", "2", "3", "4", "5"]
    assert [x[0] for x in result.targets] == [1, 2, 3, 4, 5]
    assert (
        len(list(root.iter(H + "figure"))) == 1
        and len(list(root.iter(H + "table"))) == 1
    )
    assert next(root.iter(H + "figcaption")).text == "甲图注"
    assert next(root.iter(H + "caption")).text == "乙表题"
    assert pages.validate_page_navigation(docs, payloads, settings) == result


def test_deferred_cross_file_move_keeps_legacy_id_tail_and_existing_links():
    body = (
        "<p>甲" + marker(1) + '<span id="old:2" class="source-page" data-page="2"/>'
        '乙 <a href="#old%3A2">旧链接</a></p>'
    )
    split = (
        "<p>续" + marker(3) + '完</p><figure id="fig"><img src="x" alt="x"/></figure>'
    )
    docs, payloads = package(body, split=split)
    before = {k: "".join(root.itertext()) for k, root in docs.items()}
    settings = config(
        item(1),
        item(2, position="before-target", target="fig", after=3, reason="正文跨页"),
        item(3),
    )
    result = pages.apply_page_navigation(docs, payloads, settings)
    assert {
        k: "".join(root.itertext())
        for k, root in docs.items()
        if k != "EPUB/navigation.xhtml"
    } == {k: v for k, v in before.items() if k != "EPUB/navigation.xhtml"}
    link = next(node for node in docs["EPUB/text/one.xhtml"].iter(H + "a"))
    assert link.get("href") == "two.xhtml#old%3A2"
    assert result.targets[1] == (2, "EPUB/text/two.xhtml", "old:2")
    assert pages.validate_page_navigation(docs, payloads, settings) == result


def test_undeclared_transposition_is_rejected():
    docs, payloads, settings = setup_single(deferred=False)
    with pytest.raises(pages.PageNavigationError, match="order"):
        pages.apply_page_navigation(docs, payloads, settings)


def test_wrong_but_later_anchor_cannot_certify_the_actual_placement():
    docs, payloads, _ = setup_single()
    settings = config(
        item(1),
        item(
            2,
            position="before-target",
            target="figure",
            after=4,
            reason="a later but wrong anchor",
        ),
        item(3),
        item(4),
    )
    with pytest.raises(pages.PageNavigationError, match="order"):
        pages.apply_page_navigation(docs, payloads, settings)


@pytest.mark.parametrize(
    "entry",
    [
        {"position": "marker", "after_page": 3, "reason": "x"},
        {
            "position": "before-target",
            "target": "figure",
            "after_page": 2,
            "reason": "x",
        },
        {
            "position": "before-target",
            "target": "figure",
            "after_page": 1,
            "reason": "x",
        },
        {
            "position": "before-target",
            "target": "figure",
            "after_page": 8,
            "reason": "x",
        },
        {
            "position": "before-target",
            "target": "figure",
            "after_page": True,
            "reason": "x",
        },
        {
            "position": "before-target",
            "target": "figure",
            "after_page": 3,
            "reason": "",
        },
        {"position": "before-target", "target": "figure", "after_page": 3},
        {"position": "before-target", "target": "figure", "reason": "x"},
    ],
)
def test_loader_rejects_invalid_anchor_scope_and_missing_reason(tmp_path, entry):
    (tmp_path / "page-map.tsv").write_text(
        "pdf_page\toriginal_printed_page\tdisposition\n1\t1\tretain\n2\t2\tretain\n3\t3\tretain\n4\t4\texclude\n",
        "utf-8",
    )
    loc = [
        {"pdf_page": 1, "position": "marker"},
        {"pdf_page": 2, **entry},
        {"pdf_page": 3, "position": "marker"},
    ]
    (tmp_path / "locations.json").write_text(
        json.dumps({"schema_version": 1, "pages": loc}), "utf-8"
    )
    manifest = {
        "source": {"relative_path": "source.pdf", "sha256": "a" * 64},
        "pdf": {"pages": 4},
        "epub": {
            "page_navigation": {
                "mode": "source-pages",
                "source_label": "source",
                "locations": {"zh-CN": "locations.json"},
            }
        },
    }
    with pytest.raises(pages.PageNavigationError):
        pages.load_page_navigation_config(tmp_path, manifest, "zh-CN")
    # Explicitly exercise the excluded anchor, not only a missing/invalid number.
    loc[1]["after_page"] = 4
    loc[1]["reason"] = "excluded anchor"
    (tmp_path / "locations.json").write_text(
        json.dumps({"schema_version": 1, "pages": loc}), "utf-8"
    )
    with pytest.raises(pages.PageNavigationError):
        pages.load_page_navigation_config(tmp_path, manifest, "zh-CN")


def test_loader_accepts_explicit_fields_and_preserves_folio_labels(tmp_path):
    (tmp_path / "page-map.tsv").write_text(
        "pdf_page\toriginal_printed_page\tdisposition\n1\t\tretain\n2\tiv\tretain\n3\t5\tretain\n",
        "utf-8",
    )
    loc = [
        {"pdf_page": 1, "position": "marker"},
        {
            "pdf_page": 2,
            "position": "before-target",
            "target": "figure",
            "after_page": 3,
            "reason": "整页插图",
        },
        {"pdf_page": 3, "position": "marker"},
    ]
    (tmp_path / "locations.json").write_text(
        json.dumps({"schema_version": 1, "pages": loc}), "utf-8"
    )
    manifest = {
        "source": {"relative_path": "source.pdf", "sha256": "a" * 64},
        "pdf": {"pages": 3},
        "epub": {
            "page_navigation": {
                "mode": "source-pages",
                "source_label": "source",
                "locations": {"zh-CN": "locations.json"},
            }
        },
    }
    result = pages.load_page_navigation_config(tmp_path, manifest, "zh-CN")
    assert [(x.pdf_page, x.label, x.after_page, x.reason) for x in result.pages] == [
        (1, "PDF 1", None, None),
        (2, "iv", 3, "整页插图"),
        (3, "5", None, None),
    ]


def test_direct_config_rejects_duplicate_pages_duplicate_targets_and_deferred_anchor():
    bad = [
        (item(1), item(2), item(2)),
        (
            item(1),
            item(2, position="before-target", target="fig", after=4, reason="x"),
            item(3, position="before-target", target="fig", after=4, reason="x"),
            item(4),
        ),
        (
            item(1),
            item(2, position="before-target", target="f2", after=3, reason="x"),
            item(3, position="before-target", target="f3", after=4, reason="x"),
            item(4),
        ),
    ]
    for loc in bad:
        docs, payloads = package(
            "<p>" + "".join(marker(n) for n in range(1, 5)) + "</p>"
        )
        before = ET.tostring(docs["EPUB/text/one.xhtml"])
        with pytest.raises(pages.PageNavigationError):
            pages.apply_page_navigation(docs, payloads, config(*loc))
        assert ET.tostring(docs["EPUB/text/one.xhtml"]) == before


@pytest.mark.parametrize(
    "target",
    [
        '<p id="object">text</p>',
        '<div id="object">text</div>',
        ' <img id="object" src="x" alt="x"/>',
        '<pre id="object"><code>x</code></pre>',
    ],
)
def test_deferred_target_must_be_actual_figure_or_table(target):
    docs, payloads = package(
        "<p>" + marker(1) + marker(2) + marker(3) + "</p>" + target
    )
    before = ET.tostring(docs["EPUB/text/one.xhtml"])
    settings = config(
        item(1),
        item(2, position="before-target", target="object", after=3, reason="x"),
        item(3),
    )
    with pytest.raises(pages.PageNavigationError):
        pages.apply_page_navigation(docs, payloads, settings)
    assert ET.tostring(docs["EPUB/text/one.xhtml"]) == before


def test_anchor_and_deferred_target_must_be_same_xhtml():
    docs, payloads = package(
        "<p>" + marker(1) + marker(2) + marker(3) + "</p>",
        split='<figure id="fig"><img src="x" alt="x"/></figure>',
    )
    settings = config(
        item(1),
        item(2, position="before-target", target="fig", after=3, reason="x"),
        item(3),
    )
    with pytest.raises(pages.PageNavigationError, match="same content XHTML"):
        pages.apply_page_navigation(docs, payloads, settings)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-order",
        "adjacent",
        "tail",
        "target-type",
        "target-duplicate",
        "anchor-member",
        "pagelist",
        "label",
    ],
)
def test_final_qa_rejects_drift_without_loosening_declared_order(mutation):
    docs, payloads, settings = setup_single()
    pages.apply_page_navigation(docs, payloads, settings)
    root = docs["EPUB/text/one.xhtml"]
    body = root.find(H + "body")
    point = next(n for n in root.iter() if n.get("data-page") == "2")
    next(n for n in root.iter() if n.get("data-page") == "3")
    figure = next(n for n in root.iter() if n.get("id") == "figure")
    if mutation == "extra-order":
        last = next(n for n in root.iter() if n.get("data-page") == "4")
        parent = next(n for n in root.iter() if last in list(n))
        parent.remove(last)
        body.insert(1, last)
    elif mutation == "adjacent":
        index = list(body).index(point)
        body.insert(index + 1, ET.Element(H + "p"))
    elif mutation == "tail":
        point.tail = "unexpected intervening text"
    elif mutation == "target-type":
        figure.tag = H + "div"
    elif mutation == "target-duplicate":
        ET.SubElement(body, H + "figure", {"id": "figure"})
    elif mutation == "anchor-member":
        # Keep the certified DOM sequence [1,3,2,4] and all page-list links:
        # move the object and its pagebreak, not the anchor, into a new spine member.
        docs["EPUB/text/two.xhtml"] = ET.fromstring(
            f'<html xmlns="{pages.XHTML}"><body/></html>'
        )
        other_body = docs["EPUB/text/two.xhtml"].find(H + "body")
        last = next(n for n in root.iter() if n.get("data-page") == "4")
        last_parent = next(n for n in root.iter() if last in list(n))
        body.remove(point)
        body.remove(figure)
        body.remove(last_parent)
        other_body.extend([point, figure, last_parent])
        xml = ET.fromstring(payloads["EPUB/package.opf"])
        manifest = xml.find("{" + pages.OPF + "}manifest")
        spine = xml.find("{" + pages.OPF + "}spine")
        ET.SubElement(
            manifest,
            "{" + pages.OPF + "}item",
            {
                "id": "two",
                "href": "text/two.xhtml",
                "media-type": "application/xhtml+xml",
            },
        )
        ET.SubElement(spine, "{" + pages.OPF + "}itemref", {"idref": "two"})
        payloads["EPUB/package.opf"] = ET.tostring(xml)
        nav = next(
            n
            for n in docs["EPUB/navigation.xhtml"].iter()
            if n.get(TYPE) == "page-list"
        )
        anchors = list(nav.iter(H + "a"))
        for node, index in [(point, 1), (last, 3)]:
            anchors[index].set("href", "text/two.xhtml#" + node.get("id"))
        assert point_order(docs) == [1, 3, 2, 4]
    elif mutation == "pagelist":
        nav = next(
            n
            for n in docs["EPUB/navigation.xhtml"].iter()
            if n.get(TYPE) == "page-list"
        )
        anchors = list(nav.iter(H + "a"))
        anchors[1].set("href", anchors[2].get("href"))
    elif mutation == "label":
        point.set("title", "fake")
    with pytest.raises(
        pages.PageNavigationError,
        match="same content XHTML" if mutation == "anchor-member" else None,
    ):
        pages.validate_page_navigation(docs, payloads, settings)


@pytest.mark.skipif(not shutil.which("pandoc"), reason="Pandoc required")
def test_real_markdown_epub_writer_with_figure_table_and_qa(tmp_path):
    # Production reader/writer/normalizer/EPUBCheck seam; source text remains one paragraph.
    entry = tmp_path / "main.md"
    cover = tmp_path / "cover.png"
    out = tmp_path / "book.epub"
    cover.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZPmAAAAAASUVORK5CYII="
        )
    )
    entry.write_text(
        '---\nlang: zh-CN\n---\n# 正文 {#body}\n\n开始[]{.source-page data-page="1"}前[]{#old-two .source-page data-page="2"}续[]{.source-page data-page="3"}字[]{.source-page data-page="4"}结束。\n\n![示例图](cover.png){#figure}\n\n| 甲 | 乙 |\n| --- | --- |\n| 一 | 二 |\n\n: 表题 {#table}\n\n[]{.source-page data-page="5"}后文。 [旧链接](#old-two)\n',
        "utf-8",
    )
    (tmp_path / "page-map.tsv").write_text(
        "pdf_page\toriginal_printed_page\tdisposition\n"
        + "".join(f"{x}\t{x}\tretain\n" for x in range(1, 6)),
        "utf-8",
    )
    locations = [
        {"pdf_page": 1, "position": "marker"},
        {
            "pdf_page": 2,
            "position": "before-target",
            "target": "figure",
            "after_page": 4,
            "reason": "图页插入跨页段落",
        },
        {
            "pdf_page": 3,
            "position": "before-target",
            "target": "table",
            "after_page": 4,
            "reason": "表页插入跨页段落",
        },
        {"pdf_page": 4, "position": "marker"},
        {"pdf_page": 5, "position": "marker"},
    ]
    (tmp_path / "locations.json").write_text(
        json.dumps({"schema_version": 1, "pages": locations}, ensure_ascii=False),
        "utf-8",
    )
    manifest = {
        "source": {"relative_path": "source.pdf", "sha256": "a" * 64},
        "pdf": {"pages": 5},
        "epub": {
            "cover": "cover.png",
            "title_page": False,
            "toc_depth": 1,
            "split_level": 1,
            "page_navigation": {
                "mode": "source-pages",
                "source_label": "Book PDF",
                "locations": {"zh-CN": "locations.json"},
            },
        },
    }
    lang = translator.Language(
        "zh-CN",
        "source",
        entry,
        None,
        {"epub": out},
        "测试",
        "作者",
        "urn:test:deferred",
    )
    work = translator.Work(
        Path.cwd(),
        tmp_path,
        manifest,
        "test-deferred",
        "测试",
        "markdown",
        None,
        tmp_path / "source.pdf",
        5,
        (lang,),
    )
    translator.build_markdown(work, lang, "epub", out)
    ev = tmp_path / "qa"
    ev.mkdir()
    translator.qa_epub(work, lang, out, ev)
    with zipfile.ZipFile(out) as z:
        payloads = {name: z.read(name) for name in z.namelist()}
    docs = {
        name: ET.fromstring(value)
        for name, value in payloads.items()
        if name.endswith(".xhtml")
    }
    cfg = pages.load_page_navigation_config(tmp_path, manifest, "zh-CN")
    result = pages.validate_page_navigation(docs, payloads, cfg)
    assert [x[0] for x in result.targets] == [1, 2, 3, 4, 5]
    content = [
        n
        for name, root in docs.items()
        if "/text/" in name
        for n in root.iter()
        if n.get(TYPE) == "pagebreak"
    ]
    assert [int(x.get("data-page")) for x in content] == [1, 4, 2, 3, 5]
    paragraph = next(
        node
        for name, root in docs.items()
        if "/text/" in name
        for node in root.iter(H + "p")
        if "开始" in "".join(node.itertext())
    )
    assert "".join(paragraph.itertext()) == "开始前续字结束。"
    assert any(
        n.tag == H + "figure" and n.get("id") == "figure"
        for root in docs.values()
        for n in root.iter()
    )
    assert any(
        n.tag == H + "table" and n.get("id") == "table"
        for root in docs.values()
        for n in root.iter()
    )
