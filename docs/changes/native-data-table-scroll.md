# 原生数据宽表的按需局部滚动

## Status

implemented

## Problem

窄屏数据表可能把供应商词语拆行，把 CAD 参数等标识拆成逐字碎片；普通溢出检查却通过。关闭出版社样式已有基础宽表滚动，正式包中的显式数据表也需要适配。

## Scope

- 新增 opt-in 的 table-scroll Div：只包原生宽表，以容器局部横滚、正常空白处换行和 keep-all 保护短中文项及英文／参数字段；不伪装为 Code，不缩字号、删列或转为图片。
- 连字符型号等真正不可拆的连续标识，用原生 table-token Span 显式声明；仅在 table-scroll 内设 nowrap，出版社及关闭样式基础规则均生效。不换原字符，不对整格或供应商词组一律禁断行。
- 不改变未声明普通表、代码对照表或行内文字；使用既有 Pandoc 原生 Div／Table 和浏览器 harness，保单元格、合并格、caption、链接和可访问表格语义。
- 默认、深色、关闭出版社样式三模式覆盖受保护词、普通空白换行、窄屏末列末字符及宽屏；保失败旧样与普通窄表对照。

关闭出版社样式的自有阅读器移除作者 CSS，仅保本项目声明的基础规则，包括显式标识禁拆；该配置不代表任意外部阅读器完全去样式。没有该呈现规则时，原生 Table 与全部文字仍完整可访问。

## Acceptance Criteria

1. 实际 BOM 与设计表的完整单元格身份、数值、链接与 caption 不变，原生 Table 可访问。
2. 窄屏长字段不拆字，正常词间换行仍有效；局部滚动可达末列及完整末字符，页面不横向溢出。
3. 普通表、现有 code-table 及关闭出版社样式基础适配回归通过，未声明书籍输出不受影响。
4. 消费实际修后表格；独立 review 的 blocking 关闭后收口。

## Result

已接入 opt-in 容器及显式连续标识规则；真实数据表去包装／标识后完整 AST 不变，格值、Table ID、Figure、caption 与链接保留。

通用回归 tests/test_epub_native_data_table_scroll.py 取得 2 passed：三模式×390／1920，含原生表／合并格／caption／链接／辅助功能角色，正常空白可换行，末列完整末字符可达，普通表不受影响。正式入口的原生语义与文档测试共 13 项通过。真实候选中受影响表与 XHTML 的三模式检查通过，指定标识均 nowrap，格值／ID／caption 保留；这是局部消费证据。

独立 review 的多列反例发现 keep-all 仍在字面连字符后换行；同一反例确认 hyphens:none 不解决该问题（[CSS Text 3](https://www.w3.org/TR/css-text-3/#hyphenation) 不禁止字面 U+002D 的既有换行点），因此采用原生 token Span，而非修改字符或整格 nowrap。正反夹具在三模式窄屏证明无标识失败、有标识通过，宽屏不强制横滚。

## Review

独立 review 的连字符 blocking 经原 reviewer 定点关闭。关闭样式的反事实验证确认新规则属于自有阅读器配置增量，不冒充原有配置或任意第三方阅读器。
