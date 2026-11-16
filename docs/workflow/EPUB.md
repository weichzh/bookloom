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

- Markdown：Pandoc 读取 Markdown 和共享 Lua filter，默认按二级标题拆分 spine 后生成 EPUB3；可显式配置分片层级，避免章名与正文被无意拆开。
- LaTeX：受限展开输入与图像路径，Pandoc 读取 `latex+raw_tex`，共享 filter 映射项目语义，再生成 EPUB3。
- Typst：实验性 HTML 导出保留语义，中间 HTML 经结构化处理后交给 Pandoc 生成 EPUB3。

三条路径都先写仓库 `.tmp/translator/`，成功后原子替换 manifest 声明的正式输出。不得由单本书绕过共享规范化或 QA。

分片与目录是不同决定：[epub].split_level 控制 XHTML 的阅读单元，toc_depth 控制导航列出的层级；二者都按核定结构配置，不降低真实小节的标题语义。未配置分片层级时保留各 Adapter 旧默认。实际检查章节入口是否与首节正文连续，不能只看目录链接存在。

## 可重排样式

正文不得锁定：

- `font-family`；
- 绝对字号；
- 固定行高；
- 前景色和背景色；
- 阅读器无法覆盖的两端对齐。

作者 CSS 只固定标题层级、段落语义、代码、公式、表格、图注和其他局部结构。优先使用 `em`、`rem`、百分比、`max-width` 和 `height: auto`；避免固定纸面坐标、无必要的 `!important` 和 `word-break: break-all`。外部样式表是共享事实源；内联样式仅用于稳定语义类无法表达的单点需要。

宽表和宽公式的容器可以在自身范围内横向滚动；不得用全页裁切或 `overflow: hidden` 隐藏内容。共享正文以 `text-spacing-trim: space-all` 保持中文全宽标点，避免行末收空与 DOM advance 度量不一致扩大横向滚动区。浏览器报告溢出时先定位具体元素、固有宽度和尺寸链，再决定改底稿结构还是局部容器样式，不通过改正文或放宽阈值绕过。

代码对照表用原生 `code-table` Div 包住 Table，单元格 Code 保原行并局部横滚，首列以 4em 最小内容宽度防止说明逐字竖排，文字仍可换行；保完整 caption、列、合并格及链接。`code-example`／`binding-example` 仍只接收 LineBlock，不能包 Table。

普通程序保持原行与静态 `pre/code` 语义，宽行在 pre 或 sourceCode 外容器内横滚，不因窄屏自动折行，不依赖 JavaScript。浅色和深色 token 相对各自背景的对比度不得低于 4.5:1；关闭出版社样式后代码仍须可读。

原书用颜色区分绑定来源等信息时，保留原生内联 code 与语义 Span；binding-context 表示调用方上下文，binding-macro 表示宏引入绑定，置于 binding-example 中。用红／蓝及单／双下划线共同区分，浅深色均保持对比；普通完整程序仍用 CodeBlock，不用截图替代可搜索代码。代码局部高亮用 code-example 中的 code-change Span，保留原书指明的变化范围，并辅以虚线下划线；斜体中的局部正体用 roman Span。不能因纯 CodeBlock 不支持局部强调就丢弃源页的教学信息。

原书多色对应使用 source-highlight-green／blue／yellow／purple／red／pink／orange 等通道，并在同页使用线型或线宽辅助区分；只有角色确实时才使用 type-subject／context／result／extension 等价类，不能把颜色在另一处的不同含义误标为同一类型角色。分割内联 code 时把分隔空格置于 Code 节点外，多列空格使用 NBSP 防止 Markdown 折叠，逐行核对实际 DOM 的完整代码，不以碎片库存相同代替字符保真。

Pandoc LineBlock 的真实换行由 br 表达，不能用 pre-line 把 writer 为排版插入的源码换行也显示出来。code-example／binding-example 使用等宽字体、nowrap 与容器内横滚，内联 code 保留空白；text 代码块保留推导规则的列对齐，不把同一视觉行的前提拆开。诗歌的显式 verse 语义另行保留。

LaTeX 富文本算法在 quote 内首个位置声明 `\epubcodeblock`，同行或独占行均可；正文原生换行、Math、Code 和强调映射 LineBlock，quad/qquad 保等宽缩进，hfill 以固定空白分隔评论。普通 quote 不变。富代码检查同时核对数学对象的类型、完整规范化 TeX、边界和位置，拒绝删式、普通文本替换、类型或公式改动；不靠节点数量通过。

## 公式图像

共享默认公式表示为包内路径 SVG，不改变 manifest 的输出文件名。来源 EPUB 或其他正式来源已经提供且实际阅读不受影响的公式 PNG、JPEG 或 SVG 可以保留原始图像格式，不为统一扩展名强制重制；这类来源公式仍须保持正确顺序、尺寸、替代文本、资源闭包，并通过浅色、深色和窄视口浏览器验收。

对正式底稿中的 TeX 或 Pandoc Math 公式：

- Pandoc 先生成保留 `Math` 节点的 JSON AST。
- 固定版本 GladTeX、LaTeX 和 dvisvgm 在仓库 `.tmp/` 中生成路径 SVG；书籍级 `gladtex.cache` 和已核定 SVG 保存在被忽略的 `Works/<书名>/.cache/svg-math/`。
- 首次缓存为空时，可从现存正式 EPUB 预热与当前 TeX 和显示类型无歧义匹配的 SVG；后续只编译缓存未命中公式。正文、导航和 EPUB 包仍完整重建。
- 生成公式的本地 defs／use 字形在规范化时展开为原始 path 与独立 transform 分组，不把平移烘焙进路径坐标；既有主题缓存同样迁移，保持字形、绘制顺序和尺寸，不因表示变化重编译。无法保真解析的生成字形拒绝发布，最终 QA 拒绝生成公式残留 use；保留引用的来源 SVG 必须与正式底稿声明的 Image 及完整 SVG 身份一致，不从主题或 class 猜测来源，来源自带 SVG 不进入该转换。
- 公式子进程失败时，可以在重新抛出原错误前检查点已经生成的有效缓存项；只接收当前 GladTeX 缓存版本、受限的 `eqnNNN.svg` 路径、有限数值尺寸和通过 SVG 结构校验的资源。失败检查点不能生成或覆盖正式 EPUB，也不能把损坏条目标为命中。
- 行内和展示公式分别使用 `math-inline`、`math-display`；前导支持已有 bbm 包，来源确为 TeX-bbm 的示性函数/单位矩阵用 `\mathbbm`，不全局替换普通数字、向量或其他字体。
- 来源 EPUB 的栅格行内公式可以读取来源包中静态、无冲突的 `.math-inline-*` CSS 尺寸；构建时把该尺寸写入最终 XHTML 的 `width`、`height` 属性，并以 `em` 高度和 `width: auto` 的内联样式随正文缩放。来源厂商媒体查询只作为输入证据，成品不依赖它们；CSS 缺失、冲突或无法安全解析时保留原尺寸并进入人工复核。
- 由 TeX 或 Pandoc Math 生成的公式 `img` 必须有非空、规范化 TeX `alt`；来源自带公式图像没有可靠 TeX 事实时，不猜测或反向识别公式，只保留非空且可追溯的替代文本。
- LaTeX 来源图像的 `alt` 为空或仅为 `image`／`figure` 时，共享 filter 可以用文件名生成可追溯的 `图像：...` 回退文本；显式替代文本不得被覆盖，也不能把该回退当作公式语义。
- 每个 SVG 必须有四值 `viewBox`，不依赖阅读器字体；多号 align／align*／aligned 按原生各行保显式号和无号行，渲染版本仅使对应旧 SVG 缓存失效，不合并为末号。
- 外部引用的 SVG 不继承宿主 XHTML CSS，因此 SVG 自身包含默认浅色和 `prefers-color-scheme: dark` 样式，宿主保深色衬底。原生栅格公式沿用白底，保原像素和字色；不整体反色或重编码来源公式，行内、展示及题注内对象均纳入资源、尺寸和对比检查；失败或无法自动裁决时才定点查看。
- 行内公式按各自 SVG 的原始高度以 `0.06em/px` 换算，展示公式以 `0.10em/px` 换算，并同步换算基线偏移；两者随阅读器正文缩放且保留公式之间的固有比例；声明高度用于内容盒，衬底内边距另计并从最大可用宽度扣除，展示公式仍不得被压扁。
- 生成展示公式在 .display-formula 内按声明 em 尺寸和原比例显示，容器限正文宽并局部横滚，不压小长式；行内、未包装来源公式图和普通图沿用拟合，不撑破整页。
- 最终 XHTML 和 OPF 不得残留 MathML、TeX annotation 或 `mathml` 属性。

SVG 加 TeX 替代文本优先解决现实阅读器兼容性，不等同于 MathML 的可导航数学语义。不能在完成声明中把两者的辅助技术能力说成相同。不使用远程公式服务、MathJax、JavaScript、`epub:switch` 或 manifest fallback 维护第二套自动切换内容。

## 导航

EPUB 3 Navigation Document 是唯一当前导航事实源：

- manifest 必须恰有一个 `nav` 项；
- nav 必须恰有一个 `epub:type="toc"`；
- toc 层级从正式底稿标题生成，不能维护第二份手写标题列表；
- 每个本地链接和片段目标必须存在；
- landmarks 用于封面、正文、目录等关键位置；
- 阅读版在真实正文起点声明带稳定 ID 的 bodymatter section；可用原生属性或 manifest 的 `[epub].bodymatter` 绑定该 ID，归一化按 OPF spine 与该显式语义派生唯一正文 landmark，不把首个 XHTML 或书名页猜作正文；
- NCX 只允许作为 EPUB 2 兼容层，不能成为当前目录事实源。

只生成一份规范目录文档；书内目录与阅读器导航使用同一文档，不再保留一份静态翻译旧目录。先对照原书核定逻辑章节，再验证正式 reader 的标题树和最终 nav 的父子关系、顺序、文字及落点；链接存在不证明落点正确。目录深度按核定结构配置，不能因固定默认深度静默丢失条目。

新阅读版显式关闭自动 metadata 书名页，封面后不默认再加书名／作者页；需要时在正式底稿维护至多一份 `.titlepage`。封面、版权、献词和分部页分别保留语义，不能把书名或版次当作所有章节的父标题。来源真实章首设计中的重复书名与转换噪声分开裁决。

导航界面可能剥离样式和脚本，因此目录标签本身必须是可理解的文本，不能依赖视觉 CSS 才能辨认。

标题原注保持在正文标题原位，以标准 noteref 指向唯一注文；目录标签只包含标题文字，不包含该注号。受控写出从源 AST 核定并同步清理 nav／NCX，普通数字和上下标保留；不能为修目录而搬走标题注，或靠按数字删字隐藏错误。目录与真实标题比较纳入原生图片的替代文本，保留其前后文字及嵌套语义；缺失或错误 alt 不能与来源标题匹配。标题 noteref 子树仍排除，不因读取图片替代文本而把注号并入目录。

### 来源页导航

新 PDF 阅读版在 manifest 显式启用来源页导航，逐页声明已经核定的位置；旧书未声明时保持原行为。共享归一化在删除 source-page 前派生空的 pagebreak Span、唯一 page-list 和 OPF 分页来源元数据，不插入分页或可见页码。page-list 是原版页定位，不是第二份章节目录。

- 印刷页码取 page-map；没有印刷页码时明确显示“PDF N”，不冒充纸书页码。
- 普通精确 marker 可以原位转换。迁移时保留原稳定 ID，精确移除旧空 marker 并接回其尾文本；跨 XHTML 的既有本地链接随已核定迁移重定位，fragment 身份和编码不变。代码 lead 上的来源集合不能直接作为页界，须在 sidecar 指定实际代码块、原行号和完整行字面量；同一程序不拆块，字符、空白和缩进不变。
- 纳入页必须全部且只声明一次；缺页、虚假页、原行不符或位置漂移均阻断。整页图表为保连续段落后置时，以 before-target、after_page 和理由显式绑定真实 Figure／Table；page-list 保原页序，DOM 只允许声明派生的转置，其他页不放宽顺序。锚必须为同 XHTML 中更后且未转置的纳入页，重复目标、额外逆序及错相邻目标均拒绝。数据字段见 CONTRACTS。
- OPF 的 dc:source 绑定来源身份，source-of 的 pagination refinement 标明分页来源。
- EPUB 已有可靠原生页点时，显式启用 native-pagebreaks；在正式底稿中保留纳入页点的标准空 Span、原 ID、原标签与真实位置，以完整 locations 清单派生唯一 page-list。罗马数字、非连续标签不转成 PDF 页码；source-unit 仍只表逻辑来源单元。
- 原生章首点在 writer 分片后可能落于上一 XHTML 尾部；正式底稿仍把点放在标题外，以可选 before_target 显式绑定核定标题／首标题 section。来源图注末、图像前的页点也可显式绑定原生 Figure，避免 writer 把空点留在隐藏图注内；不接受任意图像或段落为目标。完整 Table／table-leading section 或 div 表组可用稳定 ID 绑定；用原生 fenced Div 承载真实表组，页点暂寄既有非空邻接段，不能把长 Span 插入管道表改变列宽，也不从代码或表内搬点。共享层恢复原 ID 与位置、重定向旧链接并做最终紧邻 QA。未声明的点不移动；不能把点写进标题让 writer 克隆入 nav，也不能删除所有 nav 页点掩盖孤立点。
- 原标题确有连续多个页点时，可把来源顺序中连续的点显式绑定同一 before_target；最终须按原序形成标题前的相邻空白链，不允许其他节点、未声明点或正文文字插入，最后一点紧邻标题。来源在无正文容器集中点于表组前时，也保原序共用完整表组边界，不推断为各表行或不同物理页界；它不授权把相邻正文页一起搬到章首。
- 阅读版排除重复题名或重建旧目录时，在 source-units／STATUS 记录源页点的保留与排除理由，sidecar 只列纳入页点；不为凑来源总数向无关正文搬页点。原生页点的 class 不触发溯源清理或 display:none，保留可定位的原位盒与可访问语义；普通非标准溯源标记仍移除／隐藏。
- 原生模式对全部纳入页点的 ID、标签、顺序、空 Span 结构、内容 spine 归属、page-list 与 OPF 来源做最终 QA，并拒绝页点自身或祖先带 hidden／aria-hidden="true"。实际位置另对照原来源核定，并在浏览器检查代表链接的真实落点。缺点、多点、标签错误及 nav／非正文 spine 中的孤立点阻断。
- 没有可靠原生页点的 EPUB 用 not-applicable 及理由记录，不从 source-unit 虚构页码。

## 脚注

通用结构使用：

- 正文链接 `a epub:type="noteref"`；
- 注文容器 `aside epub:type="footnote"`；
- 有效且唯一的 ID；
- 普通超链接可到达注释，并能返回正文。

正式底稿的脚注 preflight 必须按章节联合枚举 Markdown 引用、HTML 数字上标、Markdown 定义和注释区普通编号段；四类对象都已裁决且一一映射后，才能冻结终态计数。Pandoc 零 warning、引用／定义数量相等或成品链接闭合都不能证明没有漏掉整章 HTML 上标或普通编号注释。每个恢复项还要绑定正文原页、定义原页和行内语义锚点；最近的 `source-page` 与当前行号只用于定位，不能单独裁决归属。

原书采用集中尾注时，正式 Markdown 在原 Notes 位置使用带稳定 ID 的 `.footnote` Div，正文链接显式声明 `epub:type="noteref"`；共享规范化将 Div 原位转换为标准 aside，不复制或移动注文。引用和回链可以跨 XHTML，静态 QA 按完整的包内文件与片段核对双向身份，浏览器按真实跨文档加载往返；相同 fragment 文字不能代替同一目标。普通原生脚注的既有用法不变。

阅读器可以把标准脚注显示成弹窗，但内容不能依赖弹窗才可访问。不得把注释正文塞入图片 `alt`，也不得把多看、掌阅等私有属性放进通用成品。

## 图片与字体

图片：

- OPF 声明、实际文件格式和 XHTML 引用必须一致；
- 信息图、图表和公式要有有意义的替代文本或正文说明；图内文字按 TRANSLATION 翻译，保留原图不免除信息性文字的翻译。
- 原书有图号／图题时保留原生 figure／figcaption；alt 提供替代说明，不是第二份可见图注，二者相同也保留唯一可见 figcaption。
- 原书无图注时，底稿用空 `[]{.image-tail}` 显式抑制 writer 自动图注，构建后移除。静态 QA 阻断 figcaption 与邻近可见手工图注重复；图内烘焙文字的重复疑点由来源对象和定点原图裁决，不从 alt 或像素猜测；
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
- 唯一 nav／toc、完整标题树的顺序／父子／层级／标签／ID／真实标题落点，以及落点在 OPF spine 与 DOM 中的阅读顺序；
- 已声明的标准页导航及分页来源元数据；普通／富代码全文、缩进、Code 内字面量及高亮范围，代码外教学颜色和 roman；
- 全部正文 XHTML 可解析；
- 封面和正文资源引用；
- 标准脚注结构；
- 公式类、非空 `alt`、OPF 图像资源；SVG 公式还须检查 `viewBox` 和主题样式；
- 全部非公式图片的数量、媒体类型和替代文本；空值、通用占位值以及会被屏幕阅读器照读的 LaTeX 控制符必须进入失败或人工裁决清单；
- 残留 MathML、TeX annotation、空导航节点和无效正文结构；
- 项目固定的官方 EPUBCheck。

资源闭包按 CSS `url(...)`、引用和实际媒体类型建立自动断言；当前工具未覆盖的能力须明确登记并补适用测试，不能用例行人工遍历图片代替。检查项包括：

- 每个 CSS URL 可解析；
- 实际字体或图像格式与 OPF MIME 一致；
- 没有未使用的大型资源；
- 空 `alt` 有明确的装饰对象身份；歧义才定点回源。

如果来源是非扫描 PDF 且 manifest 启用了 `[source.images].mode = "embedded"`，`check` 还必须证明正式底稿中的每个栅格图像 SHA-256 都属于来源 PDF 的原始图像对象；这项闭包检查先于构建和 EPUB 验收。

自动 QA 还必须从 nav 写出 `outline.txt`，逐行保存 toc 深度、可见标题和 href，供来源标题映射与真实章节编号／父子边界的自动对照，异常才人工裁决。它不根据文字或编号猜测标题层级。

构建归一化会删除源页标记删除后中文字符之间的可见空格；自动 QA 同时阻断两类已知结构退化：源页标记导致的列表项目落入普通段落，以及同一图片的 `<figcaption>` 与邻近手工图注重复。原图内文字无法由普通 XML 检查可靠判断；来源对象、文字层或适用图像检查出现冲突时，只对相关原图／资产／成品复核，并将可推广的失败条件加入回归。

自动 QA 的单书缺口进入 `STATUS.md`；需要改变共享行为时建立 change，不能静默当作已通过。

首次可构建候选执行全量自动基线；迭代使用 `refresh --lang ... --target epub --member ...` 对受影响 XHTML 执行浏览器模块检查，同一次选择保三模式。包仍完整重建、静态 QA 仍检查全包，当前并非所有检查均已增量化；结果须明确已执行与未覆盖成员。改共享 CSS、字体、转换器或检查器时按消费者扩大范围。终态候选只运行一次全量 `finalize`；成功的 `final.json` 绑定全部正式输出哈希及对应 QA 目录，并额外绑定忽略 `work.status` 的构建配置摘要。人工把 STATUS 写为本地完成后由 `complete` 原子收口 manifest；该状态迁移不使已有成品证据失效，且写后输出竞态会触发安全回滚。外部交付使用 `deliver`，只在来源、manifest 构建配置和唯一正式 EPUB 仍与该证据一致时，核验 staging 副本后原子替换明确目标文件；每个语言／目标分别保留成功回执，失败尝试只更新独立 attempt 记录，不删除旧成功事实。

## 浏览器渲染验收

通用 EPUB 不绑定 Calibre。最终哈希对应的正式 EPUB 使用 `uv run python tools/translator.py browser-qa 'Works/<书名>' --activity visual-qa` 解包到仓库 `.tmp/`，并由隔离的无头 `agent-browser` 执行 XHTML、CSS 和 SVG。首次基线和最终候选全量运行；中间可用可重复的 --member 选择明确包内 XHTML，下列模块以自动断言覆盖：

- 书架元数据、封面和书名页；
- toc、landmarks、章节跳转和返回；
- 正文、标题、列表、表格、图片和图注；
- 行内与展示公式；
- 代码浅色和深色主题；
- 脚注与反向链接；
- 出版社样式关闭、用户字体覆盖与 200% 字号；
- 浅色、深色和窄视口。

不常规把截图交模型审查；用 DOM、布局和资源断言确认资源加载、导航目标、脚注跳转、视口宽度和横向溢出。脚注对全部引用、目标和返回链接做 DOM 闭合检查；实际点击按每个 XHTML 的首项、中项和末项抽样，避免 Chromium 在同一文档连续百次导航后限流并制造假失败。跨文档加载先应用读者模式、等待字体布局，再按同一真实 fragment 定位并验证目标与返回引用可见；同文档 hash 不等待 load，显式 fragment 不被代表截图的自动居中覆盖。空页锚的亚像素取整通过实际滚动坐标纠正，不放宽可见阈值。结果分别记录全量目标检查数和实际点击数。溢出失败证据至少记录 offending selector、元素边界、`clientWidth`、`scrollWidth` 和短文本，避免另开一次不可复现的 DOM 诊断。通用验收以浏览器实际执行包内 XHTML、CSS 和 SVG 为准；只有 Kindle、多看或其他渠道专版依赖私有行为时，才追加对应真实阅读器检查。

正式 runner 默认逐一执行三种读者模式：窄屏默认、1280×900 深色配色、390×844 关闭出版社样式并覆盖 serif 字体与 32px 字号。每模式用壳页面在同一 iframe 内串行加载本次选定 XHTML，保存 `run.json`、`progress.json`、`results.json` 与摘要；默认只为具体失败成员保存诊断截图，设计／定点诊断需要代表图时显式使用 --screenshot-policy representative。总结果记录每模式设置、检查数、失败、完整／已选／未选范围与共同 EPUB 哈希；局部结果不作为最终全量回执。运行使用项目稳定 namespace 和单次唯一 session，只关闭自己的 session；禁止 `close --all`、按进程名终止或清理其他项目。诊断参数和当前默认值以 `browser-qa --help` 为准。

关闭出版社样式不等于完全无样式：该模式移除包内作者样式与内联样式，保留阅读器的文字换行、用户字体／字号、图片等比缩放和代码块局部滚动基础适配。中文标点使用 `text-spacing-trim: space-all` 保持全宽排版，避免默认行末收空与 DOM advance 度量不一致；必须核对实际计算值与生效标记，不修改书稿或放宽溢出阈值。普通 img、SVG 与未包装来源公式图受 max-width: 100%、height: auto 约束；生成展示式在限宽容器内按相对尺寸局部横滚，由 mathDisplayScrollingApplied 单独核验，mediaFittingApplied 继续核其他普通媒体，显式 table-scroll 中的表内数学图按独立适配核验；数学图像仅保已经核定的正有限 em 宽／高与 em 基线，在唯一读者覆盖样式中随用户字号等比缩放，避免高 DPI 栅格图恢复自然像素而异常变大；不恢复其他作者 CSS，没有明确相对尺寸的对象沿用原行为；pre 与原生富代码容器受 max-width: 100%、overflow-x: auto 约束，富代码行保留原换行与空白；代码及其缩进保持等宽字体，正文仍采用用户字体，代码无需强制断行且全部可访问。原生 table 在自身范围内横滚，普通表内用 normal 溢出换行／keep-all 分词保短中文项连续，代码对照表首列及其后代改用 normal 分词保长说明可换行；均保随字号缩放的水平格距，避免中文逐字挤碎、小数逐位拆行或相邻列数字连成一串；保留完整列、合并格、caption 和链接，不把宽表裁切或推成整页溢出。带双括号的原生十进制有序列表通过读者基础规则保留编号及括号，不改变列表项目或起号。DOM 确认适配实际生效，资源、链接、内容与全页溢出检查不豁免。

短标签、参数标识和数值需保完整的原生数据宽表，可显式包在 `.table-scroll` Div 中：出版社样式在容器内横滚，正常词间仍可换行，不拆开英文词或短中文项。原有连字符等需整体显示的连续标识可用 `.table-token` 原生 Span，保原字符并在两种样式状态下禁拆；不包整格或普通空白词组。只用于实际宽数据表，不套全书普通说明表；保 Table／caption／合并格／链接和完整格值，窄屏核末列及末字符真实可达。关闭出版社样式仍复用阅读器原生表基础适配。仅在该显式容器的 table 内，数学图像取消 max-width 压缩并保原相对尺寸／比例，由表或容器局部横滚；未声明表与普通媒体不变。结果以 tableMathImageCount 声明实际适用数，关闭出版社样式时 tableMathScrollingApplied 必须生效；窄屏检查比例及两端可达，不用无整页溢出代替可读性。

Windows 上首次命令启动持久 daemon 时，stdout/stderr 管道可能被 daemon 继承而阻塞调用方；项目入口因此不捕获首次启动命令，后续结构化命令才捕获输出。验收壳必须等顶层 `load` 后再开始 iframe 扫描，避免把全书扫描算进单次 `open` 的 30 秒 IPC 等待。`set viewport` 的已知 EOF 只有在随后读取的实际尺寸正确时才可记录为兼容事件。PATH 外的完整 npm 安装通过 `TRANSLATOR_AGENT_BROWSER` 指向原生可执行文件；不得为此硬编码用户目录。

本地 XHTML 的 iframe 扫描须具有文件同源访问。runner 保留既有浏览器参数并显式加入 allow-file-access-from-files；不能以跳过 iframe 或空文档计数替代检查。Windows 由项目 runtime 用 CREATE_NO_WINDOW 启动隔离 profile 的 headless Chromium，再以标准 agent-browser CDP 附加；仅隐藏 CLI 不足以约束 daemon 启动的 Chrome。可执行文件由现有安装或进程级配置解析，不硬编码用户目录。

关闭只针对本 session 的 daemon 和自有 Chrome 句柄，回收本次 profile；启动失败与超时也走同一路径。关闭失败记录 cleanup_required，不以全局清理补救。改动启动链后，用 `tools/windows_window_monitor.py` 监听 Windows 窗口创建／显示事件并关联进程树，实际验证冷启动、操作、截图和关闭没有终端短闪；启动参数或截图抽样不能替代这项观察。

需要重现终端短闪时，从隐藏父进程执行诊断包装：`uv run python tools/windows_window_monitor.py --out .tmp/window-events.json -- uv run python tools/translator.py browser-qa WORK --activity visual-qa`。证据只写仓库 .tmp；两个 hook 必须成功，否则不运行子命令并明确失败。摘要分别列出命令进程树、其他进程与未归属终端事件，不能把监听失败或 PID 复用误当成无弹窗。

EPUBCheck 证明规范结构，不证明浏览器正确显示；浏览器检查也不证明所有品牌阅读器像素级一致。官方源码以 Git submodule 固定，运行文件由 `uv run python scripts/setup_epubcheck.py` 下载同版本发行包并验证 SHA-256，不进入父仓库。最后一次修复后必须重建，并对新哈希重跑受影响的全部检查。

## 完成与证据

只有以下项目都完成，才能称 EPUB 交付通过：

1. 正式底稿已经按来源完成语义核定。
2. manifest 声明的 EPUB 重新构建成功。
3. 项目 QA 和 EPUBCheck 无阻塞项。
4. 包资源、导航、公式、脚注和链接闭合。
5. `agent-browser` 完整自动矩阵通过，`outline.txt` 与来源标题映射已核对，触发的异常已裁决。
6. `final.json` 对应最终候选哈希，`STATUS.md` 记录内容核定、自动覆盖、异常裁决与证据等级。
7. 交付副本与正式输出哈希一致。
8. 临时解包、样本和截图已清理；被忽略的书籍级公式增量缓存可以保留。

final、completion 和交付回执保存在 `.local/translator/evidence/<work-key>/`，只含路径、大小、哈希、模式和检查统计；不保存正文、截图或完整 DOM 诊断。`clean` 删除临时大文件后仍能复核终态和交付，重建仍失效旧 final；它不能恢复此前已删失的历史证据。

对历史缺陷建立正向恢复账本时，定位键应组合来源页、规范内容和邻近语义锚点；当前文件行号或 AST 序号只能作辅助。非内容结构调整后必须重绑并复核歧义项，不能因候选数量相等就宣称恢复完成。

历史只用于解释原因；当前操作按本文件、`CONTRACTS.md`、匹配的 `ACTIVE` 条目和单书 `STATUS.md`。
