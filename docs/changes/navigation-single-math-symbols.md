# 数学导航中的确定单符号

## Status

implemented

## Problem

正文数学标题可能含单独的 infty 与 varepsilon；旧封闭映射只含带后缀的 infty 表达式，实际构建因此拒绝单符号。不能为通过构建把正文 Math 改成普通字符。

## Scope

仅在现有唯一导航映射增加两个精确键：反斜杠 infty→∞、反斜杠 varepsilon→ε。保正文原 TeX／Math 图及独立核验；不增加宏展开、OCR、任意表达式或 epsilon 字形等价。旧调用与未知反斜杠公式拒绝不变。

## Acceptance Criteria

1. 两个符号的标准 nav／NCX 可读标签与原生正文 Math 身份分别通过。
2. nav 错符号、正文 alt 换符号及两层同字符串但对象错误仍失败；新键之外的未知宏及复合式仍拒绝。
3. 最小真实 EPUB 及定点正反回归通过，实际消费者增量检查；一次独立 review，不对无关输入进行全量复验。

## Result

唯一映射表已增加两个精确键，7 个定点测试通过：两个符号分别验证可读导航与完整正文 Math，错误 nav／alt／普通文字替代数学对象均拒绝；四个未知宏及复合式仍拒绝。真实 LaTeX Adapter EPUB 的 nav、NCX 与正文原 TeX alt 分别正确；Adapter 使用显式 toc_depth 的原有受控调用，不改变缺省 API 契约。

真实消费者隔离包核对来源 outline 的有序身份与层级；正文仍保数学图片和原 TeX alt。这是结构模块的消费，不宣称全书正文或公式全部核定。

## Review

独立只读 review 核对精确字典、新键外拒绝及旧 raw 标签路径，无 blocking；“相同字面但 Math 变普通文本”两个反例已合入参数测试并通过。
