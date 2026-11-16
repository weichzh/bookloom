# EPUB 原生有序列表的双括号编号

## Status

implemented

## Problem

原生列表的 AST 为 Decimal／TwoParens 时，Pandoc JSON→EPUB writer 仅写出 ol 的数字类型而丢括号，使 `(1)` 变成 `1.`。HTML ol 属性没有独立 delimiter；只补书级 Markdown 或出版社 CSS 不能同时覆盖本项目移除作者样式的读者模式。

## Scope

- 在实际 JSON→EPUB 写出路径增加独立 Lua filter，仅为 Decimal／TwoParens 原生 OrderedList 添加标准语义 Div class，保原起号、项目、嵌套、ID 和内容，不再运行其他 source-unit 语义转换。
- 公共 EPUB CSS 使用标准 @counter-style 及该 class 直接子 li 的 list-style-type；关闭出版社样式时仅由既有读者基础规则恢复同样的编号形式，作者 stylesheet／inline style 仍全部移除。
- 保既有调用入口、缓存和其余列表／格式行为；不新增 manifest 前提、不猜原文编号、不用伪列表或双份可见编号，不加书籍 ID 特例、私有阅读器机制或脚本核心内容。
- 增正式 writer 路径、非 1 起号、嵌套及相邻普通列表、链接和三模式真实回归，同步 EPUB 基础适配规则。

不扩展其他编号类型。第三方阅读器彻底移除作者 CSS 时，HTML 本身不能保证括号外观；验收以标准 EPUB 及明确的本项目 Chromium 读者配置为准，不冒称所有品牌均已验证。

## Acceptance Criteria

1. 真实列表源对象及新 fixture 的括号／数字、起号和顺序正确，原生 ol／li、嵌套、原链接不变；其他列表行为不变。
2. 最终公共 writer／CSS／读者规则真实消费，不使用临时注入；三模式含 serif32 关样式时可见编号及无障碍 ListMarker 正确，实际作者样式全移除，无页面溢出。
3. 正式路径构造的 EPUB 通过 QA／EPUBCheck，受影响既有调用回归通过，不放宽检查。
4. 真实消费者使用新实现，独立 review 无 blocking 后才继续正式终态。

## Result

公共最终 JSON→EPUB writer、独立 Lua filter、标准 counter-style 与读者基础适配已合入；仅 Decimal／TwoParens 列表加语义容器，其余列表和入口契约不变。3 项新回归与 30 项邻近回归通过，另含 6 子测试；真实 writer 样本的非 1 起号、嵌套、普通列表、链接及三模式通过，EPUBCheck 零错误警告。无障碍 ListMarker 保完整编号；直接覆盖 ::marker content 会清空实验 marker，未采用。

真实消费者的受影响列表在默认、深色及 serif32 关样式模式均显示正确括号／数字，AX marker 一致，关样式时作者样式和 inline 均为零，页面无溢出；内容、链接、页点不变。这是对象级消费，不代替全书验收。

## Review

独立 review 核对实际公共 diff、回归渲染及 AX 输出，无 blocking。
