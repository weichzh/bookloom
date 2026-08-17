# Bookloom

Bookloom 是一个本地优先的书籍内容恢复、翻译和多格式出版工作流。Git 只保存共享工具、测试与规则；原始来源放在 `Books/`，正式底稿、状态、资产和成品放在 `Works/<书名>/`，这两个目录均被忽略，不会进入公开仓库。

## 环境要求

- Git、Python 3.12 或更高版本、[uv](https://docs.astral.sh/uv/)。
- 生成或检查 EPUB：Java 8 或更高版本，以及下方安装的 EPUBCheck。
- 按实际格式安装 Pandoc、Typst、MuPDF 的 `mutool`、TeX 工具链、ImageMagick 或 `agent-browser`；`doctor` 会按任务列出缺项。

## 安装

以下命令可直接用于 PowerShell、bash 和 zsh：

```shell
git clone --recurse-submodules https://github.com/weichzh/bookloom.git
cd bookloom
uv sync --locked
uv run python scripts/setup_epubcheck.py
uv run python tools/translator.py --help
```

已有检出若尚未初始化子模块，先运行：

```shell
git submodule update --init --depth 1 tools/epubcheck
uv run python scripts/setup_epubcheck.py
```

`tools/epubcheck` 固定到官方 `w3c/epubcheck` v5.3.0 源码提交。安装脚本下载同版本官方发行包、校验固定 SHA-256，并把运行文件放到子模块已忽略的 `target/`；下载内容不会提交到本仓库。含空格的路径应使用引号。

## 常用入口

```shell
uv run python tools/translator.py init SOURCE --id ID --title TITLE
uv run python tools/translator.py render WORK --pages 1-5 --activity source-review
uv run python tools/translator.py prepare WORK --activity content-repair
uv run python tools/translator.py check WORK --activity none
uv run python tools/translator.py refresh WORK --lang zh-CN --target epub --activity translation
uv run python tools/translator.py finalize WORK --activity none
uv run python tools/translator.py complete WORK --activity visual-qa
uv run python tools/translator.py deliver WORK --lang zh-CN --target epub --to TARGET.epub --activity none
uv run python tools/translator.py benchmark
uv run python tools/translator.py clean WORK --activity none
```

精确参数以 `uv run python tools/translator.py --help` 和各子命令的 `--help` 为准。固定顺序是 `prepare` → `refresh`／`finalize` → `complete` → `deliver`；机器契约见 `docs/codebase/CONTRACTS.md`，翻译流程见 `docs/workflow/TRANSLATION.md`，EPUB 规则见 `docs/workflow/EPUB.md`。

## 验证

```shell
uv lock --check
uv run --locked python scripts/doc_length_check.py
uv run --locked python -m pytest -q
```

GitHub Actions 会在 Windows、macOS 和 Linux 上运行同一组检查。测试和构建证据只写入被忽略的 `.tmp/` 与 `.local/`；不要把任何书籍内容移出 `Books/` 或 `Works/`。

## 许可证

Bookloom 使用 [MIT License](LICENSE)。EPUBCheck 子模块及其发行文件遵循上游许可证。
