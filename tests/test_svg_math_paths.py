"""Reader-safe generated glyphs keep the original outlines and geometry."""

import base64
import copy
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tools import translator
from tools.svg_math_paths import FORMULA_THEME_STYLE, SVG, expand_local_glyphs

ROOT = Path(__file__).resolve().parents[1]
XHTML = "{http://www.w3.org/1999/xhtml}"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
)


def parse(body):
    return ET.fromstring(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="12pt" height="5pt" viewBox="-1 -2 12 5">' + body + '</svg>'
    )


def drawing_records(root):
    """Independent traversal of actual drawn shapes, resolving old references."""
    ids = {n.get('id'): n for n in root.iter() if n.get('id')}
    records = []

    def walk(node, transforms=(), paint=()):
        tag = node.tag.removeprefix(SVG)
        if tag in {'defs', 'style', 'metadata'}:
            return
        transform = node.get('transform', '')
        if tag == 'use' and ('x' in node.attrib or 'y' in node.attrib):
            transform = ' '.join(x for x in (
                transform, f"translate({node.get('x', '0')} {node.get('y', '0')})"
            ) if x)
        transforms += (transform,) if transform else ()
        if tag == 'use':
            walk(ids[(node.get('href') or node.get('{http://www.w3.org/1999/xlink}href'))[1:]], transforms, paint)
        elif tag in {'path', 'rect'}:
            records.append((tag, tuple(sorted((k, v) for k, v in node.attrib.items()
                                             if k not in {'id', 'transform'})), transforms, paint))
        else:
            own = tuple(sorted((k, v) for k, v in node.attrib.items()
                               if k not in {'id', 'transform', 'width', 'height', 'viewBox'}))
            for child in node:
                walk(child, transforms, paint + ((own,) if own else ()))
    walk(root)
    return records


def test_aliases_preserve_complete_drawing_order_transforms_and_paint():
    root = parse('''<defs><path id="p" d="M0 0L2 1Z"/>
      <use id="a" xlink:href="#p" transform="scale(1.2)"/>
      <use id="b" href="#a" transform="matrix(1 0 0 1 3 4)"/></defs>
      <g fill="red" stroke="none" transform="scale(2)">
      <use href="#b" transform="rotate(2)" x="-1.5" y="2e1"/>tail
      <rect x="0" y="1" width="2" height=".1"/>
      <use href="#p" y="3"/><use href="#p" x="4"/></g>''')
    before, attributes = drawing_records(root), dict(root.attrib)
    assert expand_local_glyphs(root) == 5
    assert drawing_records(root) == before
    assert root.attrib == attributes
    assert not list(root.iter(SVG + 'use')) and not list(root.iter(SVG + 'defs'))
    assert [p.get('d') for p in root.iter(SVG + 'path')] == ['M0 0L2 1Z'] * 3
    assert list(root)[0][0].tail == 'tail\n      '
    once = ET.tostring(root)
    assert expand_local_glyphs(root) == 0 and ET.tostring(root) == once


def test_existing_themed_cache_is_migrated_before_early_return(tmp_path):
    path = tmp_path / 'cached.svg'
    root = parse('''<style>''' + FORMULA_THEME_STYLE + '''</style>
      <metadata>row-tag-render-v2</metadata><defs><path id="p" d="M1 2L3 4"/></defs>
      <rect class="math-background" width="12" height="5"/>
      <g class="math-foreground"><use xlink:href="#p" x="1" y="-2"/></g>''')
    before, attributes = drawing_records(root), dict(root.attrib)
    path.write_bytes(ET.tostring(root))
    translator.normalize_formula_svg(path)
    actual = ET.parse(path).getroot()
    assert drawing_records(actual) == before and actual.attrib == attributes
    assert len(actual.findall(SVG + 'style')) == 1
    assert actual.find(SVG + 'metadata').text == 'row-tag-render-v2'
    assert not list(actual.iter(SVG + 'use'))
    once = path.read_bytes()
    translator.normalize_formula_svg(path)
    assert path.read_bytes() == once


@pytest.mark.parametrize('body', [
    '<defs><use id="a" href="#b"/><use id="b" href="#a"/></defs><use href="#a"/>',
    '<use href="#missing"/>',
    '<use href="other.svg#p"/>',
    '<defs><path id="p" d="M0 0" stroke="red"/></defs><use href="#p"/>',
    '<defs><g id="p"><path d="M0 0"/></g></defs><use href="#p"/>',
    '<defs><path id="p" d="M0 0"/><path id="p" d="M1 1"/></defs><use href="#p"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p" x="NaN"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p" x="1e999"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p" x="1px"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p" fill="red"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p" xlink:href="#other"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="" xlink:href="#p"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p"/><text><textPath href="#p">label</textPath></text>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p"/><g href="#p"/>',
    '<defs fill="red"><path id="p" d="M0 0"/></defs><use href="#p"/>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p"/><style>#p{opacity:.2}</style>',
    '<defs><path id="p" d="M0 0"/></defs><use id="paint" href="#p"/><style>#paint{opacity:.2}</style>',
    '<defs><path id="p" d="M0 0"/></defs><use href="#p"/><style>use{opacity:.2}</style>',
    '<defs><path id="p" d="M0 0"/></defs><g><use href="#p"/></g><style>g{opacity:.5}</style>',
    '<defs><path id="p" d="M0 0"/></defs><g fill="url(#p)"><use href="#p"/></g>',
    '<g fill="red"><defs><path id="p" d="M0 0"/></defs></g><use href="#p"/>',
    '<defs><path id="p" d="M0 0"/></defs><g fill="URL(other.svg#p)"><use href="#p"/></g>',
    '<defs><path id="p" d="M0 0"/></defs><g fill="u\\72l(other.svg#p)"><use href="#p"/></g>',
    '<defs><path id="p" d="M0 0"/></defs><g style="fill:red"><use href="#p"/></g>',
    '<defs><path id="p" d="M0 0"/></defs><g xml:base="other.svg"><use href="#p"/></g>',
])
def test_unrepresentable_generated_glyph_does_not_publish(tmp_path, body):
    path = tmp_path / 'bad.svg'
    original = ET.tostring(parse(body))
    path.write_bytes(original)
    with pytest.raises(translator.CliError, match='无法保真展开'):
        translator.normalize_formula_svg(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('body', [
    '<rect x="0" y="0" width="2" height=".1"/>',  # Fraction/rule only.
    '<g/>',  # Intentional TeX phantom, not a missing glyph.
])
def test_valid_generated_formula_without_glyphs_is_preserved(tmp_path, body):
    path = tmp_path / 'rule.svg'
    path.write_bytes(ET.tostring(parse(body)))
    before = drawing_records(ET.parse(path).getroot())
    translator.normalize_formula_svg(path)
    foreground = ET.parse(path).getroot().find(SVG + "g[@class='math-foreground']")
    assert foreground is not None
    assert [record[:3] for record in drawing_records(foreground)] == [record[:3] for record in before]


def test_definition_removal_does_not_reorder_root_siblings():
    root = parse('<defs><path id="p" d="M0 0"/></defs><use href="#p"/>'
                 '<rect width="2" height="1"/><use href="#p"/>')
    before = drawing_records(root)
    expand_local_glyphs(root)
    assert drawing_records(root) == before


def test_painted_use_identity_and_links_to_it_remain_available():
    root = parse('<defs><path id="p" d="M0 0"/></defs><use id="paint" href="#p"/>')
    expand_local_glyphs(root)
    assert root.find(SVG + 'g').get('id') == 'paint'


@pytest.mark.parametrize('before,after', [
    ('<text>first <tspan>second</tspan></text>', '<text>first<tspan>second</tspan></text>'),
    ('<desc>first\n\nsecond</desc>', '<desc>first\nsecond</desc>'),
])
def test_source_identity_does_not_discard_meaningful_text(before, after):
    assert translator.svg_source_identity(ET.tostring(parse(before))) != translator.svg_source_identity(ET.tostring(parse(after)))


@pytest.mark.parametrize("mixed_math", [False, True])
@pytest.mark.parametrize("same_theme", [False, True])
@pytest.mark.parametrize("file_uri", [False, True])
@pytest.mark.skipif(not all(shutil.which(t) for t in ("pandoc", "uv", "latex", "dvisvgm")),
                    reason="EPUB math toolchain required")
def test_legacy_source_svg_keeps_its_original_contract(tmp_path, monkeypatch, mixed_math, same_theme, file_uri):
    monkeypatch.setattr(translator, "temp_root", lambda *args, **kwargs: tmp_path)
    entry, output = tmp_path / "main.md", tmp_path / "book.epub"
    theme = translator.FORMULA_THEME_STYLE + ("" if same_theme else ".native-source{fill:currentColor}")
    original = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 12 5"><style>' + theme + '</style>'
        '<defs><path id="old" d="M0 0L12 0L0 5Z"/></defs>'
        '<rect class="math-background" width="12" height="5"/>'
        '<g class="math-foreground"><use xlink:href="#old" x="0" y="0"/></g></svg>'
    ).encode()
    asset = tmp_path / "legacy.svg"
    asset.write_bytes(original)
    (tmp_path / "cover.png").write_bytes(PNG)
    reference = asset.as_uri() if file_uri else 'legacy.svg'
    entry.write_text('# Source SVG {#body}\n\n'
                     f'![Legacy formula]({reference})' + '{.math-inline width=12 height=5}\n\n'
                     + ('$x$\n' if mixed_math else ''), encoding="utf8")
    language = translator.Language("en", "source", entry, None, {"epub": output})
    work = translator.Work(ROOT, tmp_path, {"epub": {
        "title": "Source fixture", "cover": "cover.png", "title_page": False,
        "toc_depth": 1, "split_level": 1, "bodymatter": "body"}},
        "source-svg-fixture", "Source fixture", "markdown", None,
        tmp_path / "source.pdf", 1, (language,))
    translator.build_markdown(work, language, "epub", output)
    evidence = tmp_path / "qa"
    evidence.mkdir()
    translator.qa_epub(work, language, output, evidence)
    assert asset.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        images = [(name, image) for name in archive.namelist() if name.endswith('.xhtml')
                  for image in ET.fromstring(archive.read(name)).iter(XHTML + 'img')
                  if image.get('alt') == 'Legacy formula']
        assert len(images) == 1
        name, image = images[0]
        member = posixpath.normpath(posixpath.join(posixpath.dirname(name), image.get('src')))
        data = archive.read(member)
        assert translator.svg_source_identity(data) == translator.svg_source_identity(original)
        assert len(list(ET.fromstring(data).iter(SVG + 'use'))) == 1
        if mixed_math:
            # A generated formula cannot become a source image merely by
            # changing its theme or adding semantic classes.
            bad = tmp_path / 'bad-generated.epub'
            changed = False
            with zipfile.ZipFile(bad, 'w') as candidate:
                for info in archive.infolist():
                    content = archive.read(info)
                    if info.filename.endswith('.svg') and info.filename != member:
                        svg = ET.fromstring(content)
                        foreground = svg.find(SVG + "g[@class='math-foreground']")
                        if foreground is not None:
                            foreground.set('class', 'math-foreground highlight')
                            next(foreground.iter(SVG + 'path')).set('id', 'extra-reference')
                            ET.SubElement(foreground, SVG + 'use',
                                          {'{http://www.w3.org/1999/xlink}href': '#extra-reference'})
                            svg.find(SVG + 'style').text += '.not-an-origin-marker{fill:currentColor}'
                            content = ET.tostring(svg)
                            changed = True
                    candidate.writestr(copy.copy(info), content)
            assert changed
            with pytest.raises(translator.CliError, match='残留 use'):
                translator.qa_epub(work, language, bad, evidence)
        damaged = tmp_path / 'damaged-source.epub'
        with zipfile.ZipFile(damaged, 'w') as candidate:
            for info in archive.infolist():
                content = archive.read(info)
                if info.filename == member:
                    content = content.replace(b'M0 0L12 0L0 5Z', b'M0 0L99 0L0 5Z')
                candidate.writestr(copy.copy(info), content)
    with pytest.raises(translator.CliError, match='残留 use'):
        translator.qa_epub(work, language, damaged, evidence)
