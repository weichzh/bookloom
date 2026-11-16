# 关闭出版社样式时的宽表基础适配

## Status

implemented

## Problem

关闭出版社样式且用户字号较大时，原生宽表可能超过正文宽度造成整页溢出。全局 anywhere 可将小数逐位拆行，normal 的 CJK 断行又可能将短中文表头和数据项逐字挤碎；相邻数字列还需要随字号缩放的格距。局部横滚与适用分词须一起验证。

## Scope

- 仅在 tools/epub_browser_qa.py 的关闭出版社样式基础规则中，为原生 table 提供 block／max-width: 100%／overflow-x: auto，并在其子树使用 overflow-wrap: normal／word-break: keep-all，禁止短 CJK 词组被逐字挤碎但仍可在正常空白处换行；th／td 使用 0.25em 水平内边距。
- 增加现有壳规则断言及真实浏览器回归，保表头、合并格、caption、链接、原值和完整内容；EPUB 规则明确宽表局部横滚属于阅读器基础适配。
- 不改正式 EPUB、来源数据、出版社样式开启模式、溢出阈值或检查口径；不增加书籍 ID 特例，不以隐藏／裁切掩盖内容。

## Acceptance Criteria

1. 真实候选宽表在关闭样式／32px 下整页溢出归零，表自身可横滚至最后一列，全部格值和表头关系不变，小数值不被逐位拆行，短 CJK 表头／数据项不被逐字挤碎。
2. 通用回归覆盖宽表、普通窄表、caption、colspan／rowspan、ID 和链接；无正文或资源丢失。
3. 默认与深色模式不应用新规则；关闭样式仍实际应用用户字体、字号与媒体基础适配。
4. 既有图片／代码／标点适配和整页溢出拒绝测试继续通过，不放宽检查。
5. 真实消费者使用新规则并完成必要候选复验；独立 review 的 blocking 关闭。

## Result

基础规则为原生 table 局部横滚、overflow-wrap: normal、word-break: keep-all 与 0.25em 格距；短中文项不再逐字挤碎，Latin 在空白处仍自然换行，不拆表、不缩字号或放宽检查。通用壳及真实三模式回归共 2 passed；原脚注链接／返回、合并格、caption、代码及资源检查继续有效。

使用当前公共 write_harness 而非临时注入，对真实候选的中文表及 12 列数字表做三模式局部消费：格值保全，末列可滚动到达，全页无溢出；关样式时作者 stylesheet／inline style 均移除且 serif32 生效，默认／深色不应用新规则。

## Review

独立 reviewer 对照修前快照、真实左右端渲染与回归完成审查，无 blocking；实际表内滚动时文档横移为 0，末列进入视口，Chromium 可访问性树仍暴露 table／caption／行列头／cell／link。

新 fixture 没有完整验证图片资源闭包，不能把内联 SVG 输入及适配标志当作资源库存证明；真实目标页另核图像加载，原资源检查未改。CJK 增量的独立 review 复验 Latin 换行及末格值，无本范围 blocking。
