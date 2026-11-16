# 多号对齐公式保留逐行编号

## Status

implemented

## Problem

现有 GladTeX 适配把 align 转为 aligned，删除全部 tag 后只绘制最后一个编号；原 alt 含全部编号仍会假通过。顶层 align、align* 和 aligned 均可能含多个显式行号。

## Scope

- 仅顶层 align／align*／aligned 且有多个显式 tag 的显示公式，使用原生 amsmath align* 按原行保留 tag／tag*／notag／nonumber；不拆原 Math 或改原 AST、alt、公式字面、来源号、目标 ID。
- 使用标准 LaTeX 环境体捕获及盒宽测量，为公式和标签分配紧凑宽度，不用整页宽制造巨大空白。渲染输入沿用已有空行折叠和旧字体别名归一化。
- 单号／无号和其他环境保持原路径。本项不宣称修复其他未命中的数学环境。
- 相关 SVG 写标准 metadata 渲染版本，缓存命中核该版本；仅让受影响旧 SVG 失效，包含从旧 EPUB 预热者。其他公式缓存和其既有调用契约保持。
- 宽式拟合／局部滚动和并发资源策略另作具体研究，不夹带改动。

## Acceptance Criteria

1. 真实多号对象受控渲染，源码／alt 与原绑定不变；数值两行、中间无号、∞／ε、tag*、顶层 aligned 的实际 SVG 均保各行号码和表达式，不以 alt 或 tag 数量单独证明绘制。
2. 构造旧只留末号的反例，并验证实际行坐标／可绘制字形；短式不占整页宽。单号、无号及普通行内是对照。
3. 旧多号缓存及旧 EPUB 预热不能复用坏图；新版命中复用、无关缓存仍命中，失败不覆盖正式输出。
4. 实际受影响消费者按需重建，模块消费后随冻结候选最终检查；独立 review 关闭 blocking，文档及 diff 检查通过。

## Basis

[amsmath 用户指南](https://www.ams.org/arc/tex/amsmath/amsldoc.pdf) 支持逐行显式标签和不编号行；aligned 是从属数学结构，不能用删除标签来替代 align 的编号语义。GladTeX 3.1.0 公共 LaTeXDocument 环境接口可接入标准原生环境。

## Result

生产 filter 已合入原生测宽／逐行绘制与选择性缓存版本。两号样例由整页宽 447pt 收至 76.7pt，各号保原行。真实多号对象和 7 个夹具的生产 SVG 与原型的完整有序字形、路径／规则线和几何一致；规范化 alt 保持既有 GladTeX 行为，非原输入逐字节副本。空行及末行注释的两个失败条件已进入回归。

15 个单元正反控、2 个实际 SVG 回归（12 子例）及真实 EPUB／EPUBCheck 接入通过。新版实际缓存再调用 9 hit／0 miss；旧多号缓存和旧 EPUB 预热均拒命中，无关公式保留命中。上述对象级验证不替代整书验收。

## Review

独立 reviewer 发现 aligned 的合法 [t] 可选定位被画成正文的 blocking。修复仅对该环境剥 [t]／[b]／[c]，align／align* 的方括号正文保留，原 AST／alt 不变；新增正反控。原 reviewer 独立绘制无选项／t／b／c，完整字形、坐标与 viewBox 全相同，确认关闭。
