# LaTeX 原生图注、回退资源与宽表语义

## Status

implemented

## Problem

原生 Figure／Table 的交叉引用已有来源编号，但 Caption 未显示相同编号；epubfallback 只接受 SVG，造成无必要的图片包装。显式宽表重解析又可能把原 table 内的表注提升为相邻块；只接受单一 Table AST 会误拒或诱使移动正文。

Pandoc 3.13 reader 丢掉 caption* 的星号，普通空 short caption 仍编号，figure* 只表示宽浮动，不能据这两者猜测无号。

## Scope

- 新增显式 native_caption_numbers，默认不变。复用来源 counter／标签、setcounter、编号格式、附录与字母后缀，为原生 Caption 加语言化编号；已有同号不重复，异号／异类型拒绝，空图注／caption* 不造号，caption[] 仍编号。
- 新模式只限制影响 figure／table 计数的不可保真顺序，保留原位置的 footnote 等计数；caption 后改变编号格式清楚拒绝，不把新前提加给旧调用。
- 临时 reader 输入经短图注的受控标记保住 caption*，Lua 消费后不进入成品；保护注释及 verbatim／verbatim*／Verbatim／lstlisting／minted／inline verb，不改正式 LaTeX 或 PDF 分支。
- epubfallback 接受 PNG／JPG／JPEG 及既有 SVG，保持 alt、路径及资源闭包，不重编码或向 PDF-only 调用增加 EPUB 依赖。
- epubtablescroll 在 PDF 透明传递，EPUB 复用现有 reader 与 table-scroll Div／Table。原 table／longtable 内的完整表注按原 AST 顺序保留；表外额外正文、多个表及嵌套 wrapper 拒绝，公式、格值、来源点、原注与引用保持。
- 保留原编号图邻段检查条件；来源正文的真实交叉引用用原生 Link 表达，不能仅因 Figure 已有图注就跳过旧图组检查。

## Acceptance Criteria

1. 原生图／表可见编号与 ref 一致；短图注、无图注、caption*、手写同号、字母后缀、附录及旧 API 有正反对照。
2. 栅格及 SVG 回退保资源和显式 alt；动态／越界路径继续拒绝，原 PDF 输入不变。
3. 包装前后展开 Div 的完整 AST 相同，含源表注、原位 footnote counter、Note、caption、公式及链接；表外内容和多表负例仍失败。
4. 实际消费者做受影响模块检查，最终书籍以冻结全量收口；共享检查不增加逐页审图。

## Result

图注、资源及原生宽表入口已接入共享构建器。图注／资源定点回归通过，wrapper 的 19 项非 PDF 模块测试通过；PDF 透明环境和 PDF-only 无回退资源的既有真实对照有效复用。旧独立手工图注及显式图号两项回归通过。测试 helper 的类型错误只修测试调用，未放宽产品条件。

真实来源中 39 个原生表去除 wrapper 后完整 AST 相等，原位表注、脚注与交叉引用保持；实际表格的浏览器检查消费另一个明确的数学适配变更。本变更未声称补齐 Pandoc 尚不支持的任意 LaTeX 环境。

## Review

独立消费撤回了非空 Figure caption 即跳过邻段检查的提案，恢复原测试与原调用条件；原生来源链接按身份恢复，不改自然正文以迁就检查。补充非图表计数、verbatim* 和编号后缀边界反例，并按原源环境而非提升后的块形状保存表注；相关阻塞已关闭。
