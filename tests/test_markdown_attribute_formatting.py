from __future__ import annotations

import json
import shutil
import subprocess
from unittest import mock

import pytest

from tools import translator


def native_attributes(text):
    ast = json.loads(translator.run([
        "pandoc", "--from=markdown+fenced_divs+bracketed_spans+footnotes-raw_html",
        "--to=json",
    ], input_text=text).stdout)
    attributes = []

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("t")
            if kind in {"Header", "Span", "Div", "Image", "Figure", "Link", "Code", "CodeBlock"}:
                attrs = value["c"][1 if kind == "Header" else 0]
                if attrs[0] or attrs[1] or attrs[2]:
                    attributes.append((kind, attrs))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(ast)
    return attributes


@pytest.mark.skipif(
    not shutil.which("pandoc") or not shutil.which("autocorrect"), reason="format tools required"
)
def test_real_formatter_preserves_chinese_native_ids_classes_values_and_links(tmp_path):
    entry = tmp_path / "main.md"
    fence = chr(96) * 3
    source = (
        '# 第 1 章 导论 {#第-1-章-导论 .unnumbered data-original="原始English"}\n\n'
        '中文English测试。[原章](#第-1-章-导论)\n\n'
        '[表格旧锚]{#表-3-1 .retained data-original="A {B}，C"}\n\n'
        '::: {.table-group #表-7-1 data-source="原始English"}\n'
        '中文English表格。\n'
        ':::\n\n'
        '![中文English图](image.png){#图-13-5 .figure}\n\n'
        + fence + '{#代码-1 .python}\nprint("English")\n' + fence
        + '\n\n$x_{i+1}=y$\n'
    )
    entry.write_text(source, "utf-8")
    before = native_attributes(source)
    translator.autocorrect_markdown(entry, fix=True)
    formatted = entry.read_text("utf-8")
    assert "中文 English 测试" in formatted
    assert "中文 English 表格" in formatted
    assert native_attributes(formatted) == before
    ids = {attrs[0] for _, attrs in before}
    assert {"第-1-章-导论", "表-3-1", "表-7-1", "图-13-5", "代码-1"} <= ids
    assert '(#第-1-章-导论)' in formatted
    assert 'data-original="A {B}，C"' in formatted
    assert "translatorAttributePlaceholder" not in formatted
    assert translator.markdown_protected_literals(source) == translator.markdown_protected_literals(formatted)
    translator.autocorrect_markdown(entry, fix=False)


def test_attribute_mask_handles_quoted_braces_multiline_and_colliding_token_prefix():
    source = (
        'translatorAttributePlaceholder {#中文-1 .class data-x="A {B}"\n'
        "data-y='C } D'} and ordinary {a+b}.\n"
    )
    masked, attributes = translator.protect_markdown_attributes(source)
    assert len(attributes) == 1
    token, original = attributes[0]
    assert token.startswith("{#translatorAttributePlaceholderX")
    assert "{a+b}" in masked
    assert masked.replace(token, original) == source


@pytest.mark.skipif(
    not shutil.which("pandoc") or not shutil.which("autocorrect"), reason="format tools required"
)
@pytest.mark.parametrize("source", [
    "# 标题 {- #中文English}\n\n中文English测试。\n",
    '[可见正文]{说明="原始English" #中文English}\n\n中文English测试。\n',
])
def test_real_formatter_preserves_mixed_unnumbered_and_unicode_key_attributes(tmp_path, source):
    entry = tmp_path / "main.md"
    entry.write_text(source, "utf-8")
    before = native_attributes(source)
    assert any(attrs[0] == "中文English" for _, attrs in before)
    translator.autocorrect_markdown(entry, fix=True)
    formatted = entry.read_text("utf-8")
    assert native_attributes(formatted) == before
    assert "中文 English 测试" in formatted
    translator.autocorrect_markdown(entry, fix=False)


def test_formatter_rejects_a_lost_attribute_token_without_writing(tmp_path):
    entry = tmp_path / "main.md"
    original = "# 中文 {#中文-1}\n"
    entry.write_text(original, "utf-8")
    with (
        mock.patch.object(
            translator, "run", return_value=subprocess.CompletedProcess([], 0, "# 中文\n", "")
        ),
        pytest.raises(translator.CliError, match="属性占位符.*未写入"),
    ):
        translator.autocorrect_markdown(entry, fix=True)
    assert entry.read_text("utf-8") == original
