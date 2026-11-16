# 原生标题注的来源绑定

## Status

implemented

## Problem

LaTeX 标题内的原生 footnote 由 Pandoc 表达为 Note，而共享目录规范化只支持显式 noteref Link，因而拒绝合法标题；实际 writer 可以保留原位、可见注号和完整注文。

## Scope

- 原生 Note 不进入目录可见标签，正文标题注保原位，不搬到正文后。
- 生产目录核验绑定该来源 Note 的真实引用、顺序、writer 号／ID、目标及完整注文；使用与实际 writer 相同的拆分配置及其原生 Note 编号规则，不把新生成号冒称原印号。原印号仍由单书来源证据核定。
- 复用现有注释／语义检查，保留显式 Link 的原 ID、注号、href 和旧调用条件。新增配置或上下文仅用于新的原生 Note 路径。
- 范围限 tools/epub_semantics.py、必要的 translator 调用、对应测试及直接契约；不改阅读器、正文、全局样式或现有成品。

## Acceptance Criteria

- 真实 Pandoc LaTeX→EPUB 保标题原注、完整注文及无注号导航；覆盖章内非标题注、跨章／拆分和普通上标正常对照。
- 改号、改引用 ID／目标、丢注、改／截注文、挪动标题内注位的具体反例失败；显式 Link 及原无注标题回归保持。
- 实际标题注的原印号与实际目标对应；增量检查后才冻结全量。

## Result

- 已接入原生 Note 的目录标签、实际分卷计数、正文引用位置、writer 身份与完整注文来源绑定；旧 Link 调用条件不变。
- 真实 Pandoc split 1／2、章内普通注、嵌套容器、synthetic title、未知块明确失败及破坏对照共 77 项通过；相关既有模块的 105 项／22 子测试证据按未变范围复用。
- 真实源 AST 与 XHTML 中标题注的原位、引用、唯一目标与完整正文已经局部消费；正式全量另行执行。直接机器契约已更新。

## Review

独立审查核实默认 split=2 只用于 Markdown 专用 QA，关闭误用 LaTeX 的疑点；发现新 Note 的可见链接文字正确但 href 可被改到另一有效目标，补充来源链接数量／外部 URL／跨文件片段身份绑定及实际 writer 正反例，blocking 关闭。
