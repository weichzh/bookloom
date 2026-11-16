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
| [docs/workflow/PROTOCOL.md](workflow/PROTOCOL.md) | 任务与变更生命周期 | audit, book, shared-change |
| [docs/codebase/CONTRACTS.md](codebase/CONTRACTS.md) | manifest、CLI 与日志机器语义 | book, shared-change |
| [docs/codebase/SOURCE_FIGURES.md](codebase/SOURCE_FIGURES.md) | 来源图形 runtime、候选状态、来源绑定与增量复用 | book, shared-change |
| [docs/workflow/TRANSLATION.md](workflow/TRANSLATION.md) | 内容恢复、翻译与交付流程 | book |
| [docs/workflow/EPUB.md](workflow/EPUB.md) | EPUB 生成与验收 | book, shared-change |
| [docs/changes/epub-source-intake.md](changes/epub-source-intake.md) | 已批准的来源探查、身份映射与信息保全变更 | shared-change |
| [docs/changes/reading-edition-structure.md](changes/reading-edition-structure.md) | 已实施的阅读版标准流程、结构与验收结果 | shared-change |
| [docs/changes/reading-edition-production.md](changes/reading-edition-production.md) | 已实施的生产门禁、原页导航、勘误与无窗口执行 | shared-change |
| [docs/changes/reading-edition-batch-compatibility.md](changes/reading-edition-batch-compatibility.md) | 已批准的目录层级、原页导航、正文入口、注释及读者模式兼容 | shared-change |
| [docs/changes/reading-edition-caption-and-heading-alias.md](changes/reading-edition-caption-and-heading-alias.md) | 已批准的原生可见图注与标准页点首标题别名修复 | shared-change |
| [docs/changes/pure-title-source-units.md](changes/pure-title-source-units.md) | 纯标题来源单元原生元数据表达 | shared-change |
| [docs/changes/reader-wide-table-adaptation.md](changes/reader-wide-table-adaptation.md) | 关闭出版社样式时原生宽表的局部横滚适配 | shared-change |
| [docs/changes/raster-math-dark-backing.md](changes/raster-math-dark-backing.md) | 原生栅格数学图像的深色可读衬底 | shared-change |
| [docs/changes/reader-formula-em-sizing.md](changes/reader-formula-em-sizing.md) | 关闭出版社样式时数学图像的相对尺寸基础适配 | shared-change |
| [docs/changes/native-epub-list-delimiters.md](changes/native-epub-list-delimiters.md) | 原生 Decimal／TwoParens 列表的标准 EPUB 编号及读者基础适配 | shared-change |
| [docs/changes/automated-incremental-validation.md](changes/automated-incremental-validation.md) | 自动化优先、异常定点视觉与渐进模块检查 | shared-change |
| [docs/changes/numbered-figure-math-reference-boundary.md](changes/numbered-figure-math-reference-boundary.md) | 编号图追溯中的数学对象和正文引用边界 | shared-change |
| [docs/changes/publisher-fullwidth-punctuation.md](changes/publisher-fullwidth-punctuation.md) | 出版社样式下的中文全宽标点与横向滚动 | shared-change |
| [docs/changes/heading-image-alternatives.md](changes/heading-image-alternatives.md) | 目录与真实图片标题的语义一致性 | shared-change |
| [docs/changes/native-multiparagraph-figure-captions.md](changes/native-multiparagraph-figure-captions.md) | 原生 Figure 的多段图注与短替代文本 | shared-change |
| [docs/changes/code-comparison-table-scroll.md](changes/code-comparison-table-scroll.md) | 原生代码对照表的局部滚动与保行 | shared-change |
| [docs/changes/explicit-deferred-page-objects.md](changes/explicit-deferred-page-objects.md) | 整页图表后置的显式来源页序及精确目标核验 | shared-change |
| [docs/changes/source-ocr-typed-routing.md](changes/source-ocr-typed-routing.md) | 来源 OCR 类型路由、字段保护与有界 DOE | shared-change |
| [docs/changes/math-heading-navigation-labels.md](changes/math-heading-navigation-labels.md) | 数学标题可读导航与完整来源标题的双层核验 | shared-change |
| [docs/changes/native-block-figures.md](changes/native-block-figures.md) | 非图像原生 Math／代码／规则块的标准 Figure／Caption | shared-change |
| [docs/changes/native-data-table-scroll.md](changes/native-data-table-scroll.md) | 原生数据宽表的按需局部滚动及字段保全 | shared-change |
| [docs/changes/latex-definition-list-labels.md](changes/latex-definition-list-labels.md) | LaTeX 原生定义列表中的来源顺序标签与交叉引用 | shared-change |
| [docs/changes/latex-unlisted-headings.md](changes/latex-unlisted-headings.md) | LaTeX 显式不列目录的真实标题 | shared-change |
| [docs/changes/navigation-single-math-symbols.md](changes/navigation-single-math-symbols.md) | 数学标题导航的两个确定单符号 | shared-change |
| [docs/changes/math-image-content-box.md](changes/math-image-content-box.md) | 数学图像内容盒的 em 高度与内边距预算 | shared-change |
| [docs/changes/math-row-tags.md](changes/math-row-tags.md) | 多号对齐公式的原生逐行编号和选择性缓存失效 | shared-change |
| [docs/changes/display-formula-local-scroll.md](changes/display-formula-local-scroll.md) | 生成展示公式的相对尺寸与标准局部横滚 | shared-change |
| [docs/changes/latex-reading-object-compatibility.md](changes/latex-reading-object-compatibility.md) | 已实施的原生附录、富文本算法、纯锚点段落与 bbm 字形兼容 | shared-change |
| [docs/changes/native-heading-notes.md](changes/native-heading-notes.md) | 原生标题 Note 的目录与完整注释来源绑定 | shared-change |
| [docs/changes/latex-native-caption-resources.md](changes/latex-native-caption-resources.md) | 原生计数图注、PNG/JPEG 回退与完整宽表语义 | shared-change |
| [docs/changes/formula-runtime-bound-and-checkpoint.md](changes/formula-runtime-bound-and-checkpoint.md) | 固定公式工具的线程上界、无窗口调用与失败版本戳检查点 | shared-change |
| [docs/changes/public-docs-and-source-regression.md](changes/public-docs-and-source-regression.md) | 公共文档边界、来源回归与模型分工实验 | shared-change |
| [docs/changes/reader-semantic-edge-closure.md](changes/reader-semantic-edge-closure.md) | 原生诗行注释与显式宽表公式的适用条件修复 | shared-change |
| [docs/changes/finalize-browser-time-budget.md](changes/finalize-browser-time-budget.md) | 最终验收的可选有限浏览器时间预算，保原默认与全部检查 | shared-change |
| [docs/changes/native-table-page-boundaries.md](changes/native-table-page-boundaries.md) | 原生页点的完整表格边界与列宽保全 | shared-change |
| [docs/changes/source-figure-localization.md](changes/source-figure-localization.md) | 原生来源图形、图注配对及 CV 候选的统一入口与复核边界 | shared-change |
