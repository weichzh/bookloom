# EPUB 当前基线

## 适用范围

本文件是共享 EPUB 生成、样式、公式、导航、资源和验收的唯一详细当前规则。单书构建完成度和下一步只看对应 `STATUS.md`，活动工作流路由看 `docs/ACTIVE.md`；机器参数和 Adapter 输入输出只看 `docs/codebase/CONTRACTS.md`。

历史提交可以描述当时的 MathML 或旧工具版本，不覆盖本文件。

## 目标

共享 EPUB 必须：

- 从已核定的语义底稿生成，不从 PDF 固定坐标重排；
- 可重排、无远程核心依赖、无运行时脚本；
- 允许阅读器和用户控制正文阅读体验；
- 使用标准导航、脚注和资源声明；
- 由统一 CLI、项目 QA、官方 EPUBCheck 和 `agent-browser` 浏览器渲染共同验收。

Markdown、LaTeX 和 Typst EPUB 使用同一套最终规范化、样式和 QA 门槛。HTML 正式底稿当前只正式输出 HTML。

## 构建路径

- Markdown：Pandoc 读取 Markdown 和共享 Lua filter，按二级标题拆分 spine 后生成 EPUB3，避免单个 XHTML 堆积过多公式和正文。
- LaTeX：受限展开输入与图像路径，Pandoc 读取 `latex+raw_tex`，共享 filter 映射项目语义，再生成 EPUB3。
- Typst：实验性 HTML 导出保留语义，中间 HTML 经结构化处理后交给 Pandoc 生成 EPUB3。

三条路径都先写仓库 `.tmp/translator/`，成功后原子替换 manifest 声明的正式输出。不得由单本书绕过共享规范化或 QA。

## 可重排样式

正文不得锁定：

- `font-family`；
- 绝对字号；
- 固定行高；
- 前景色和背景色；
- 阅读器无法覆盖的两端对齐。

作者 CSS 只固定标题层级、段落语义、代码、公式、表格、图注和其他局部结构。优先使用 `em`、`rem`、百分比、`max-width` 和 `height: auto`；避免固定纸面坐标、无必要的 `!important` 和 `word-break: break-all`。

宽表和宽公式的容器可以在自身范围内横向滚动；不得用全页裁切或 `overflow: hidden` 隐藏内容。浏览器报告溢出时先定位具体元素、固有宽度和尺寸链，再决定改底稿结构还是局部容器样式。

外部样式表是共享事实源。内联样式只用于无法由稳定语义类表达的单点需要。

代码保持静态 `pre/code` 语义，不依赖 JavaScript。浅色和深色 token 相对各自背景的对比度不得低于 4.5:1；关闭出版社样式后代码仍须可读。

## 公式图像

共享默认公式表示为包内路径 SVG，不改变 manifest 的输出文件名。来源 EPUB 或其他正式来源已经提供且实际阅读不受影响的公式 PNG、JPEG 或 SVG 可以保留原始图像格式，不为统一扩展名强制重制；这类来源公式仍须保持正确顺序、尺寸、替代文本、资源闭包，并通过浅色、深色和窄视口浏览器验收。

对正式底稿中的 TeX 或 Pandoc Math 公式：

- Pandoc 先生成保留 `Math` 节点的 JSON AST。
- 固定版本 GladTeX、LaTeX 和 dvisvgm 在仓库 `.tmp/` 中生成路径 SVG；书籍级 `gladtex.cache` 和已核定 SVG 保存在被忽略的 `Works/<书名>/.cache/svg-math/`。
- 首次缓存为空时，可从现存正式 EPUB 预热与当前 TeX 和显示类型无歧义匹配的 SVG；后续只编译缓存未命中公式。正文、导航和 EPUB 包仍完整重建。
- 公式子进程失败时，可以在重新抛出原错误前检查点已经生成的有效缓存项；只接收当前 GladTeX 缓存版本、受限的 `eqnNNN.svg` 路径、有限数值尺寸和通过 SVG 结构校验的资源。失败检查点不能生成或覆盖正式 EPUB，也不能把损坏条目标为命中。
- 行内和展示公式分别使用 `math-inline`、`math-display`。
- 来源 EPUB 的栅格行内公式可以读取来源包中静态、无冲突的 `.math-inline-*` CSS 尺寸；构建时把该尺寸写入最终 XHTML 的 `width`、`height` 属性，并以 `em` 高度和 `width: auto` 的内联样式随正文缩放。来源厂商媒体查询只作为输入证据，成品不依赖它们；CSS 缺失、冲突或无法安全解析时保留原尺寸并进入人工复核。
- 由 TeX 或 Pandoc Math 生成的公式 `img` 必须有非空、规范化 TeX `alt`；来源自带公式图像没有可靠 TeX 事实时，不猜测或反向识别公式，只保留非空且可追溯的替代文本。
- LaTeX 来源图像的 `alt` 为空或仅为 `image`／`figure` 时，共享 filter 可以用文件名生成可追溯的 `图像：...` 回退文本；显式替代文本不得被覆盖，也不能把该回退当作公式语义。
- 每个 SVG 必须有四值 `viewBox`，不依赖阅读器字体。
- 外部引用的 SVG 不继承宿主 XHTML CSS，因此 SVG 自身包含默认浅色和 `prefers-color-scheme: dark` 样式。
- 行内公式按各自 SVG 的原始高度以 `0.06em/px` 换算，展示公式以 `0.10em/px` 换算，并同步换算基线偏移；两者随阅读器正文缩放且保留公式之间的固有比例，展示公式仍不得被压扁。
- 宿主 CSS 用 `max-width: 100%` 约束展示公式，不能让长公式撑破正文。
- 最终 XHTML 和 OPF 不得残留 MathML、TeX annotation 或 `mathml` 属性。

SVG 加 TeX 替代文本优先解决现实阅读器兼容性，不等同于 MathML 的可导航数学语义。不能在完成声明中把两者的辅助技术能力说成相同。

不使用远程公式服务、MathJax、JavaScript、`epub:switch` 或 manifest fallback 维护第二套自动切换内容。

## 导航

EPUB 3 Navigation Document 是唯一当前导航事实源：

- manifest 必须恰有一个 `nav` 项；
- nav 必须恰有一个 `epub:type="toc"`；
- toc 层级从正式底稿标题生成，不能维护第二份手写标题列表；
- 每个本地链接和片段目标必须存在；
- landmarks 用于封面、正文、目录等关键位置；
- NCX 只允许作为 EPUB 2 兼容层，不能成为当前目录事实源。

导航界面可能剥离样式和脚本，因此目录标签本身必须是可理解的文本，不能依赖视觉 CSS 才能辨认。

### 来源页导航

项目正式底稿已经保存 PDF `source-page`，但当前成品规范化会删除空溯源节点，尚未生成标准 page navigation。这是已确认缺口，不得误写成已支持。

后续实现应在删除空节点前派生：

- 正文 `epub:type="pagebreak"` 定位点；
- nav 中的 `page-list`；
- 对应固定版来源的分页来源元数据。

页码节点只提供定位和引用，不强制 EPUB 分页。实现前需要 accepted change、跨格式样本和真实阅读器检查。

## 脚注

通用结构使用：

- 正文链接 `a epub:type="noteref"`；
- 注文容器 `aside epub:type="footnote"`；
- 有效且唯一的 ID；
- 普通超链接可到达注释，并能返回正文。

正式底稿的脚注 preflight 必须按章节联合枚举 Markdown 引用、HTML 数字上标、Markdown 定义和注释区普通编号段；四类对象都已裁决且一一映射后，才能冻结终态计数。Pandoc 零 warning、引用／定义数量相等或成品链接闭合都不能证明没有漏掉整章 HTML 上标或普通编号注释。每个恢复项还要绑定正文原页、定义原页和行内语义锚点；最近的 `source-page` 与当前行号只用于定位，不能单独裁决归属。

阅读器可以把标准脚注显示成弹窗，但内容不能依赖弹窗才可访问。不得把注释正文塞入图片 `alt`，也不得把多看、掌阅等私有属性放进通用成品。

## 图片与字体

图片：

- OPF 声明、实际文件格式和 XHTML 引用必须一致；
- 信息图、图表和公式要有有意义的替代文本或正文说明；
- 原图已经包含原文图注时，可以保留图内图注，不要求在图片下方重复添加译文图注；需要外部图注时只能保留一个语义所有者。构建器会删除仅重复图片 `alt` 的自动 `<figcaption>`；Markdown 图片后的空 `[]{.image-tail}` 仍可用于在底稿阶段显式抑制 Pandoc 图注，构建后会被移除；
- 装饰图可以使用空 `alt`，但必须是有意决定；
- 保留长宽比，不用固定 DPI 代替清晰度判断；
- 图片和图注使用语义结构，避免依赖厂商放大 class。
- 窄视口下，带普通锚点的单图段落也必须受 `max-width` 约束，不能因 Pandoc 在图片前生成的 `<span id>` 使来源表格图撑破正文。

图片验收从最终 XHTML 枚举全部非公式 `img` 和 SVG 引用，不按文件名前缀、扩展名或旧资产清单推断范围；空 `alt` 与 `image` 等通用值都必须逐项裁决。若扫描图片是表格、参考文献、索引或其他唯一信息载体，应恢复为可搜索的原生语义内容，不能用超长替代文本代替。图片位置还须结合图号、唯一正文引用、图注和前后段落核对，不能只依赖最近的 `source-page`。

字体：

- 默认不嵌入中文正文字体；
- 只有标题、特殊字符或明确版式需要时才嵌入；
- 嵌入前核对许可，按实际字符子集化并提供通用 fallback；
- 检查 normal、bold、italic、缺字和目标阅读器；
- OPF 使用当前标准媒体类型。

## 脚本与私有扩展

通用可重排 EPUB 不依赖脚本、远程资源或厂商私有属性提供核心内容、导航、公式和脚注。

确需渠道专版时，使用独立 manifest 输出和单独验收，不让 `duokan-*`、`zy-*` 或其他私有行为污染通用成品。

## 自动 QA

`translator.py qa` 必须至少检查：

- ZIP、`mimetype`、container、OPF、manifest 和 spine；
- nav、toc、链接和片段；
- 全部正文 XHTML 可解析；
- 封面和正文资源引用；
- 标准脚注结构；
- 公式类、非空 `alt`、OPF 图像资源；SVG 公式还须检查 `viewBox` 和主题样式；
- 全部非公式图片的数量、媒体类型和替代文本；空值、通用占位值以及会被屏幕阅读器照读的 LaTeX 控制符必须进入失败或人工裁决清单；
- 残留 MathML、TeX annotation、空导航节点和无效正文结构；
- 项目固定的官方 EPUBCheck。

仅检查 manifest 文件存在不足以证明资源闭包。自动化覆盖 CSS `url(...)` 和文件内容媒体类型之前，含嵌入字体或复杂图片的书还必须人工审计：

- 每个 CSS URL 可解析；
- 实际字体或图像格式与 OPF MIME 一致；
- 没有未使用的大型资源；
- 空 `alt` 确实属于装饰图。

如果来源是非扫描 PDF 且 manifest 启用了 `[source.images].mode = "embedded"`，`check` 还必须证明正式底稿中的每个栅格图像 SHA-256 都属于来源 PDF 的原始图像对象；这项闭包检查先于构建和 EPUB 验收。

自动 QA 还必须从 nav 写出 `outline.txt`，逐行保存 toc 深度、可见标题和 href，供人工核对真实章节编号与父子边界。它不根据文字或编号猜测标题层级。

构建归一化会删除源页标记删除后中文字符之间的可见空格；自动 QA 同时阻断两类已知结构退化：源页标记导致的列表项目落入普通段落，以及同一图片的 `<figcaption>` 与邻近手工图注重复。原图内文字无法由普通 XML 检查可靠判断，仍需在来源页、图片资产和代表性 EPUB 页面之间做一次视觉确认。

自动 QA 的单书缺口进入 `STATUS.md`；需要改变共享行为时建立 change，不能静默当作已通过。

迭代阶段使用目标级 `refresh`，只重建并验收受影响的语言和输出；已变化的 EPUB 仍完整重建、完整静态 QA，并运行浏览器验收。终态候选只运行一次全量 `finalize`；成功的 `final.json` 绑定全部正式输出哈希及对应 QA 目录，并额外绑定忽略 `work.status` 的构建配置摘要。人工把 STATUS 写为本地完成后由 `complete` 原子收口 manifest；该状态迁移不使已有成品证据失效，且写后输出竞态会触发安全回滚。外部交付使用 `deliver`，只在来源、manifest 构建配置和唯一正式 EPUB 仍与该证据一致时，核验 staging 副本后原子替换明确目标文件；每个语言／目标分别保留成功回执，失败尝试只更新独立 attempt 记录，不删除旧成功事实。

## 浏览器渲染验收

通用 EPUB 不绑定 Calibre。最终哈希对应的正式 EPUB 使用 `uv run python tools/translator.py browser-qa 'Works/<书名>' --activity visual-qa` 解包到仓库 `.tmp/`，并由隔离的无头 `agent-browser` 直接执行全部 XHTML、CSS 和 SVG；代表页至少抽查：

- 书架元数据、封面和书名页；
- toc、landmarks、章节跳转和返回；
- 正文、标题、列表、表格、图片和图注；
- 行内与展示公式；
- 代码浅色和深色主题；
- 脚注与反向链接；
- 出版社样式关闭；
- 用户字体覆盖、200% 字号；
- 浅色、深色和窄视口。

检查不能只截图：还要用 DOM 断言确认资源加载、导航目标、脚注跳转、视口宽度和横向溢出。脚注对全部引用、目标和返回链接做 DOM 闭合检查；实际点击按每个 XHTML 的首项、中项和末项抽样，避免 Chromium 在同一文档连续百次导航后限流并制造假失败。结果分别记录全量目标检查数和实际点击数。溢出失败证据至少记录 offending selector、元素边界、`clientWidth`、`scrollWidth` 和短文本，避免另开一次不可复现的 DOM 诊断。通用验收以浏览器实际执行包内 XHTML、CSS 和 SVG 为准；只有 Kindle、多看或其他渠道专版依赖私有行为时，才追加对应真实阅读器检查。

正式 runner 默认逐一执行三种读者模式：窄屏默认、1280×900 深色配色、390×844 关闭出版社样式并覆盖 serif 字体与 32px 字号。每模式用壳页面在同一 iframe 内串行加载全部 XHTML，保存 `run.json`、`progress.json`、`results.json`、摘要和代表页截图；总结果记录每模式设置、检查数、失败与共同 EPUB 哈希。运行使用项目稳定 namespace 和单次唯一 session，只关闭自己的 session；禁止 `close --all`、按进程名终止或清理其他项目。诊断参数和当前默认值以 `browser-qa --help` 为准。

Windows 上首次命令启动持久 daemon 时，stdout/stderr 管道可能被 daemon 继承而阻塞调用方；项目入口因此不捕获首次启动命令，后续结构化命令才捕获输出。验收壳必须等顶层 `load` 后再开始 iframe 扫描，避免把全书扫描算进单次 `open` 的 30 秒 IPC 等待。`set viewport` 的已知 EOF 只有在随后读取的实际尺寸正确时才可记录为兼容事件。PATH 外的完整 npm 安装通过 `TRANSLATOR_AGENT_BROWSER` 指向原生可执行文件；不得为此硬编码用户目录。

`close` 成功后，Windows 上的 `session list` 仍可能保留已经没有进程的陈旧 session 名；反复精确关闭也可能以连接超时结束。验收入口只记录关闭结果和 `cleanup_required`，不得用 `doctor`、`close --all`、全局进程终止或跨 namespace 删除来清理这类工具状态。

EPUBCheck 证明规范结构，不证明浏览器正确显示；浏览器检查也不证明所有品牌阅读器像素级一致。官方源码以 Git submodule 固定，运行文件由 `uv run python scripts/setup_epubcheck.py` 下载同版本发行包并验证 SHA-256，不进入父仓库。最后一次修复后必须重建，并对新哈希重跑受影响的全部检查。

## 完成与证据

只有以下项目都完成，才能称 EPUB 交付通过：

1. 正式底稿已经按来源完成语义核定。
2. manifest 声明的 EPUB 重新构建成功。
3. 项目 QA 和 EPUBCheck 无阻塞项。
4. 包资源、导航、公式、脚注和链接闭合。
5. `agent-browser` 浏览器渲染抽查完成，`outline.txt` 已按实际来源核对。
6. `final.json` 对应最终候选哈希，`STATUS.md` 记录人工结论、证据等级和未覆盖项。
7. 交付副本与正式输出哈希一致。
8. 临时解包、样本和截图已清理；被忽略的书籍级公式增量缓存可以保留。

final、completion 和交付回执保存在 `.local/translator/evidence/<work-key>/`，只含路径、大小、哈希、模式和检查统计；不保存正文、截图或完整 DOM 诊断。`clean` 删除临时大文件后仍能复核终态和交付，重建仍失效旧 final；它不能恢复此前已删失的历史证据。

对历史缺陷建立正向恢复账本时，定位键应组合来源页、规范内容和邻近语义锚点；当前文件行号或 AST 序号只能作辅助。非内容结构调整后必须重绑并复核歧义项，不能因候选数量相等就宣称恢复完成。

历史只用于解释原因；当前操作按本文件、`CONTRACTS.md`、匹配的 `ACTIVE` 条目和单书 `STATUS.md`。
