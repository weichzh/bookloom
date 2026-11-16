#!/usr/bin/env python3
"""Run isolated agent-browser QA against selected or all XHTML in an EPUB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

try:
    from tools import browser_runtime
    from tools.epub_semantics import (
        CODE_DOM_EXTRACTOR_JS,
        CodeRecord,
        EpubSemanticError,
        code_from_xhtml,
        validate_code_records,
    )
except ModuleNotFoundError:  # Direct execution: python tools/epub_browser_qa.py
    import browser_runtime
    from epub_semantics import (
        CODE_DOM_EXTRACTOR_JS,
        CodeRecord,
        EpubSemanticError,
        code_from_xhtml,
        validate_code_records,
    )

ROOT = Path(__file__).resolve().parents[1]
KNOWN_VIEWPORT_EOF = "EOF while parsing a value"
PUBLISHER_MODE = "publisher-default"
READER_DARK_MODE = "reader-dark"
READER_LARGE_PRINT_MODE = "reader-large-print"
ALL_MODES = "all"
DEFAULT_VIEWPORT = (390, 844)
DEFAULT_MODE_NAMES = (
    PUBLISHER_MODE,
    READER_DARK_MODE,
    READER_LARGE_PRINT_MODE,
)


def mode_definition(
    name: str,
    *,
    width: int,
    height: int,
    color_scheme: str | None,
    disable_publisher_css: bool,
    user_font_family: str | None,
    user_font_size_px: int | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "viewport": [width, height],
        "color_scheme": color_scheme,
        "disable_publisher_css": disable_publisher_css,
        "user_font_family": user_font_family,
        "user_font_size_px": user_font_size_px,
    }


def default_modes(
    width: int = DEFAULT_VIEWPORT[0], height: int = DEFAULT_VIEWPORT[1]
) -> list[dict[str, Any]]:
    """Return the production reader matrix in deterministic order.

    The first viewport remains configurable for the existing diagnostic CLI,
    while the two reader modes retain their documented dimensions unless a
    caller explicitly selects one of them.
    """

    return [
        mode_definition(
            PUBLISHER_MODE,
            width=width,
            height=height,
            color_scheme="light",
            disable_publisher_css=False,
            user_font_family=None,
            user_font_size_px=None,
        ),
        mode_definition(
            READER_DARK_MODE,
            width=1280,
            height=900,
            color_scheme="dark",
            disable_publisher_css=False,
            user_font_family=None,
            user_font_size_px=None,
        ),
        mode_definition(
            READER_LARGE_PRINT_MODE,
            width=390,
            height=844,
            color_scheme="light",
            disable_publisher_css=True,
            user_font_family="serif",
            user_font_size_px=32,
        ),
    ]


def selected_modes(
    mode: str,
    *,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Resolve a matrix or a deliberately selected diagnostic mode."""

    modes = default_modes(width, height)
    if mode == ALL_MODES:
        return modes
    if mode not in DEFAULT_MODE_NAMES:
        raise BrowserQAError(
            f"未知浏览器模式：{mode}；可用值为 all、{', '.join(DEFAULT_MODE_NAMES)}"
        )
    if mode == PUBLISHER_MODE:
        return [modes[0]]
    return [item for item in modes if item["name"] == mode]


HARNESS = r"""<!doctype html>
<meta charset="utf-8">
<title>EPUB browser QA</title>
<style>
  html, body { margin: 0; }
  iframe { display: block; width: 100vw; height: __HEIGHT__px; border: 0; }
</style>
<iframe id="reader" title="EPUB QA"></iframe>
<script>
  const pages = __PAGES__;
  const allPages = __ALL_PAGES__;
  const pageTimeout = __PAGE_TIMEOUT__;
  const expectedViewport = [__WIDTH__, __HEIGHT__];
  const modeSettings = __MODE_SETTINGS__;
  const extractCode = __CODE_DOM_EXTRACTOR__;
  const state = (window.qaState = { done: false, results: [], errors: [] });
  const frame = document.querySelector("#reader");
  let pageReaderSettings = null;
  const formulaMetrics = new WeakMap();

  function applyReaderMode(document) {
    const publisherNodes = [
      ...document.querySelectorAll("link[rel~='stylesheet'], style"),
    ];
    const inlineStyleNodes = [...document.querySelectorAll("[style]")];
    const formulaRules = [];
    if (modeSettings.disable_publisher_css) {
      const emNumber = (value, positive) => {
        if (!/^[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)em$/.test(value)) return null;
        const number = Number(value.slice(0, -2));
        return Number.isFinite(number) && (!positive || number > 0) ? number : null;
      };
      const images = [...document.querySelectorAll("img.math-inline, img.math-display")];
      images.forEach((image, index) => {
        if (!formulaMetrics.has(image)) {
          let width = emNumber(image.style.width, true);
          const height = emNumber(image.style.height, true);
          if (width === null && height !== null && image.naturalHeight > 0) {
            width = height * image.naturalWidth / image.naturalHeight;
          }
          formulaMetrics.set(image, width > 0 && Number.isFinite(width)
            ? { width, baseline: emNumber(image.style.verticalAlign, false) } : null);
        }
        const metrics = formulaMetrics.get(image);
        if (!metrics) return;
        image.setAttribute("data-qa-math-size", String(index));
        const baseline = metrics.baseline === null
          ? "" : `vertical-align: ${metrics.baseline}em !important;`;
        formulaRules.push(
          `img[data-qa-math-size="${index}"] { width: ${metrics.width}em !important; ${baseline} }`,
        );
      });
    }
    let removedPublisherStyleCount = 0;
    let removedInlineStyleCount = 0;
    if (modeSettings.disable_publisher_css) {
      for (const node of publisherNodes) {
        node.remove();
        removedPublisherStyleCount += 1;
      }
      for (const node of inlineStyleNodes) {
        node.removeAttribute("style");
        removedInlineStyleCount += 1;
      }
    }
    let override = document.querySelector("#qa-reader-overrides");
    if (override) override.remove();
    const rules = [];
    if (modeSettings.user_font_family) {
      rules.push(
        `html, body, body * { font-family: ${modeSettings.user_font_family} !important; }`,
      );
    }
    if (modeSettings.user_font_size_px) {
      rules.push(`html { font-size: ${modeSettings.user_font_size_px}px !important; }`);
    }
    if (modeSettings.disable_publisher_css) {
      rules.push(
        "html, body, body * { overflow-wrap: anywhere !important; word-break: break-word !important; text-spacing-trim: space-all !important; }",
        "img, svg { max-width: 100% !important; height: auto !important; }",
        ".display-formula { display: block !important; max-width: 100% !important; overflow-x: auto !important; text-indent: 0 !important; }",
        ".display-formula img.math-display { max-width: none !important; }",
        "pre { max-width: 100% !important; overflow-x: auto !important; }",
        "table { display: block !important; max-width: 100% !important; overflow-x: auto !important; }",
        "table, table * { overflow-wrap: normal !important; word-break: keep-all !important; }",
        ".table-scroll .table-token { white-space: nowrap !important; }",
        ".table-scroll table img.math-inline, .table-scroll table img.math-display { max-width: none !important; }",
        "table th, table td { padding-inline: 0.25em !important; }",
        ".code-table th:first-child, .code-table td:first-child { min-width: 4em !important; }",
        ".code-table th:first-child, .code-table td:first-child, .code-table th:first-child *, .code-table td:first-child * { overflow-wrap: normal !important; word-break: normal !important; }",
        "@counter-style translator-decimal-two-parens { system: extends decimal; prefix: '('; suffix: ') '; }",
        "div.translator-list-decimal-two-parens > ol > li { list-style-type: translator-decimal-two-parens; }",
        "pre, pre *, code, code *, .code-example .line-block, .code-example .line-block *, .binding-example .line-block, .binding-example .line-block * { font-family: monospace !important; }",
        ".code-example, .binding-example, .code-table { max-width: 100% !important; overflow-x: auto !important; }",
        ".code-example .line-block, .binding-example .line-block { white-space: nowrap !important; }",
        ".code-example code, .binding-example code, .code-table code { white-space: pre !important; }",
      );
    }
    rules.push(...formulaRules);
    if (rules.length) {
      override = document.createElement("style");
      override.id = "qa-reader-overrides";
      override.textContent = rules.join("\n");
      (document.head || document.documentElement).append(override);
    }
    const computedHtml = getComputedStyle(document.documentElement);
    const computedBody = getComputedStyle(document.body);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const light = matchMedia("(prefers-color-scheme: light)").matches;
    const actualColorScheme = dark ? "dark" : light ? "light" : "no-preference";
    const remainingPublisherNodes = [
      ...document.querySelectorAll("link[rel~='stylesheet'], style:not(#qa-reader-overrides)"),
    ].length;
    const remainingInlineStyleNodes = document.querySelectorAll("[style]").length;
    return {
      mode: modeSettings.name,
      requestedColorScheme: modeSettings.color_scheme || "default",
      actualColorScheme,
      disablePublisherCssRequested: Boolean(modeSettings.disable_publisher_css),
      publisherCssDisabled: modeSettings.disable_publisher_css
        ? remainingPublisherNodes === 0 && remainingInlineStyleNodes === 0
        : false,
      mathImagesWithRelativeSizing: formulaRules.length,
      publisherStyleNodesBefore: publisherNodes.length,
      publisherStyleNodesRemoved: removedPublisherStyleCount,
      publisherStyleNodesRemaining: remainingPublisherNodes,
      inlineStyleNodesBefore: inlineStyleNodes.length,
      inlineStyleNodesRemoved: removedInlineStyleCount,
      inlineStyleNodesRemaining: remainingInlineStyleNodes,
      requestedFontFamily: modeSettings.user_font_family,
      actualFontFamily: computedBody.fontFamily,
      userFontApplied: modeSettings.user_font_family
        ? computedBody.fontFamily.toLowerCase().includes(
            modeSettings.user_font_family.toLowerCase(),
          )
        : false,
      requestedFontSizePx: modeSettings.user_font_size_px,
      actualFontSizePx: parseFloat(computedHtml.fontSize),
      userFontSizeApplied: modeSettings.user_font_size_px
        ? Math.abs(
            parseFloat(computedHtml.fontSize) - modeSettings.user_font_size_px,
          ) < 0.1
        : false,
      reflowWrapApplied: modeSettings.disable_publisher_css
        ? ["anywhere", "break-word"].includes(computedBody.overflowWrap)
        : false,
      actualTextSpacingTrim: computedBody.textSpacingTrim,
      fullwidthPunctuationApplied: modeSettings.disable_publisher_css
        ? computedBody.textSpacingTrim === "space-all"
        : false,
      mediaFittingApplied: modeSettings.disable_publisher_css
        ? [...document.querySelectorAll("img, svg")].filter(
            (node) => !(node.matches("img.math-display") && node.closest(".display-formula"))
              && !node.matches(".table-scroll table img.math-inline, .table-scroll table img.math-display"),
          ).every(
            (node) => getComputedStyle(node).maxWidth === "100%",
          )
        : false,
      mathDisplayScrollingApplied: modeSettings.disable_publisher_css
        ? [...document.querySelectorAll(".display-formula img.math-display")].every(
            (node) => {
              const container = getComputedStyle(node.closest(".display-formula"));
              return getComputedStyle(node).maxWidth === "none"
                && container.maxWidth === "100%"
                && container.display === "block" && container.overflowX === "auto";
            },
          )
        : false,
      tableMathScrollingApplied: modeSettings.disable_publisher_css
        ? [...document.querySelectorAll(
            ".table-scroll table img.math-inline, .table-scroll table img.math-display",
          )].every((node) => {
            const table = getComputedStyle(node.closest("table"));
            return getComputedStyle(node).maxWidth === "none"
              && table.maxWidth === "100%" && table.overflowX === "auto";
          })
        : false,
      codeScrollingApplied: modeSettings.disable_publisher_css
        ? [...document.querySelectorAll("pre, .code-example, .binding-example, .code-table")].every(
            (node) => getComputedStyle(node).maxWidth === "100%"
              && getComputedStyle(node).overflowX === "auto",
          ) && [...document.querySelectorAll(
            ".code-example .line-block, .binding-example .line-block",
          )].every((node) => getComputedStyle(node).whiteSpace === "nowrap")
          && [...document.querySelectorAll(
            ".code-example code, .binding-example code, .code-table code",
          )].every((node) => getComputedStyle(node).whiteSpace === "pre")
          && [...document.querySelectorAll(
            "pre, code, .code-example .line-block, .binding-example .line-block",
          )].every((node) => getComputedStyle(node).fontFamily === "monospace")
        : false,
    };
  }

  const documentCache = new Map();

  function documentUrl(url) {
    const value = new URL(url, window.location.href);
    value.hash = "";
    value.pathname = decodeURIComponent(value.pathname).split("/")
      .map((part, index) => value.protocol === "file:" && index === 1 && /^[a-z]:$/i.test(part)
        ? part : encodeURIComponent(part)).join("/");
    return new URL(value.href).href;
  }

  const packageDocuments = new Set(allPages.map((path) => documentUrl(path)));

  function linkIdentity(url, base) {
    const value = new URL(url, base);
    const member = documentUrl(value.href);
    const fragment = decodedFragment(value.href);
    if (!packageDocuments.has(member) || value.search || !fragment) return null;
    return { member, fragment };
  }

  async function loadPage(path) {
    const requested = new URL(path, window.location.href);
    const fragment = decodedFragment(requested.href);
    if (fragment && frame.contentDocument?.readyState === "complete"
        && documentUrl(frame.contentWindow.location.href) === documentUrl(requested.href)) {
      frame.contentWindow.location.hash = requested.hash;
      pageReaderSettings = applyReaderMode(frame.contentDocument);
      if (!(await positionFragment(fragment))?.visible) {
        throw new Error(`fragment not visible after reader layout: ${fragment}`);
      }
      return;
    }
    return new Promise((resolve, reject) => {
      const loaded = async () => {
        clearTimeout(timeout);
        try {
          if (!frame.contentDocument) {
            throw new Error(`cannot access local XHTML ${path}; check file-origin launch arguments`);
          }
          pageReaderSettings = applyReaderMode(frame.contentDocument);
          const fragment = decodedFragment(frame.contentWindow.location.href);
          if (fragment && !(await positionFragment(fragment))?.visible) {
            throw new Error(`fragment not visible after reader layout: ${fragment}`);
          }
          documentCache.set(documentUrl(frame.contentWindow.location.href),
            frame.contentDocument.cloneNode(true));
          resolve();
        } catch (error) {
          reject(error);
        }
      };
      const timeout = setTimeout(() => {
        frame.removeEventListener("load", loaded);
        reject(new Error(`timeout ${path}`));
      }, pageTimeout);
      frame.addEventListener("load", loaded, { once: true });
      frame.src = path;
    });
  }

  async function targetDocument(member) {
    if (!documentCache.has(member)) await loadPage(member);
    return documentCache.get(member);
  }

  function fragmentPosition(element) {
    const rect = element.getBoundingClientRect();
    const viewportHeight = frame.contentDocument.documentElement.clientHeight;
    return { top: rect.top, bottom: rect.bottom, viewportHeight,
      visible: rect.top < viewportHeight && rect.bottom >= 0 };
  }

  async function positionFragment(fragment) {
    const document = frame.contentDocument;
    const target = document.getElementById(fragment);
    if (!target) return null;
    // The browser's initial hash scroll precedes reader font/style overrides.
    // Re-anchor the same verified target only after its final layout is ready.
    if (document.fonts) await document.fonts.ready;
    target.scrollIntoView({ block: "start", inline: "nearest", behavior: "instant" });
    const rect = target.getBoundingClientRect();
    if (rect.height === 0) {
      // Empty pagebreak anchors can round a fractional top just above the viewport.
      // Move to a real in-viewport coordinate; keep the visibility test exact.
      frame.contentWindow.scrollTo({
        top: Math.floor(frame.contentWindow.scrollY + rect.top),
        left: frame.contentWindow.scrollX, behavior: "instant",
      });
    }
    await new Promise((resolve) => frame.contentWindow.requestAnimationFrame(resolve));
    return fragmentPosition(target);
  }

  function clickNavigation(anchor, expected) {
    const previous = frame.contentDocument;
    const crossesDocument = documentUrl(frame.contentWindow.location.href) !== expected.member;
    return new Promise((resolve) => {
      let loaded = false;
      const onload = () => { loaded = true; };
      const started = Date.now();
      const finish = async (success) => {
        clearInterval(timer);
        frame.removeEventListener("load", onload);
        try {
          if (success) {
            pageReaderSettings = applyReaderMode(frame.contentDocument);
            success = Boolean((await positionFragment(expected.fragment))?.visible);
          }
          resolve(success);
        } catch (_) {
          resolve(false);
        }
      };
      frame.addEventListener("load", onload);
      const timer = setInterval(() => {
        const actual = linkIdentity(frame.contentWindow.location.href, window.location.href);
        const ready = frame.contentDocument && frame.contentDocument.readyState === "complete";
        if (actual && actual.member === expected.member && actual.fragment === expected.fragment
            && ready && (!crossesDocument || (loaded && frame.contentDocument !== previous))) {
          finish(Boolean(frame.contentDocument.getElementById(expected.fragment)));
        } else if (Date.now() - started >= pageTimeout) {
          finish(false);
        }
      }, 20);
      anchor.click();
    });
  }

  function hasEpubType(element, token) {
    const value = element.getAttributeNS("http://www.idpf.org/2007/ops", "type")
      || element.getAttribute("epub:type") || "";
    return value.split(/\s+/).includes(token);
  }

  function decodedFragment(url) {
    try {
      return decodeURIComponent(new URL(url).hash.slice(1));
    } catch (_) {
      return null;
    }
  }

  async function verifyFootnoteNavigation(document) {
    const sourceMember = documentUrl(frame.contentWindow.location.href);
    let noterefTargetChecks = 0;
    let backlinkTargetChecks = 0;
    let noterefTargetFailures = 0;
    let backlinkTargetFailures = 0;
    let noterefNavigationChecks = 0;
    let backlinkNavigationChecks = 0;
    let noterefNavigationFailures = 0;
    let backlinkNavigationFailures = 0;
    const validPairs = [];
    const footnoteNavigation = [];
    const references = [...document.querySelectorAll("a")]
      .filter((element) => hasEpubType(element, "noteref"));
    for (const reference of references) {
      noterefTargetChecks += 1;
      const targetLink = linkIdentity(reference.getAttribute("href"), sourceMember);
      const targetRoot = targetLink && await targetDocument(targetLink.member);
      const target = targetRoot && targetRoot.getElementById(targetLink.fragment);
      if (!reference.id || references.filter((item) => item.id === reference.id).length !== 1
          || !target || [...targetRoot.querySelectorAll("[id]")]
            .filter((item) => item.id === targetLink.fragment).length !== 1
          || target.localName !== "aside"
          || !hasEpubType(target, "footnote")) {
        noterefTargetFailures += 1;
        continue;
      }
      const backlink = [...target.querySelectorAll("a")].find((element) => {
        if (!hasEpubType(element, "backlink")) return false;
        const link = linkIdentity(element.getAttribute("href"), targetLink.member);
        return link && link.member === sourceMember && link.fragment === reference.id;
      });
      backlinkTargetChecks += 1;
      if (!backlink) {
        backlinkTargetFailures += 1;
        continue;
      }
      // Store identities, not DOM nodes: cross-file navigation replaces the document.
      validPairs.push({ referenceId: reference.id, targetLink });
    }
    if (documentUrl(frame.contentWindow.location.href) !== sourceMember) await loadPage(sourceMember);
    const samples = validPairs.length <= 3 ? validPairs : [
      validPairs[0],
      validPairs[Math.floor((validPairs.length - 1) / 2)],
      validPairs[validPairs.length - 1],
    ];
    for (const { referenceId, targetLink } of samples) {
      try {
        const reference = frame.contentDocument.getElementById(referenceId);
        noterefNavigationChecks += 1;
        if (!reference || !await clickNavigation(reference, targetLink)) {
          noterefNavigationFailures += 1;
          continue;
        }
        const target = frame.contentDocument.getElementById(targetLink.fragment);
        const targetPosition = fragmentPosition(target);
        const backlink = target && [...target.querySelectorAll("a")].find((element) => {
          const link = hasEpubType(element, "backlink")
            && linkIdentity(element.getAttribute("href"), targetLink.member);
          return link && link.member === sourceMember && link.fragment === referenceId;
        });
        backlinkNavigationChecks += 1;
        if (!backlink || !await clickNavigation(backlink, { member: sourceMember, fragment: referenceId })) {
          backlinkNavigationFailures += 1;
        } else {
          footnoteNavigation.push({ source: sourceMember, referenceId,
            target: targetLink.member, targetId: targetLink.fragment, targetPosition,
            returned: documentUrl(frame.contentWindow.location.href),
            returnPosition: fragmentPosition(frame.contentDocument.getElementById(referenceId)) });
        }
      } finally {
        if (documentUrl(frame.contentWindow.location.href) !== sourceMember) await loadPage(sourceMember);
      }
    }
    return {
      noterefTargetChecks,
      backlinkTargetChecks,
      noterefTargetFailures,
      backlinkTargetFailures,
      noterefNavigationChecks,
      backlinkNavigationChecks,
      noterefNavigationFailures,
      backlinkNavigationFailures,
      footnoteNavigation,
    };
  }

  async function inspect(path, verifyNavigation = true) {
    const navigation = verifyNavigation ? await verifyFootnoteNavigation(frame.contentDocument) : {};
    const document = frame.contentDocument;
    const viewport = document.documentElement.clientWidth;
    const images = [...document.images];
    const formulas = [...document.querySelectorAll("img.math-inline,img.math-display")];
    const documentScrollWidth = document.documentElement.scrollWidth;
    const bodyScrollWidth = document.body.scrollWidth;
    const identifiers = [...document.querySelectorAll("[id]")]
      .map((element) => element.id);
    const internalLinks = [...document.querySelectorAll("a[href]")]
      .map((element) => {
        const url = new URL(element.getAttribute("href"), document.baseURI);
        if (url.protocol !== "file:" || !/\.x?html$/i.test(url.pathname)) return null;
        return {
          href: element.getAttribute("href"),
          target: documentUrl(url.href),
          fragment: decodedFragment(url.href),
        };
      })
      .filter(Boolean);
    const overflowWidth = Math.max(documentScrollWidth, bodyScrollWidth) - viewport;
    const overflowElements = overflowWidth > 1
      ? [...document.querySelectorAll("body *")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.right > viewport + 1 || element.scrollWidth > element.clientWidth + 1;
        })
        .slice(0, 12)
        .map((element) => {
          const rect = element.getBoundingClientRect();
          const classes = [...element.classList];
          return {
            selector: element.id
              ? `${element.tagName.toLowerCase()}#${CSS.escape(element.id)}`
              : element.tagName.toLowerCase()
                + classes.map((value) => `.${CSS.escape(value)}`).join(""),
            rect: [rect.left, rect.right, rect.width],
            clientWidth: element.clientWidth,
            scrollWidth: element.scrollWidth,
            text: (element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 160),
          };
        })
      : [];
    return {
      path,
      url: documentUrl(frame.contentWindow.location.href),
      identifiers,
      internalLinks,
      viewport: [frame.contentWindow.innerWidth, frame.contentWindow.innerHeight],
      documentScrollWidth,
      bodyScrollWidth,
      textLength: (document.body.textContent || "").trim().length,
      codeRecords: extractCode(document),
      imageCount: images.length,
      brokenImages: images.filter((image) => !image.complete || image.naturalWidth === 0).length,
      formulaCount: formulas.length,
      formulasWithoutAlt: formulas.filter((image) => !image.alt.trim()).length,
      mathmlCount: document.querySelectorAll("math").length,
      texAnnotationCount: document.querySelectorAll('[type="application/x-tex"]').length,
      tableCount: document.querySelectorAll("table").length,
      tableMathImageCount: document.querySelectorAll(
        ".table-scroll table img.math-inline, .table-scroll table img.math-display",
      ).length,
      footnoteRefCount: [...document.querySelectorAll("a")]
        .filter((element) => hasEpubType(element, "noteref")).length,
      footnoteCount: [...document.querySelectorAll("aside")]
        .filter((element) => hasEpubType(element, "footnote")).length,
      overflowCount: overflowWidth > 1 ? 1 : 0,
      overflowWidth: Math.max(0, overflowWidth),
      overflowElements,
      readerSettings: pageReaderSettings,
      ...navigation,
    };
  }

  async function validateLinks() {
    const documents = new Map(
      state.results.map((item) => [item.url, new Set(item.identifiers)])
    );
    // An inspected XHTML can link to a non-inspected XHTML. Resolve that
    // target from the full extracted package without counting it as a rendered
    // inspection; the footnote verifier uses the same package-wide identity.
    for (const item of state.results) {
      for (const link of item.internalLinks) {
        if (documents.has(link.target) || !packageDocuments.has(link.target)) continue;
        try {
          const target = await targetDocument(link.target);
          documents.set(link.target, target
            ? new Set([...target.querySelectorAll("[id]")].map((node) => node.id))
            : null);
        } catch (_) {
          documents.set(link.target, null);
        }
      }
    }
    for (const item of state.results) {
      item.xhtmlLinkCount = item.internalLinks.length;
      item.fragmentLinkCount = item.internalLinks.filter((link) => link.fragment).length;
      item.missingXhtmlTargets = item.internalLinks
        .filter((link) => !documents.get(link.target)).length;
      item.missingFragmentTargets = item.internalLinks
        .filter((link) => {
          const identifiers = documents.get(link.target);
          return identifiers && link.fragment && !identifiers.has(link.fragment);
        }).length;
      delete item.identifiers;
      delete item.internalLinks;
    }
  }

  window.qaShowPage = async (path) => {
    await loadPage(path);
    const document = frame.contentDocument;
    const target = [...document.querySelectorAll("a")].find(
      (element) => hasEpubType(element, "noteref")
    ) || document.querySelector("table, img.math-inline, img.math-display, h1, img");
    if (target && !decodedFragment(new URL(path, window.location.href).href)) {
      target.scrollIntoView({ block: "center" });
    }
    return inspect(path, false);
  };

  async function runQA() {
    const started = Date.now();
    while ((innerWidth !== expectedViewport[0] || innerHeight !== expectedViewport[1])
           && Date.now() - started < 30000) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (innerWidth !== expectedViewport[0] || innerHeight !== expectedViewport[1]) {
      state.errors.push(`viewport ${innerWidth}x${innerHeight}`);
      state.done = true;
      return;
    }
    for (const page of pages) {
      try {
        await loadPage(page);
        state.results.push(await inspect(page));
      } catch (error) {
        state.errors.push(`${page}: ${String(error)}`);
      }
    }
    await validateLinks();
    state.done = true;
  }

  window.startQA = () => {
    runQA();
    return true;
  };
</script>
"""


class BrowserQAError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def safe_extract(epub: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True)
    with zipfile.ZipFile(epub) as archive:
        names = archive.namelist()
        for name in names:
            normalized = posixpath.normpath(name)
            if normalized.startswith(("../", "/")):
                raise BrowserQAError(f"EPUB 包含越界路径：{name}")
            target = (destination / normalized).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as error:
                raise BrowserQAError(f"EPUB 包含越界路径：{name}") from error
        archive.extractall(destination)
    return sorted(name for name in names if name.lower().endswith(".xhtml"))


def select_members(available: list[str], selected: list[str] | None) -> list[str]:
    if selected is None:
        return list(available)
    if not isinstance(selected, list) or not selected:
        raise BrowserQAError("--member 必须包含至少一个包内 XHTML 路径")
    if any(
        not isinstance(member, str)
        or not member.strip()
        or "\\" in member
        or posixpath.isabs(member)
        or posixpath.normpath(member) != member
        for member in selected
    ):
        raise BrowserQAError("--member 必须是规范的包内 XHTML 路径")
    if len(set(selected)) != len(selected):
        raise BrowserQAError("--member 不可重复")
    unknown = [member for member in selected if member not in available]
    if unknown:
        raise BrowserQAError("未知 XHTML --member：" + ", ".join(unknown))
    chosen = set(selected)
    return [member for member in available if member in chosen]


def write_harness(
    path: Path,
    members: list[str],
    width: int,
    height: int,
    page_timeout: int,
    mode_settings: dict[str, Any] | None = None,
    *,
    base_prefix: str = "../",
    available_members: list[str] | None = None,
) -> list[str]:
    paths = [base_prefix + member for member in members]
    all_members = available_members if available_members is not None else members
    all_paths = [base_prefix + member for member in all_members]
    settings = mode_settings or mode_definition(
        PUBLISHER_MODE,
        width=width,
        height=height,
        color_scheme=None,
        disable_publisher_css=False,
        user_font_family=None,
        user_font_size_px=None,
    )
    text = (
        HARNESS.replace("__PAGES__", json.dumps(paths, ensure_ascii=False))
        .replace("__ALL_PAGES__", json.dumps(all_paths, ensure_ascii=False))
        .replace("__WIDTH__", str(width))
        .replace("__HEIGHT__", str(height))
        .replace("__PAGE_TIMEOUT__", str(page_timeout * 1000))
        .replace("__MODE_SETTINGS__", json.dumps(settings, ensure_ascii=False))
        .replace("__CODE_DOM_EXTRACTOR__", CODE_DOM_EXTRACTOR_JS)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return paths


def parse_json_output(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        for index, character in enumerate(stripped):
            if character not in "[{\"-0123456789tfn":
                continue
            try:
                return json.loads(stripped[index:])
            except json.JSONDecodeError:
                continue
    raise BrowserQAError(f"agent-browser 未返回 JSON：{stripped[-500:]}")


def run_agent(
    namespace: str,
    session: str,
    arguments: list[str],
    *,
    timeout: int = 30,
    check: bool = True,
    capture: bool = True,
    scratch: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    for attempt in range(3):
        try:
            result = browser_runtime.run_agent(
                namespace,
                session,
                arguments,
                timeout=timeout,
                capture=capture,
                scratch=scratch,
            )
        except RuntimeError as error:
            raise BrowserQAError(str(error)) from error
        detail = (result.stdout or "") + (result.stderr or "")
        if result.returncode and "Failed to connect" in detail and attempt < 2:
            time.sleep(1)
            continue
        break
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise BrowserQAError(f"agent-browser 失败：{' '.join(arguments)}\n{detail}")
    return result


def result_failures(
    state: dict[str, Any],
    expected: list[str],
    width: int,
    height: int,
    mode_settings: dict[str, Any] | None = None,
    *,
    expected_code: dict[str, tuple[CodeRecord, ...]] | None = None,
) -> list[str]:
    failures = [str(item) for item in state.get("errors", [])]
    results = state.get("results", [])
    if len(results) != len(expected):
        failures.append(f"XHTML 数量不匹配：期望 {len(expected)}，实际 {len(results)}")
    found = {item.get("path") for item in results}
    missing = [path for path in expected if path not in found]
    if missing:
        failures.append("未检查 XHTML：" + ", ".join(missing))
    for item in results:
        path = item.get("path", "unknown")
        if expected_code is not None and path in expected_code:
            actual_code = item.get("codeRecords")
            if not isinstance(actual_code, list):
                failures.append(f"{path}: codeRecords 缺失或不是列表")
            else:
                try:
                    validate_code_records(expected_code[path], actual_code)
                except (EpubSemanticError, KeyError, TypeError, AttributeError) as error:
                    failures.append(f"{path}: codeRecords 保全失败：{error}")
        if item.get("viewport") != [width, height]:
            failures.append(f"{path}: viewport={item.get('viewport')}")
        for field in (
            "brokenImages",
            "formulasWithoutAlt",
            "mathmlCount",
            "texAnnotationCount",
            "missingXhtmlTargets",
            "missingFragmentTargets",
            "noterefTargetFailures",
            "backlinkTargetFailures",
            "noterefNavigationFailures",
            "backlinkNavigationFailures",
        ):
            if item.get(field):
                failures.append(f"{path}: {field}={item[field]}")
        if item.get("overflowCount"):
            offenders = "; ".join(
                f"{element.get('selector')} rect={element.get('rect')} "
                f"scroll={element.get('scrollWidth')}/{element.get('clientWidth')}"
                for element in item.get("overflowElements", [])
            )
            detail = f" overflowWidth={item.get('overflowWidth')}"
            if offenders:
                detail += f" offenders={offenders}"
            failures.append(f"{path}: overflowCount={item['overflowCount']}{detail}")
        if mode_settings is not None:
            actual = item.get("readerSettings")
            if not isinstance(actual, dict):
                failures.append(f"{path}: readerSettings 缺失")
                continue
            expected_scheme = mode_settings.get("color_scheme")
            if expected_scheme and actual.get("actualColorScheme") != expected_scheme:
                failures.append(
                    f"{path}: colorScheme={actual.get('actualColorScheme')}"
                )
            if mode_settings.get("disable_publisher_css") and not actual.get(
                "publisherCssDisabled"
            ):
                failures.append(f"{path}: publisherCssDisabled=false")
            if mode_settings.get("user_font_family") and not actual.get(
                "userFontApplied"
            ):
                failures.append(f"{path}: userFontApplied=false")
            if mode_settings.get("user_font_size_px") and not actual.get(
                "userFontSizeApplied"
            ):
                failures.append(f"{path}: userFontSizeApplied=false")
            if mode_settings.get("disable_publisher_css") and not actual.get(
                "reflowWrapApplied"
            ):
                failures.append(f"{path}: reflowWrapApplied=false")
            if mode_settings.get("disable_publisher_css") and (
                actual.get("actualTextSpacingTrim") != "space-all"
                or not actual.get("fullwidthPunctuationApplied")
            ):
                failures.append(f"{path}: fullwidthPunctuationApplied=false")
            if mode_settings.get("disable_publisher_css") and not actual.get(
                "mediaFittingApplied"
            ):
                failures.append(f"{path}: mediaFittingApplied=false")
            if mode_settings.get("disable_publisher_css") and not actual.get(
                "codeScrollingApplied"
            ):
                failures.append(f"{path}: codeScrollingApplied=false")
            if (
                mode_settings.get("disable_publisher_css")
                and item.get("formulaCount", 0) > 0
                and not actual.get("mathDisplayScrollingApplied")
            ):
                failures.append(f"{path}: mathDisplayScrollingApplied=false")
            if (
                mode_settings.get("disable_publisher_css")
                and item.get("tableMathImageCount", 0) > 0
                and not actual.get("tableMathScrollingApplied")
            ):
                failures.append(f"{path}: tableMathScrollingApplied=false")
    return failures


def screenshot_paths(state: dict[str, Any], maximum: int) -> list[str]:
    results = state.get("results", [])
    if not results or maximum <= 0:
        return []
    chosen: list[str] = []

    def add(path: str | None) -> None:
        if path and path not in chosen and len(chosen) < maximum:
            chosen.append(path)

    for item in results:
        if item.get("overflowCount") or item.get("brokenImages"):
            add(item.get("path"))
    add(next((item["path"] for item in results if item["path"].endswith("cover.xhtml")), None))
    add(next((item["path"] for item in results if item["path"].endswith("nav.xhtml")), None))
    add(max(results, key=lambda item: item.get("formulaCount", 0)).get("path"))
    add(next((item["path"] for item in results if item.get("tableCount")), None))
    add(next((item["path"] for item in results if item.get("footnoteRefCount")), None))
    add(results[-1].get("path"))
    return chosen


def failure_screenshot_paths(
    state: dict[str, Any],
    expected: list[str],
    width: int,
    height: int,
    mode_settings: dict[str, Any],
    expected_code: dict[str, tuple[CodeRecord, ...]],
    maximum: int,
) -> list[str]:
    if maximum <= 0:
        return []
    chosen: list[str] = []
    for item in state.get("results", []):
        path = item.get("path")
        if not path or path not in expected or path in chosen:
            continue
        if result_failures(
            {"results": [item]}, [path], width, height, mode_settings,
            expected_code=expected_code,
        ):
            chosen.append(path)
            if len(chosen) >= maximum:
                break
    return chosen


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def mode_summary(payload: dict[str, Any]) -> list[str]:
    settings = json.dumps(payload["settings"], ensure_ascii=False, sort_keys=True)
    actual = json.dumps(
        payload.get("actual_settings"), ensure_ascii=False, sort_keys=True
    )
    return [
        f"mode={payload['mode']}",
        f"settings={settings}",
        f"actual_settings={actual}",
        f"viewport={payload['viewport'][0]}x{payload['viewport'][1]}",
        f"xhtml_count={payload['xhtml_count']}",
        f"formula_count={payload['formula_count']}",
        f"code_block_count={payload['code_block_count']}",
        f"xhtml_link_count={payload['xhtml_link_count']}",
        f"fragment_link_count={payload['fragment_link_count']}",
        f"noteref_target_checks={payload['noteref_target_checks']}",
        f"backlink_target_checks={payload['backlink_target_checks']}",
        f"noteref_navigation_checks={payload['noteref_navigation_checks']}",
        f"backlink_navigation_checks={payload['backlink_navigation_checks']}",
        f"failures={len(payload['failures'])}",
        (
            "launch_timeout_recovered="
            f"{str(payload['events']['launch_timeout_recovered']).lower()}"
        ),
        f"poll_timeouts={payload['events']['poll_timeouts']}",
        (
            "viewport_command_eof="
            f"{str(payload['events']['viewport_command_eof']).lower()}"
        ),
        (f"close_returncode={payload['events']['close_returncode']}"),
    ]


def run_mode(
    epub: Path,
    unpacked: Path,
    members: list[str],
    evidence: Path,
    digest: str,
    namespace: str,
    mode_settings: dict[str, Any],
    *,
    timeout: int,
    page_timeout: int,
    screenshots: int,
    available_members: list[str] | None = None,
    screenshot_policy: str = "failures",
) -> dict[str, Any]:
    mode = str(mode_settings["name"])
    width, height = (int(value) for value in mode_settings["viewport"])
    mode_dir = evidence / mode
    mode_dir.mkdir(parents=True)
    harness = mode_dir / "harness.html"
    expected = write_harness(
        harness,
        members,
        width,
        height,
        page_timeout,
        mode_settings,
        base_prefix="../unpacked/",
        available_members=available_members,
    )
    try:
        expected_code = {
            path: code_from_xhtml([ET.parse(unpacked / member).getroot()])
            for path, member in zip(expected, members)
        }
    except (ET.ParseError, EpubSemanticError) as error:
        raise BrowserQAError(f"浏览器代码核对基准无效：{error}") from error
    session = f"epub-{digest[:10].lower()}-{os.getpid()}-{mode}-{time.time_ns() % 1_000_000}"
    events: dict[str, Any] = {
        "namespace": namespace,
        "session": session,
        "launch_timeout_recovered": False,
        "close_timeouts": 0,
        "close_errors": [],
        "cleanup_required": False,
        "poll_timeouts": 0,
        "viewport_command_eof": False,
        "media_command": None,
        "close_returncode": None,
        "runtime_dir": str((mode_dir / "runtime").resolve()),
    }
    write_json(
        mode_dir / "run.json",
        {
            "epub": str(epub.resolve()),
            "sha256": digest,
            "namespace": namespace,
            "session": session,
            "mode": mode,
            "settings": mode_settings,
            "viewport": [width, height],
        },
    )
    state: dict[str, Any] | None = None
    run_error: str | None = None
    screenshot_errors: list[str] = []
    try:
        try:
            run_agent(
                namespace,
                session,
                [
                    "--headed",
                    "false",
                    "--allow-file-access",
                    "open",
                    "about:blank",
                ],
                timeout=45,
                capture=False,
                scratch=mode_dir / "runtime",
            )
        except subprocess.TimeoutExpired:
            ping = run_agent(
                namespace,
                session,
                ["eval", "location.href"],
                timeout=15,
                check=False,
            )
            if ping.returncode:
                raise
            events["launch_timeout_recovered"] = True
        viewport = run_agent(
            namespace,
            session,
            ["set", "viewport", str(width), str(height)],
            check=False,
        )
        if viewport.returncode:
            detail = viewport.stdout + viewport.stderr
            if KNOWN_VIEWPORT_EOF not in detail:
                raise BrowserQAError(f"设置 viewport 失败：{detail.strip()}")
            events["viewport_command_eof"] = True
        dimensions = parse_json_output(
            run_agent(
                namespace,
                session,
                ["eval", "({width:innerWidth,height:innerHeight})"],
            ).stdout
        )
        if dimensions != {"width": width, "height": height}:
            raise BrowserQAError(f"viewport 未生效：{dimensions}")
        color_scheme = mode_settings.get("color_scheme")
        if color_scheme:
            run_agent(namespace, session, ["set", "media", color_scheme])
            events["media_command"] = color_scheme
        run_agent(
            namespace,
            session,
            ["open", harness.resolve().as_uri()],
            timeout=max(90, page_timeout + 10),
        )
        run_agent(namespace, session, ["eval", "startQA()"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                progress = parse_json_output(
                    run_agent(
                        namespace,
                        session,
                        [
                            "eval",
                            "({done:qaState.done,count:qaState.results.length,errors:qaState.errors.length})",
                        ],
                    ).stdout
                )
            except subprocess.TimeoutExpired:
                events["poll_timeouts"] += 1
                continue
            write_json(mode_dir / "progress.json", progress)
            if progress.get("done"):
                state = parse_json_output(
                    run_agent(namespace, session, ["eval", "qaState"]).stdout
                )
                break
            time.sleep(2)
        if state is None:
            raise BrowserQAError(f"浏览器验收超过 {timeout} 秒")

        screenshots_dir = mode_dir / "screenshots"
        screenshots_dir.mkdir()
        if screenshot_policy == "representative":
            paths = screenshot_paths(state, screenshots)
        else:
            paths = failure_screenshot_paths(
                state, expected, width, height, mode_settings, expected_code, screenshots
            )
        for index, path in enumerate(paths, 1):
            try:
                run_agent(
                    namespace,
                    session,
                    ["eval", f"qaShowPage({json.dumps(path)})"],
                    timeout=page_timeout + 10,
                )
                stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(path).stem)
                target = screenshots_dir / f"{index:02d}-{stem}.png"
                run_agent(namespace, session, ["screenshot", str(target.resolve())])
            except (BrowserQAError, OSError, subprocess.TimeoutExpired) as error:
                screenshot_errors.append(f"{path}: 截图失败：{error}")
    except (BrowserQAError, OSError, subprocess.TimeoutExpired) as error:
        run_error = str(error)
        (mode_dir / "error.txt").write_text(run_error + "\n", encoding="utf-8")
    finally:
        for _ in range(3):
            try:
                closed = run_agent(
                    namespace,
                    session,
                    ["close"],
                    timeout=30,
                    check=False,
                    capture=False,
                )
            except (BrowserQAError, OSError, subprocess.TimeoutExpired) as error:
                events["close_timeouts"] += 1
                events["close_errors"].append(str(error))
                continue
            events["close_returncode"] = closed.returncode
            if not closed.returncode:
                break
        events["cleanup_required"] = events["close_returncode"] != 0

    if state is None:
        state = {"errors": [], "results": []}
    failures = (
        result_failures(
            state, expected, width, height, mode_settings,
            expected_code=expected_code,
        )
        + screenshot_errors
    )
    if run_error:
        failures.insert(0, run_error)
    if events["cleanup_required"]:
        failures.append(f"session 未关闭：namespace={namespace} session={session}")
    actual_settings = next(
        (
            item.get("readerSettings")
            for item in state.get("results", [])
            if isinstance(item.get("readerSettings"), dict)
        ),
        None,
    )
    results = state.get("results", [])
    observed_paths = {item.get("path") for item in results} & set(expected)
    payload: dict[str, Any] = {
        "epub": str(epub.resolve()),
        "sha256": digest,
        "mode": mode,
        "settings": mode_settings,
        "actual_settings": actual_settings,
        "viewport": [width, height],
        "xhtml_count": len(observed_paths),
        "formula_count": sum(item.get("formulaCount", 0) for item in results),
        "code_block_count": sum(
            len(expected_code[path]) for path in observed_paths
        ),
        "xhtml_link_count": sum(item.get("xhtmlLinkCount", 0) for item in results),
        "fragment_link_count": sum(
            item.get("fragmentLinkCount", 0) for item in results
        ),
        "noteref_target_checks": sum(
            item.get("noterefTargetChecks", 0) for item in results
        ),
        "backlink_target_checks": sum(
            item.get("backlinkTargetChecks", 0) for item in results
        ),
        "noteref_navigation_checks": sum(
            item.get("noterefNavigationChecks", 0) for item in results
        ),
        "backlink_navigation_checks": sum(
            item.get("backlinkNavigationChecks", 0) for item in results
        ),
        "failures": failures,
        "events": events,
        "results": results,
    }
    write_json(mode_dir / "results.json", payload)
    (mode_dir / "summary.txt").write_text(
        "\n".join(mode_summary(payload)) + "\n", encoding="utf-8"
    )
    return payload


def run_browser_qa(
    epub: Path,
    evidence: Path,
    *,
    width: int = 390,
    height: int = 844,
    timeout: int = 600,
    page_timeout: int = 30,
    screenshots: int = 6,
    mode: str = ALL_MODES,
    selected_members: list[str] | None = None,
    screenshot_policy: str = "failures",
) -> dict[str, Any]:
    if not epub.is_file():
        raise BrowserQAError(f"EPUB 不存在：{epub}")
    if min(width, height, timeout, page_timeout) <= 0 or screenshots < 0:
        raise BrowserQAError("viewport、超时必须为正数，截图数量不能为负数")
    if screenshot_policy not in {"failures", "representative"}:
        raise BrowserQAError("未知截图策略：" + str(screenshot_policy))
    if evidence.exists():
        raise BrowserQAError(f"证据目录已经存在：{evidence}")
    modes = selected_modes(mode, width=width, height=height)
    evidence.mkdir(parents=True)
    unpacked = evidence / "unpacked"
    try:
        available = safe_extract(epub, unpacked)
        if not available:
            raise BrowserQAError("EPUB 不含 XHTML")
        members = select_members(available, selected_members)
    except (BrowserQAError, OSError, zipfile.BadZipFile) as error:
        (evidence / "error.txt").write_text(str(error) + "\n", encoding="utf-8")
        if isinstance(error, BrowserQAError):
            raise
        raise BrowserQAError(str(error)) from error
    selected_set = set(members)
    coverage = {
        "kind": "full" if selected_members is None else "selected",
        "available_members": available,
        "selected_members": members,
        "unselected_members": [
            member for member in available if member not in selected_set
        ],
        "checked_members": [],
        "unchecked_members": available,
    }
    digest = sha256(epub)
    project_key = hashlib.sha256(str(ROOT).encode("utf-8")).hexdigest()[:12]
    namespace = f"translator-{project_key}"
    run_record = {
        "epub": str(epub.resolve()),
        "sha256": digest,
        "mode": mode,
        "namespace": namespace,
        "coverage": coverage,
        "modes": [
            {
                "name": item["name"],
                "settings": item,
                "viewport": item["viewport"],
            }
            for item in modes
        ],
    }
    write_json(evidence / "run.json", run_record)
    write_json(
        evidence / "progress.json",
        {"done": False, "mode_count": len(modes), "completed_modes": []},
    )
    mode_payloads: list[dict[str, Any]] = []
    for item in modes:
        payload = run_mode(
            epub,
            unpacked,
            members,
            evidence,
            digest,
            namespace,
            item,
            timeout=timeout,
            page_timeout=page_timeout,
            screenshots=screenshots,
            available_members=available,
            screenshot_policy=screenshot_policy,
        )
        mode_payloads.append(payload)
        write_json(
            evidence / "progress.json",
            {
                "done": len(mode_payloads) == len(modes),
                "mode_count": len(modes),
                "completed_modes": [entry["mode"] for entry in mode_payloads],
                "failures": {
                    entry["mode"]: len(entry["failures"]) for entry in mode_payloads
                },
            },
        )
    failures = [
        f"{item['mode']}: {failure}"
        for item in mode_payloads
        for failure in item["failures"]
    ]
    paths_to_members = {"../unpacked/" + member: member for member in members}
    checked_by_mode = [
        [
            paths_to_members[result["path"]]
            for result in item["results"]
            if result.get("path") in paths_to_members
        ]
        for item in mode_payloads
    ]
    checked_in_all = set(members)
    for checked in checked_by_mode:
        checked_in_all.intersection_update(checked)
    coverage = {
        **coverage,
        "checked_members": [member for member in available if member in checked_in_all],
        "unchecked_members": [
            member for member in available if member not in checked_in_all
        ],
    }
    run_record["coverage"] = coverage
    write_json(evidence / "run.json", run_record)
    mode_records = [
        {
            **{
                key: item[key]
                for key in (
                    "mode",
                    "settings",
                    "sha256",
                    "actual_settings",
                    "viewport",
                    "xhtml_count",
                    "formula_count",
                    "code_block_count",
                    "xhtml_link_count",
                    "fragment_link_count",
                    "noteref_target_checks",
                    "backlink_target_checks",
                    "noteref_navigation_checks",
                    "backlink_navigation_checks",
                    "failures",
                    "events",
                )
            },
            "checked_members": checked,
        }
        for item, checked in zip(mode_payloads, checked_by_mode)
    ]
    payload: dict[str, Any] = {
        "epub": str(epub.resolve()),
        "sha256": digest,
        "mode": mode,
        "namespace": namespace,
        "mode_count": len(mode_payloads),
        "xhtml_count": len(coverage["checked_members"]),
        "total_xhtml_checks": sum(item["xhtml_count"] for item in mode_payloads),
        "coverage": coverage,
        "failures": failures,
        "modes": mode_records,
        "sessions": [item["events"]["session"] for item in mode_payloads],
    }
    write_json(evidence / "results.json", payload)
    summary: list[str] = [
        f"epub={epub.resolve()}",
        f"sha256={digest}",
        f"mode={mode}",
        f"mode_count={len(mode_payloads)}",
        f"xhtml_count_per_mode={payload['xhtml_count']}",
        f"total_xhtml_checks={payload['total_xhtml_checks']}",
        f"failures={len(failures)}",
    ]
    for item in mode_payloads:
        summary.extend(mode_summary(item))
    (evidence / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    if failures:
        (evidence / "error.txt").write_text(
            "浏览器验收失败：\n" + "\n".join(failures) + "\n", encoding="utf-8"
        )
        raise BrowserQAError("浏览器验收失败：\n" + "\n".join(failures))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("epub", type=Path)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--page-timeout", type=int, default=30)
    parser.add_argument("--screenshots", type=int, default=6)
    parser.add_argument(
        "--screenshot-policy", choices=("failures", "representative"), default="failures"
    )
    parser.add_argument(
        "--member", action="append", dest="members", help="精确的包内 XHTML 路径；可重复"
    )
    parser.add_argument(
        "--mode",
        choices=(ALL_MODES, *DEFAULT_MODE_NAMES),
        default=ALL_MODES,
        help="默认执行完整读者矩阵；选择单模式仅用于诊断",
    )
    args = parser.parse_args(argv)
    try:
        payload = run_browser_qa(
            args.epub.resolve(),
            args.evidence.resolve(),
            width=args.width,
            height=args.height,
            timeout=args.timeout,
            page_timeout=args.page_timeout,
            screenshots=args.screenshots,
            mode=args.mode,
            selected_members=args.members,
            screenshot_policy=args.screenshot_policy,
        )
    except BrowserQAError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    formula_count = sum(item["formula_count"] for item in payload["modes"])
    print(
        f"browser-qa: modes={payload['mode_count']} "
        f"xhtml_per_mode={payload['xhtml_count']} "
        f"formulas={formula_count} sha256={payload['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
