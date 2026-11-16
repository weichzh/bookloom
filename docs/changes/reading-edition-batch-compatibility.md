# 阅读版批量复核兼容修复

## Status

implemented

## Problem

多个独立共享兼容缺口影响来源身份和阅读结构：带 ID 页标迁移可能产生临时 collision；LaTeX 缺少显式正文入口；嵌套 cnconcept 页标可能丢失；标准跨 XHTML 注释未被 QA 识别。含分部 LaTeX 的独立章层级、原生 EPUB pagebreak、中文机器属性格式化、章节分片、连续页点及标题原注也各有可复现边界。不能通过改正文、抹除链接身份或伪造页码绕过这些缺口。

## Scope

- 稳定 ID 核验兼容 Pandoc EPUB 既有序列化：首字不是字母时加 id_；仅实际 EPUB 调用显式开启，旧 toc-only 调用与原文／ID／链接不改，序列化冲突仍失败。
- 保留原 source-page ID，按原位置声明迁移后移除旧空 marker，字符／空白／尾文本和已有链接身份不变。
- 增加可选 [epub].bodymatter 稳定目标 ID，在各 Adapter 共同规范化 seam 指向明确正文 section／div 并派生唯一地标；未声明时保留原生显式属性与旧调用。
- 在 cnconcept 既有解析 seam 转换嵌套 sourcepage，保持同一概念、强调、文字和精确页界，不移动标记规避。
- 将显式 footnote Div 规范为 aside，原地保留唯一注文；静态和浏览器 QA 按包内 href 核对跨 XHTML noteref／回链，旧本地用法保持可用。
- 在 disable_publisher_css 的 reader 基础规则设置 text-spacing-trim: space-all；出版社模式、书稿、共享书籍 CSS、字号、溢出阈值和代码字面量不改。
- 含分部 LaTeX 按实际分部状态映射独立章与分部下章节，支持原生书末独立章；保旧无分部路径及章节／图表／公式／引用编号，不以伪分部或手写目录绕过。
- 增加显式 native-pagebreaks 模式：用原生稳定页点与标签的完整 locations 清单核定顺序、覆盖、真实落点及分页来源，原地保留 ID／文字／位置／既有 href，派生唯一标准 page-list；不读取或制造 PDF page-map／pdf_page，不改变 source-unit 语义。无 opt-in 与旧四参配置调用保持原条件。
- 增加可选 before_target，把 writer 留在前一分片的章首页点按已核定标题／section ID 恢复；复用稳定 ID 迁移与链接重定向，无声明的两字段原生点仍原位，旧接口不新增前提。同一 before_target 可由来源顺序中连续、显式声明的多个原生点共享，形成标题前的有序空白相邻链，最后一点紧邻标题；其他节点、未声明点、非连续声明、错误顺序和孤立点仍阻断。
- 保护中文 Markdown 格式化中的原生机器属性：AutoCorrect 调整可见正文，不破坏 Header／Span／Div／图像的稳定锚、class 与属性值。保 quiet stdin、代码／公式字面量失败门禁及既有 fix／lint 调用。
- 增加可选 [epub].split_level（1–6），由核定章节结构决定 XHTML 分片；不改标题语义或 toc_depth。未声明时保各 Adapter 原默认值与 LaTeX 直接调用契约。
- 受控阅读版目录从源 AST 排除标准 noteref 的标签内容，正文标题和原注保持原位；仅清理已经与完整原标题匹配的导航标签，并同步 NCX。真实目标标题比较只排除标准 noteref，不按数字、字体或长度猜测；其他标题、链接身份和层级不变。
- 增加反例、必要 Adapter 回归和独立 review；同步当前契约，不改无关共享行为。

## Acceptance Criteria

1. 带 ID 的 before-target 在同／跨 XHTML 中成功，旧目标 href 仍有效，全文／代码及空白不变；冲突 ID、无目标或不实迁移失败。
2. 新 bodymatter 声明核定唯一实际目标；缺失、冲突或不正确地标在构建／QA 失败，旧调用不新增前提。
3. LaTeX 与其他 Adapter 的入口传递有代表性验证；真实候选指向正确正文而非版权页／书名页。
4. cnconcept 内部 sourcepage 进入标准页标；嵌套强调／连续文本不变，无页标的旧概念保持原行为，真实来源位置正确。
5. 集中 Notes 保持原顺序与精确页界，全部注引用／回链有效；跨文件目标错误或回链指错正文失败，浏览器实际往返验证，不以同 fragment ID 冒充同文档落点。
6. CJK 闭标点 advance 误报通过通用 reader 排版解决；最小真实浏览器 fixture 和代码完整性保持，无内容裁切或按书散点软断点。
7. 真实 LaTeX 覆盖前置／分部前独立章、分部及其章节、书末独立章，最终 nav 不新增书名根且父子／标签／落点与来源一致；既有无分部与所有编号、稳定 ID 和引用保留。
8. 原生 EPUB fixture 与全部纳入页点的 ID、标签、顺序、DOM 位置、既有链接及 OPF 分页来源均保持，缺点／多点／错误标签与落点阻断；排除的重复题名／旧目录页须说明，不以无关目标凑来源总数。旧 PDF 页导航和 not-applicable 不新增前提。
9. 原生章首迁移仅在 before_target 显式声明时执行，目标必须唯一且为真实标题／首标题 section，原 ID／标签／属性及正文字符保持，旧链接跟随；最终 QA 核定同文档、同父级且紧邻标题之前。无目标、歧义、代码／非标题或错误顺序／漂移失败；nav／非 spine 孤立点仍失败。
10. 真实 AutoCorrect 与正式 Pandoc 证明中文稳定 ID／属性在 fix 后完整保留，lint 可通过，可见正文仍正常格式化；代码／公式旧保护不退化，异常或空输出不写正式文件。
11. 显式分片层级在真实 Adapter 中生效，章名、首节、正文与注释／交叉链接完整，目录深度与标题树不变；未声明时维持 Markdown 2、Typst 1、LaTeX 含分部 2／无分部 1，非法类型或范围失败。
12. 连续页点章首可按有序相邻链消费，字符／标签／ID／旧链接保持；无声明点、插入正文或中断／漂移的链失败，旧单点门禁不退化。
13. 标题注在源、正文和唯一注文中保持原位，nav 与 NCX 的章名不含注号；目录清理前文字不匹配、普通上标／数字误删、标题错级或错目标仍失败。实际标题标准注引用的数量、注号、显式 ID 与目标须对应源标题，无显式源 ID 的旧调用不新增前提。
14. 独立 review 无 blocking，触碰中文 Markdown 和相关检查通过。

## Result

分页迁移、正文入口、嵌套概念页标、跨文件注释、稳定 ID 序列化与 reader 全宽标点已实现；现有接口保留原条件。共享定点回归与真实 Pandoc／浏览器反例通过。真实消费者的目录、导航、跨文件注释往返和三模式检查用于相应共享 seam 的验证，不以其中任一局部结果代替其他来源的全书内容验收。

分部状态修复有 5 个真实 reader／成熟 Adapter 回归通过。原生 EPUB 页点模式完成代码、拒绝反例及样式定点回归；真实 Markdown Adapter 保持页点 ID／标签／原位字符与链接，EPUBCheck 通过。reserved-class 清理、非连续标签、最终 QA 的 nav／非 spine 孤立点均有反例，旧 PDF 23 测试／33 子测试的有效证据保留。显式章首 before_target 新增 16 项回归及 2 项文本节点反例，真实 Pandoc RawBlock 分片、稳定 ID／字符／旧链接与最终绑定已验证。

机器属性格式化保护经真实 AutoCorrect／Pandoc 验证：Header、Span、Div、Figure 与 CodeBlock ID／class／值及链接保持，可见中文正文仍格式化。三个新增测试和两个旧代码字面量保护测试通过；独立 review 补出的混合 unnumbered 简写与 Unicode 属性名反例也保持身份。

显式 split_level 的 15 项针对性回归通过：首轮 9 项通过，另 6 项因测试夹具位置错误未触及配置门禁，修正后单独复验通过。真实 Markdown 验证默认二级与显式整章分片、原 H2、目录深度、完整注文和交叉链接；其他 Adapter 核对有效 writer 选项及旧 API 默认。

连续同目标页点链新增 7 项测试及 2 项旧单点回归通过；一项最初误把新增 page-list 文字纳入正文不变基线，修正测试范围后单独复验通过。真实 Markdown 分片与同目标点消费及 page-list 实际点击验证了位置和旧链接。标准 noteref 的 nav／NCX 标签清理与真实目标比较有 9 项测试通过；补充源／实际 noteref 数量、标签、显式 ID 与目标一致门禁后，4 个参数反例、无显式 ID 旧调用及真实 writer 反例共 6 项通过。

## Review

独立 review 和相关复验关闭了这些 blocking：CJK 实际计算值门禁、跨文件导航时读者字体／fragment 与空页锚时序、最终 QA 孤立点、标准 pagebreak class 被隐藏、before_target 中非空白尾文本误称紧邻、机器属性前缀漏识别，以及标题注目标／标签漂移。相关反例分别复验；跨文件浏览器反例 10 测试／30 子测试通过。

机器属性保护的明确边界：属性形式的字面表达式即使出现在普通段落，也优先保持数据原值，不在其中插入中英空格，周围正文仍格式化。分片层级和连续页点链经独立 delta review；未重跑条件未变的既有矩阵。
