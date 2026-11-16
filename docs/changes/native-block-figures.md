# 原生块内容图示

## Status

implemented

## Problem

图示的语义域、转移或推理规则可能由原生 Math／段落组成，并有完整图号与图注，但旧 native-figure 仅接受含 Image 的正文，不能将这类来源图示表达为标准 Figure／Caption。

## Scope

复用 Pandoc 已有 Figure(content, caption, attr)，不增加排版平台、图片重画或 OCR。native-figure 缺省继续要求 Image，既有调用、负控和普通 Div 不变。仅显式 data-figure-body="blocks" 启用非空原生块正文，可含 Math、代码、段落、LineBlock、列表、Table 及原生容器。

新模式不接入 RawBlock／RawInline 的跨格式私有内容；正文只有空白或溯源标记时拒绝。唯一末尾 figure-caption、可见图注、禁止嵌套 Figure、source-unit 元数据规则、全部块顺序／字面／ID／链接／页点均保持。未知模式拒绝，不向缺省既有调用增加前提。

不从公式、样式或段落相似性自动推断图号。实际来源图的编号与边界仍由单书核定，普通正文 Math／代码不自动包装成图。CSS 仅在实际消费者出现已定位问题时扩展，不预先重做样式。

## Acceptance Criteria

1. 原有 Image 模式及其成功／失败夹具保持；普通 Div、单段图片图注和 source-unit 不变。
2. 显式 blocks 模式写出真正 Figure／唯一可见 Caption，原 Math、Code 字面及块次序、ID／属性／链接／页点完整；无 Image 亦可用。
3. 空正文、仅源点、无图注、空／重复／嵌套或非末尾图注、嵌套 Figure、未知模式及未支持 Raw 均有负控，不能静默丢内容。
4. 用真实 Pandoc reader／writer 和项目 SVG 公式／代码 seam 检查数学规则图及正常位图图；最小 EPUB 的浏览器只测受影响成员。实际来源对象核定后才应用。
5. 核实际 diff 与一次独立定点 review；只复验 blocking 及必要回归，中文文档通过 AutoCorrect、长度检查，diff 无空白错误。

## Basis

[Pandoc Lua Figure API](https://pandoc.org/lua-filters.html#type-figure) 直接接受 Blocks；旧限制来自本项目 Image 守卫，不是写出器缺少能力。通用正反夹具保存在 tests/test_native_block_figures.py。

## Result

已合入显式 blocks 模式，原生 Figure／Caption 与旧 Image 缺省分开；原块字面和属性保留。真实块图触发的窄屏居中／压缩已用限定选择器修为起始对齐、等比与局部横滚；普通位图对照未变。真实来源夹具 reader／EPUB／EPUBCheck 与三模式成员检查通过；首轮 23 新模式、14 旧模式测试通过，review 修复后 27 个相关正反控及 20 个必要 seam／旧模式通过。正式入口定点 29 项通过、23 未选，复用条件未变的实际浏览器证据；未重建全书。

## Review

独立 reviewer 发现隐藏内层图注可假通过，以及 HorizontalRule-only 原生图体误拒。修复新模式原生隐藏属性／明确内联样式、空元数据例外及 HorizontalRule 后，原 reviewer 用最小夹具只复验两项，均关闭。静态检查不冒称证明任意外部 CSS 的可见性。
