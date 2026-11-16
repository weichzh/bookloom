# LaTeX 显式不列目录的真实标题

## Status

implemented

## Problem

阅读版需要可见、可定位但不列入目录的受控题名页。LaTeX 的 titlepage 只保版式，规范化后没有真实顶级标题；单独关闭自动题名页仍会产生构建器书名目录项。星号章节虽然不编号，Pandoc 仍默认列目录，不能全局隐藏所有星号前言。

## Scope

新增显式元数据命令 epubunlisted{既有标题标签}，只对唯一原生 unnumbered Header 添加标准 unlisted 类。先收集声明，再映射目标，不依赖相邻位置；不存在、重复目标或编号标题失败。未声明的既有标题、编号与目录不变。LaTeX PDF 中该命令为无可见输出的语义声明；不添加阅读器扩展、CSS 隐藏、自动猜测或第二套目录。

输出复用 Pandoc [标准 unnumbered／unlisted 类](https://pandoc.org/MANUAL.html#extension-header_attributes)，正式源码保真实章标题和既有 label；新命令不成为旧调用前提。

## Acceptance Criteria

1. 受控题名在正文中可见且唯一，保作者与合作研究角色等内容；真实 EPUB nav 不列它，也不生成 metadata 书名根，正文地标仍指正文。
2. 原有未声明的星号前言仍列入目录；正常章及小节层级、编号和链接不变。
3. 显式目标必须是唯一不编号 Header，错误目标拒绝；声明前后位置均有效，不能把命令字面留入 EPUB。
4. reader、实际 Adapter EPUB 与相关 LaTeX 回归通过；PDF helper 保原排字。独立 review 后只复核具体阻塞，真实消费者按变更模块检查。

## Result

已实现按显式标签映射原生 Header 的 unlisted 类，保持原内容、ID、编号及其他星号标题。9 个新例通过，覆盖声明前后位置、五种错误目标、真实 Adapter EPUB 和 PDF no-op helper；实际包保唯一可见题名、原有前言／章／小节及正文地标，所有链接可达。测试初稿的文档外声明、分词 JSON、NBSP 与同文件链接解析问题只修测试，不修改生产语义以迁就断言。

真实消费者的前置与首个正文模块确认题名不列目录、不作前言父项，作者与合作研究角色完整，正文地标、原页导航和公式链接有效；只验证受影响模块。

## Review

独立只读 review 无 blocking／non-blocking。对同版过滤器移除新 pass 的对照显示，未声明情况下普通输入与含 part 的 AST 完全一致；声明和 sourcepage 同行语义保留，编号标题仍拒绝。
