from pathlib import Path

import pytest

from scripts.setup_epubcheck import archive_member_path


def test_archive_member_path_blocks_escape() -> None:
    with pytest.raises(ValueError, match="不安全路径"):
        archive_member_path(Path.cwd() / ".tmp", "../escape")
