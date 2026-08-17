# 文档索引

本文件是当前文档的唯一注册表。历史计划、研究和 review 只从 Git 提交历史读取，不在工作树建立第二套档案。

## Context Bundles

- `audit`：读取事实所有者和一份直接证据；默认在回复中报告，不自动升级为实施。
- `book`：读取翻译规则、目标书 `manifest.toml` 与 `STATUS.md`；涉及 EPUB 时追加 EPUB 规则，涉及共享行为时转为 `shared-change`。
- `shared-change`：读取协议、活动 change 和一个直接相关的契约或工作流所有者；只在兼容性验证需要时加载代表性单书。

## 注册表

| 当前文档 | 唯一事实 | 消费者 |
| --- | --- | --- |
| [AGENTS.md](../AGENTS.md) | 启动与硬规则 | all |
| [CONTEXT.md](../CONTEXT.md) | 稳定目标、目录与所有权 | all |
| [docs/ACTIVE.md](ACTIVE.md) | 当前执行指针 | all |
| [docs/changes/cross-platform-packaging.md](changes/cross-platform-packaging.md) | 当前跨平台发布与依赖整理 | shared-change |
| [docs/workflow/PROTOCOL.md](workflow/PROTOCOL.md) | 任务与变更生命周期 | audit, book, shared-change |
| [docs/codebase/CONTRACTS.md](codebase/CONTRACTS.md) | manifest、CLI 与日志机器语义 | book, shared-change |
| [docs/workflow/TRANSLATION.md](workflow/TRANSLATION.md) | 内容恢复、翻译与交付流程 | book |
| [docs/workflow/EPUB.md](workflow/EPUB.md) | EPUB 生成与验收 | book, shared-change |
