# 整页插图的显式来源页转置

## Status

implemented

## Problem

整页图表可能插在未完句之间。阅读版把图表置于完整段落之后时，DOM 页点顺序会发生转置；旧检查把任何转置都当错误。不能为满足数字递增而拆句、把图页锚留在无关正文或去掉原页导航。

## Scope

仅扩展 source-pages 的显式 before-target 位置：每个需延后的原页可声明 after_page 与非空 reason，target 必须为真实 Figure 或 Table，after_page 必须是同一内容 XHTML 中更后且未转置的纳入原页。pages 清单仍按原物理页完整有序；从该清单确定性派生 DOM 阅读顺序，多个图页共享锚时保原相对顺序。未声明的书与页仍严格按旧顺序校验。

page-list 继续按原页序列输出、分别指向真实图表及续句，不伪造空页点；实际 DOM 必须精确等于声明转置后的序列，图表目标紧邻关系、原 ID、旧链接、页值及来源元数据继续严格核验。原连续段落、Figure／Table／Caption 原生语义和资源不改变，不使用 CSS 或脚本伪装顺序。

依据：DAISY [Page List FAQ](https://kb.daisy.org/publishing/docs/navigation/pagelist.html) 明确 page-list 无强制排序，电子版内容顺序可以不同于原版；[EPUB 3.3](https://www.w3.org/TR/epub-33/#sec-nav-pagelist) 将其定义为静态页界导航，而非章节目录。

## Acceptance Criteria

1. 新字段缺省时，既有 source-pages、代码行、native-pagebreaks 和无配置旧调用保持旧契约。
2. 单个图页、两个连续图／表页、跨页连续段落保全文／语义；page-list 按来源原序，实际 DOM 按声明转置原序。
3. 未声明转置、错误／重复／排除页锚、转置锚本身再移动、目标不是 Figure／Table、错相邻目标、额外逆序、丢 ID／链接等拒绝；不加全局跳过开关。
4. 真实消费者只检查所列插入对象与受影响成员，既有全量基线有效复用；冻结后再做正式全量。
5. 核对候选与实际 diff、相关新旧测试及一次独立 review 后收口。

## Result

已合入追加缺省字段、确定性 DOM 页序和精确来源页序链接，生产 QA 按页身份映射核验位置。新用例 29 项通过，既有导航测试 101 项及 33 subtests 通过；合跑时一项原生浏览器用例遇自有 profile 清理 WinError 32，单独重跑通过。正式入口新增 29 项测试通过。

真实消费者对来源页点与显式转置做了静态包／生产 QA，以及受影响 XHTML 的三模式成员检查；Table／Figure 相邻、正文续段、强调范围和原索引页链接保留。这是局部消费，不代表全书冻结或交付。

## Review

未参与设计／实现的独立 reviewer 核对当前实现、契约、新增及有效旧回归证据，未发现 blocking。
