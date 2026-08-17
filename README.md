# Bookloom

这是一个用于书籍内容恢复、翻译和多格式派生的工具仓库。原始来源放在 `Books/`，每本书的正式底稿、状态、资产和成品放在 `Works/<书名>/`；两个目录均为本机私有内容，不进入 Git。PDF、HTML 和 EPUB 成品由统一 CLI 生成。

Agent 从 `AGENTS.md`、`CONTEXT.md` 和 `docs/ACTIVE.md` 启动；按任务选读 `docs/INDEX.md` 中的一个 Context Bundle。

## 常用入口

```powershell
uv run tools/translator.py init SOURCE --id ID --title TITLE
uv run tools/translator.py render WORK --pages 1-5 --activity source-review
uv run tools/translator.py prepare WORK --activity content-repair
uv run tools/translator.py check WORK --activity none
uv run tools/translator.py finalize WORK --activity none
uv run tools/translator.py complete WORK --activity visual-qa
uv run tools/translator.py deliver WORK --lang zh-CN --target epub --to TARGET.epub --activity none
uv run tools/translator.py benchmark
uv run tools/translator.py clean WORK --activity none
uv run python -m pytest -q
# 无 pytest 时的后备入口
uv run python -m unittest discover -s tests -p "test_*.py"
```

精确参数和迭代命令使用 `uv run tools/translator.py --help` 与子命令 `--help` 查询。固定顺序是 `prepare` → `refresh`／`finalize` → `complete` → `deliver`；机器契约见 `docs/codebase/CONTRACTS.md`，翻译流程见 `docs/workflow/TRANSLATION.md`，EPUB 生成与验收见 `docs/workflow/EPUB.md`。PDF 来源渲染使用 `mutool`。
