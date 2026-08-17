#!/usr/bin/env python3

"""Install the pinned official EPUBCheck release beside its source submodule."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUBCHECK_ROOT = ROOT / "tools" / "epubcheck"
INSTALL_ROOT = EPUBCHECK_ROOT / "target" / "bookloom"
EPUBCHECK_JAR = INSTALL_ROOT / "epubcheck.jar"
VERSION = "5.3.0"
VERSION_FILE = INSTALL_ROOT / ".version"
ARCHIVE = ROOT / ".tmp" / f"epubcheck-{VERSION}.zip"
DOWNLOAD_URL = (
    f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}/"
    f"epubcheck-{VERSION}.zip"
)
EXPECTED_SHA256 = (
    "6c07e68584b2e2ce2f89fe06e1246dfead3eb36b46b340e7d93524f29dcff6c5"
)


def archive_member_path(base: Path, name: str) -> Path:
    member = Path(name.replace("\\", "/"))
    if (
        member.is_absolute()
        or member.drive
        or ".." in member.parts
        or (member.parts and member.parts[0].endswith(":"))
    ):
        raise ValueError(f"压缩包包含不安全路径：{name}")
    target = (base / member).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError as error:
        raise ValueError(f"压缩包路径越出解包目录：{name}") from error
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_archive() -> None:
    if ARCHIVE.is_file() and sha256(ARCHIVE) == EXPECTED_SHA256:
        return
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    partial = ARCHIVE.with_suffix(".zip.part")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(
        DOWNLOAD_URL, headers={"User-Agent": "bookloom"}
    )
    try:
        with urllib.request.urlopen(request) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(partial) != EXPECTED_SHA256:
            raise ValueError("EPUBCheck 发行包的 SHA-256 不匹配")
        os.replace(partial, ARCHIVE)
    finally:
        partial.unlink(missing_ok=True)


def extract_archive(staging: Path) -> Path:
    with zipfile.ZipFile(ARCHIVE) as archive:
        for info in archive.infolist():
            target = archive_member_path(staging, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    release = staging / f"epubcheck-{VERSION}"
    if not (release / "epubcheck.jar").is_file() or not (release / "lib").is_dir():
        raise ValueError("EPUBCheck 发行包结构不完整")
    return release


def install() -> None:
    if not (EPUBCHECK_ROOT / "pom.xml").is_file():
        raise FileNotFoundError(
            "EPUBCheck 子模块未初始化；请运行："
            "git submodule update --init --depth 1 tools/epubcheck"
        )
    if (
        EPUBCHECK_JAR.is_file()
        and VERSION_FILE.is_file()
        and VERSION_FILE.read_text(encoding="ascii", errors="ignore").strip()
        == VERSION
    ):
        ARCHIVE.unlink(missing_ok=True)
        print(f"EPUBCheck {VERSION} 已就绪：{EPUBCHECK_JAR}")
        return

    download_archive()
    try:
        target_root = EPUBCHECK_ROOT / "target"
        target_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".bookloom-", dir=target_root
        ) as directory:
            release = extract_archive(Path(directory))
            (release / ".version").write_text(VERSION + "\n", encoding="ascii")
            if INSTALL_ROOT.is_symlink() or INSTALL_ROOT.is_junction():
                raise ValueError(f"拒绝替换链接目录：{INSTALL_ROOT}")
            if INSTALL_ROOT.exists():
                try:
                    INSTALL_ROOT.resolve().relative_to(target_root.resolve())
                except ValueError as error:
                    raise ValueError(
                        f"安装目录越出 EPUBCheck target：{INSTALL_ROOT}"
                    ) from error
                shutil.rmtree(INSTALL_ROOT)
            release.replace(INSTALL_ROOT)
    finally:
        ARCHIVE.unlink(missing_ok=True)
    print(f"EPUBCheck {VERSION} 已安装：{EPUBCHECK_JAR}")


def main() -> int:
    try:
        install()
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
