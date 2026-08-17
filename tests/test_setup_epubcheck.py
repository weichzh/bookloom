import tempfile
from pathlib import Path
from unittest import mock

import pytest

from scripts import setup_epubcheck

ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = ROOT / ".tmp" / "tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def test_archive_member_path_blocks_escape() -> None:
    with pytest.raises(ValueError, match="不安全路径"):
        setup_epubcheck.archive_member_path(ROOT / ".tmp", "../escape")


def test_target_root_blocks_link_escape() -> None:
    with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as directory:
        root = Path(directory) / "repo"
        epubcheck = root / "tools" / "epubcheck"
        outside = Path(directory) / "outside"
        epubcheck.mkdir(parents=True)
        outside.mkdir()
        try:
            (epubcheck / "target").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
        with mock.patch.object(setup_epubcheck, "ROOT", root):
            with mock.patch.object(
                setup_epubcheck, "EPUBCHECK_ROOT", epubcheck
            ):
                with pytest.raises(ValueError, match="链接目录"):
                    setup_epubcheck.checked_target_root()
