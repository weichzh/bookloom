# LaTeX 定义列表中的标签与交叉引用

## Status

implemented

## Problem

原生 description 中带标签的公式不被 collect_labels 遍历，列表外 eqref 报未解析；相同公式移出列表的对照通过。

## Scope

仅补全 collect_labels 对 Pandoc 原生 DefinitionList 的来源顺序遍历：先词项 Inlines，再各定义 Blocks，递归复用原计数与标签逻辑。保持 description 语义、公式原文、已有编号、唯一 ID、未解析引用拒绝及旧列表路径；不增加手写后备链接、书籍特例、依赖或构建前提。

Pandoc 官方结构为词项和定义块列表，见 [DefinitionList 文档](https://pandoc.org/lua-filters.html#type-definitionlist)。现有第二阶段原生 walk 负责转换，不另造 walker。

## Acceptance Criteria

1. 原生 description 内带标签公式的前向／后向引用有效，嵌套定义与列表内外编号按真实来源顺序连续，词项内显式标签可达。
2. 实际 AST 保持 DefinitionList、词项、定义段与公式内容；生成锚点唯一，原编号显示正确。
3. 缺失标签仍失败；无定义列表的旧正常对照不改变。
4. 最小正反例及相关 LaTeX 编号回归通过，真实消费者只复跑受影响模块；独立 review 关闭阻塞。

## Result

已补顺序遍历，公式锚第二阶段同时保原类型处理 Para／Plain，关闭 Plain 定义体引用可生成而锚缺失反例。5 个新回归及 5 个既有 LaTeX 分部／编号／真实 Adapter EPUB 回归通过；无定义列表路径、缺失引用拒绝及原编号保持。

真实消费者的展开输入经实际 Pandoc/Lua 输出保留原生 DefinitionList、唯一公式锚及指向它的引用，reader 通过。这是受影响模块的实际 reader 消费，不冒称整书浏览器或最终成书验收。

## Review

独立核对实际代码及正反例后发现 Plain 原生 AST 反例；复用原锚处理并增加参数化回归，5 个新例全过，blocking 关闭。
