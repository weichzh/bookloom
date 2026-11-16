# 目录真实标题读取图片替代文本

## Status

implemented

## Problem

原生标题可能含有承载字符的 Image。正式 reader 和导航已读取图片替代文本，真实 XHTML 标题比较却只读文本节点，因此误报标题不一致。

## Scope

只修 epub_semantics.heading_text：img 返回其替代文本，递归容器和尾文本照旧，标题 noteref 仍排除。不扩展 TeX 白名单、不改源图片、标题或校验阈值；缺失／错误 alt 继续不能匹配实际源标题。

## Acceptance Criteria

1. 原生 Image 的源 AST、纯文本导航与真实 XHTML 图片标题语义一致，支持嵌套容器及尾文本。
2. 缺失／错误 alt 仍失败；正文标题注的原注号、目标及唯一正文检查不变。
3. 运行受影响目录及标题注测试；真实消费者先做原失败对象定点验证，再对修复输入完成最终全量。

## Result

heading_text 仅增加原生 img 的 alt 读取，保原递归、尾文本与 noteref 排除。目录及标题注相关测试 30 项／20 subtests 通过；另一个旧用例因更早的标题注数量门禁改变错误消息而失败，修正精确消息断言后该单项通过，拒绝行为不变。

真实图片标题经正式 AST、Pandoc EPUB writer、导航转换及真实 XHTML 比较通过，移除正文 alt 的负例仍失败；图片及尺寸不变。消费者最终全量通过 EPUBCheck 与三模式检查，未重跑不变公式转换。

## Review

未参与设计／实现的独立 reviewer 核对 helper 与正反例，无 blocking／non-blocking。相邻旧测试只更新精确拒绝消息，没有扩大通过条件。
