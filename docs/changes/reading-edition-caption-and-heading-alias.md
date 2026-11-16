# 阅读版图注与标题别名修复

## Status

implemented

## Problem

- 构建器把 figcaption 等于 img alt 认作重复，删除唯一可见图号／图题，静态 QA 还要求这一删除。alt 是替代文本，不是第二份可见图注。
- 来源 Div 首块是标题时，Pandoc nav 可指向其外层 section。原页点迁移到标题前后，section 首子节点成为标准空 pagebreak；旧别名门禁要求首子节点直接为标题，误拒相同真实章节落点。下划线 ID 的序列化仍沿用既有 id_ 规则。

## Scope

- 撤销按 alt 相等删除 figcaption 的归一化及配套 QA 条件，保留原生完整图注与其强调、链接和页点；真实 figcaption 与邻近可见手工图注重复仍拒绝。原书没有图注时继续用既有 image-tail 明确表达，不从 alt 猜图注意图，也不读取图片像素猜烘焙 caption。
- 首标题别名仅额外允许真实标题前连续的标准空 pagebreak span；正文字符、普通空 span、容器、错误 heading、非空尾文等不放宽。来源 Div 首块仍必须是同一 Header，真实标签／层级／ID／注引用／父子和 spine 顺序门禁保持。
- 只修改相关公共归一化、QA、定点测试与当前规则；不改变 split、页点迁移、公式、注释或 reader 样式。

## Acceptance Criteria

1. alt 与真实 figcaption 相同的图注仍原生可见，富文本、链接和标准页点完整；真实两个可见图注仍报重复。
2. 原无图注的既有 image-tail 形式不生成图注；不以恢复原生图注为由给所有无图注图发明标题。
3. 真正的来源 Div 首标题及标准空页点前缀可以绑定合法 nav 外层别名；有正文、非标准节点、错误标题／层级／顺序或重复 ID 仍失败；无前缀及 toc-only 旧用法保持。
4. 真实消费者分别验证恢复的原生可见图号／图题和章节别名；浏览器必要代表与 EPUBCheck 通过，局部结果不替代全书验收。
5. 一次独立 review 的 blocking 关闭，触碰中文 Markdown 通过 AutoCorrect，文档和 diff 检查通过。

## Result

已删除按 alt 相等剥除 figcaption 的归一化和错误 QA 条件，保留真实相邻可见图注重复门禁；首标题别名只接受标准空 pagebreak 前缀。定点测试 17 项通过，覆盖富文本图注、image-tail、合法前缀与错误正文／节点／标题／原注身份反例。

真实消费者的原生 figcaption 恢复后，EPUBCheck 零错误／警告，受影响图的三种读者模式检查显示可见图号／图题唯一、无溢出；另一候选通过章节外层别名门禁和完整三模式 QA。旧“alt 相同即应删自动图注”的规则明确撤回，恢复原本要求的内容与原生语义；共享修复通过不代表全部来源内容均已验收。

## Review

独立 delta review 无 blocking。额外真实 Pandoc probe 保留图注中的强调、链接、标准页点和尾文；非空页点字符／尾文反例正确拒绝，image-tail 不生成图注。中文文档 AutoCorrect、文档长度及 diff 检查通过。
