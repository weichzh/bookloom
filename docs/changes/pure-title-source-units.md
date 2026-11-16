# 纯标题来源单元的元数据表达

## Status

implemented

## Problem

来源单元可能只有一个完整标题。去掉重复标签后，把 source-unit Span 放进 Header 会被正式 prepare 拒绝；单独空段或把标记移到无关正文也不符合溯源契约。需要表达“这个完整标题就是此来源单元”，而不是放宽标题／空段门禁。

## Scope

- Markdown 新增严格的原生 Div.source-unit 元数据容器：无容器 ID、仅 source-unit 类和数字 data-unit 属性，内容恰为一个 Header。标题保自己的原 ID、文字、级别和原注。
- 真实 reader JSON 保容器用于溯源核对；导出 HTML／EPUB 及其他 writer 时只展开原 Header，不输出元数据容器或空段。既有 Span 用法、来源顺序／处置和标题内标记禁止均不变。
- 只修 Lua 语义、EPUB 的 JSON→writer 边界、定点测试和当前契约，不新增自动标题推断、来源变更或布局效果。

## Acceptance Criteria

1. 有效纯标题容器保同一原生 Header 与所有 ID／链接，导出不含 source-unit 包装或空段。
2. 错误单位号、普通正文／多标题、容器 ID 及额外样式／属性不得借此被静默丢掉。
3. 既有 Span 仍原样支持，标题内 Span 仍拒绝；真实 builder 保持目录／分片／原页导航与内容顺序。
4. 真实纯标题单元实际消费通过，定点回归、一次独立 review、AutoCorrect／文档／diff 检查通过。

## Result

已新增严格 Lua 容器校验，并在 EPUB 的 JSON→writer 边界只展开相同 Header，来源审计 AST 保留元数据。真实 writer 最初发现仅在 Lua 导出阶段展开不够：JSON 中间路线会把下一 H2 误升目录根；此失败由边界展开修复，不放宽目录门禁。

定点验证按变化分段执行：10 项 Lua／旧 Span 检查、1 项真实新 writer、5 项 JSON 边界反例和 1 项既有 caption／alias writer 均通过；文档示例经真实 Pandoc 解析、AutoCorrect、文档长度和 diff 检查通过。真实消费者的纯标题单元及中英文目标重建通过，中文候选三模式检查通过；书级最终构建另受单书完成条件约束。

## Review

独立 reviewer 审查实际 diff 及范围，无 blocking。另以分部／子章、富 Header 及标题原注／回链、before_target 页点运行真实 writer：来源 JSON 保元数据 Div，目录父子、全部标题／注／页 ID 及链接完整，输出无元数据包装或空段。共享结果不替代全书内容验收。
