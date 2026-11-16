# 图形语义边界：编号图检查与原生页点

## Status

implemented

## Problem

展示公式后普通正文可能以图号 Link 起句。旧编号图检查把 Math 图像及正文 Link 当编号图／图注，误要求公式有图号和 fig- ID，并将相邻数学图卷入真实插图组；真正的编号图仍须执行原门禁。

## Scope

只修 reader AST 中编号图追溯的对象边界：显式 math-inline／math-display 数学语义不算普通编号图；以图号 Link 起句的普通段落不作为相邻图注。保真实插图的原图号、fig- ID、图片组数量及现有纯文本图注门禁。另对已核定图注末／图像前的原生页点，复用受控 before_target 迁移，允许显式绑定标准 Figure；原空 Span 从被 aria-hidden 的 writer 图注移至 Figure 前，保文字／ID／顺序／链接，无空段。最终页点 QA 拒绝隐藏祖先。无正文变更，无新的 manifest 前提，不修改文本后备 API 或重跑已交付书籍。

## Acceptance Criteria

1. 真实展示公式＋图号正文引用的反例不报假缺图号，不把相邻数学图并入插图组。
2. 数学语义存在于 Image 或原生 Span／Div 时一致；相邻未声明数学语义的普通插图仍受原门禁约束。
3. 真图缺 ID、错误原图号、图号组数量不一致仍失败；正文前置空溯源节点不改变 Link 身份。
4. 显式 Figure 页点迁移保原字符／顺序及已有链接，不接受任意图像或未声明位置；无 before_target 的隐藏页点失败。已有标题迁移契约不变。
5. 现有 Pandoc reader／原生页导航回归与最小正反例通过，真实消费者只复跑受影响模块。

## Result

AST 检查按 Image／Span／Div 的显式数学角色区分公式与普通编号图；图号 Link 起句及其前置空溯源节点不再被当作图注。原生页点复用既有迁移机制，支持显式 Figure 目标并拒绝隐藏祖先；旧 API 与标题迁移保持。

本地回归：reading_edition 与 epub_caption_heading_alias 共 35 passed／12 subtests；native heading migration、native navigation 与 PDF navigation 共 101 passed／33 subtests。真实消费者的图注页点经实际 Pandoc 转换、apply 与 validate，均位于声明 Figure 前且无隐藏祖先，文字、顺序、标签和链接保全；未重跑全书。

## Review

未参与实现的独立 reviewer 核对实际代码、契约与正反例，未发现 blocking 或 non-blocking。
