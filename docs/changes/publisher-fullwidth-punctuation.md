# 出版社样式下的中文全宽标点

## Status

implemented

## Problem

窄屏中文标题的行末全角右括号 Range 仍计全宽，但浏览器 normal 行末收空后可能把它排入不足一个全宽的位置，扩大滚动区。更改标题文字只能偶然改变折行，不能解决排版规则。

## Scope

仅在共享 EPUB body 样式声明标准 text-spacing-trim: space-all，与既有关闭出版社样式的阅读器适配一致。不改书稿、字号、检查阈值或其他输出格式；不回头重建已交付书籍。

## Acceptance Criteria

1. 原标题逐字不变，实际计算值为 space-all，窄屏标题与文档不再扩展横向滚动区。
2. 对适用的中文行末闭标点建立回归，正文和代码字面量不变，真正的过宽对象仍被检查发现。
3. EPUBCheck 接受标准属性；真实消费者先局部检查受影响成员，再对新冻结候选执行最终全量。

## Basis

[W3C CSS Text Level 4](https://www.w3.org/TR/css-text-4/#text-spacing-trim-property) 定义 normal 的行末收空及 space-all 的全宽行为。隔离试验只改该属性后，标题及文档滚动宽度回到各自 client 宽度，原标题不变；EPUBCheck 5.3.0 同 CSS 单文档验证为 0 错误／警告。

## Result

仅共享 EPUB body 增加标准属性；正文、字号与溢出阈值不变。相关回归 3 passed／3 subtests，覆盖原长标题、正文及完整代码保全，以及真正 900px 过宽对象仍失败。EPUBCheck 单文档 0 错误／警告。

真实消费者先对受影响成员执行局部三模式检查，再对新冻结候选完成最终三模式全量，失败为 0；EPUBCheck 错误／警告为 0。既有关闭出版社样式规则保持。

## Review

未参与实现的独立 reviewer 核对代码、测试和门禁，无 blocking。
