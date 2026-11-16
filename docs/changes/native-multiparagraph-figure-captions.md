# 多段原生图注与短替代文本

## Status

implemented

## Problem

原生图像可能配有完整多段 Caption。短占位说明若被用作隐式 figcaption，完整图注会留为游离正文；把所有段落塞进 alt 又会丢分段并制造冗长重复。

## Scope

复用 [Pandoc Figure／Caption API](https://pandoc.org/lua-filters.html#type-figure) 和现有 fenced Div／Span，不新增转换平台。单段图注优先使用既有图片说明加独立 alt 属性；多段用 native-figure Div 包住图像体及唯一的末尾 figure-caption Div，在现有 Lua filter 映射为原生 Figure／Caption。图像以 source-image Span 避免隐式嵌套 Figure，完整图注的分段、强调、链接与元数据保持。其他 Div、旧隐式图、source-unit 行为不变。

## Acceptance Criteria

1. 实际 reader AST、HTML 与 EPUB 均为唯一 Figure，短 alt 与完整多段 figcaption 分离，图注不游离或重复。
2. 原 ID／文字／强调／链接与页点保全，图注可访问且不被错误隐藏；资源和图片不改。
3. 缺失、重复或空图注，以及隐式嵌套 Figure 有明确错误；未选择新语法的旧稿行为不变。
4. 新语法正反例、直接相关旧 Div 回归及真实消费者受影响图组通过；不重跑已交付书籍。

## Result

接入 Pandoc 原生 Figure／Caption；完整 caption Div 的 ID／属性及后代语义保留，单段沿用现有 alt 属性。正式新语法与 pure-title 回归初轮 28 项通过；补齐后代 caption 唯一性后，受影响原生 Figure 模块 14 项通过。

真实消费者的单段及多段图组逐对象检查；多段对象经正式 reader／EPUB writer 保留分段、原 Figure ID、短 alt 及可访问图注，无错误 aria-hidden，未重跑全部页点定位。稳定写法已在 CONTRACTS 与 Lua 注释示例中登记。

## Review

独立核对发现嵌套重复 figure-caption 未拒绝的 blocking；补全后代计数与正文／caption 内嵌反例后，原 reviewer 定点复核并关闭该项。
