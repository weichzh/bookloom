# LaTeX 阅读对象的原生兼容

## Status

implemented

## Problem

原生 appendix 已穿过 Pandoc reader，但 Lua 未切换计数；富文本算法没有显式 LineBlock 入口；仅来源标记和空 label 的段落输出空 p，制造段距。来源使用 TeX-bbm 双线数字时，渲染前导未加载 bbm，不能用普通粗体或 mathbb 数字冒充。

## Scope

- 在既有 Lua 计数器中消费真实 appendix RawBlock/RawInline：重置章/节状态，以字母章前缀同步真实标题、ref、eqref 与图表编号；不改正式底稿、ID 或其他旧路径。不增加预处理标记。原生 equation/align 的缺省编号按 AMS 行语义修正：包括无 label 行，显式 tag 不递增，notag/star 不自动编号；派生 tag 同步可见号与引用。
- 提供 quote 内首个非打印命令 epubcodeblock 的显式 LaTeX 接口，仅将该块的原生换行、Math、Code 及强调转换成 code-example 内的 LineBlock；原生 quad/qquad 空白保留为等宽空白，普通引文不变。PDF helper 不打印标记，不把算法降为代码字符串。
- 现有富代码核验增加原生 Math 及公式图的类型、完整规范化字面、对象边界和位置；AST、成品 XHTML 与浏览器 DOM 使用同一契约，非数学旧代码不变。复用既有 GladTeX alt 规则，不另建规则副本或跳过核验。
- 仅把全部由来源标记、空标签锚点及普通分隔空白组成的 Para 改为原生 Plain，保每个 ID、页号和顺序；含正文、Math、Code 或其他内容者不动，不清理真实空白段。
- GladTeX 前导补载已有 bbm 包，让明确的 mathbbm 来源符号真实绘制。逐来源恢复示性函数，不全局改写 mathbb 或普通 1，也不使无关缓存失效。

## Acceptance Criteria

1. 真实 reader 的附录字母章节、子节、正文及注释内引用和图表／公式引用一致；RawInline／RawBlock、分部及字面命令对照通过。没有触发原生缺省编号修正的旧路径不变。
2. 显式算法生成原生 LineBlock，Math／Code／强调及行和缩进完整；普通 quote 保持，非法／多余标记明确失败。实际 EPUB 内层 DOM、链接、EPUBCheck 与窄屏适配通过。
3. 来源／空锚点段落不产生空 p，IDs／来源页仍原位；混合正文和真实空白段为反控。
4. 实际生产 bbm SVG 与标准 LaTeX-bbm 原生参考字形一致，普通数字不同；行内与显示通过实际 Adapter，原源码及 alt 保持，新输入缓存可复用。
5. 消费来源绑定模块后再冻结最终包；独立 review 关闭 blocking，相关中文文档及 diff 检查通过。

## Result

共享模块终检 58 个测试、22 个子例通过；没有重跑整书内容验收。真实展开输入证实 appendix 为 RawInline，因此直接修既有状态机。新附录、算法、锚点、类型化数学和嵌套 tag 均有正反控；默认公式及 BBM 字形的实际 SVG 与独立标准 LaTeX/T1 参考自动比较，EPUBCheck 与三模式成员检查通过。无 label／手动 tag／无号行计数另以原生 LaTeX aux 为独立参考。

真实消费者的算法、注释及数学对象经子集 Adapter、EPUBCheck 和三模式检查；另一消费者的示性函数按原生对象恢复，并有独立轮廓反控和受影响模块证据，非 BBM 对象未批量替换。子集通过不作整书冻结。

## Review

独立 review 发现无 label 式计数、section 显式计数和同行 RawInline 算法标记三项 blocking；修后又发现嵌套显式 tag 被正则截断。改用已有 balanced 和原生 AMS 路由后，实际字形、引用及后续计数通过，原 reviewer 确认全部关闭。普通引用保既有单 Str 契约。
