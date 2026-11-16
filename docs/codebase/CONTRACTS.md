# 项目契约

## Manifest v2

每本书的 `manifest.toml` 使用以下机器字段：

```toml
schema_version = 2

[work]
id = "example-book"
title = "Example Book"
status = "active"

[source]
path_base = "repository_root"
relative_path = "Books/Example Book.pdf"
sha256 = "..."

[authoring]
format = "typst"

[[languages]]
code = "en"
role = "source"
entry = "en/main.typ"
outputs = { pdf = "output/example-book.en.pdf" }

[[languages]]
code = "zh-CN"
role = "translation"
entry = "zh/main.typ"
glossary = "zh/glossary.tsv"
outputs = { pdf = "output/example-book.zh.pdf" }
```

- `work.status` 只能是 `planned`、`active` 或 `complete`。
- `authoring.format` 只能是 `typst`、`markdown`、`html` 或 `latex`。
- 语言代码唯一，且恰有一个 `role = "source"`。
- 源语言和目标语言只从 `languages` 的 `role` 推导，不设置重复字段。
- 交付目标只从各语言的 `outputs` 推导，不设置重复的 `build.targets`。
- Markdown 需要 PDF 时允许在 `authoring.pdf_engine` 中写 `typst`；其他格式不得设置该字段。
- Markdown 声明 EPUB 输出时必须设置 `[epub].cover`，且封面路径位于书籍目录内并在构建前存在。Typst 或 LaTeX 声明 EPUB 输出时必须设置 `[epub].title`，可选设置 `[epub].author`；语言可用 `epub_title`、`epub_author` 和 `epub_identifier` 覆盖。标识符未设置时稳定生成为 `urn:translator:<work.id>:<language.code>`。
- `[epub].title_page` 可设布尔值控制 Pandoc 自动书名页；缺省保留旧写出器行为，新阅读版显式设 false。`toc_depth` 可设 1--6 的整数，缺省仍为 3；深度须由核定标题树决定，不能用减小深度掩盖错级。
- `[epub].split_level` 可选设 1--6 的整数，控制 XHTML 分片而不改变标题层级或目录深度；例如章为一级、章内小节不列目录时，可设 1 保持整章连续。未声明时保留 Markdown 2、Typst 1、LaTeX 含分部 2／无分部 1；LaTeX 直接 API 的同名可选参数默认 None，保留既有调用。
- LaTeX 可显式设置 `[epub].native_caption_numbers = true`，在非空原生图／表标题前显示与来源计数器和引用一致的编号；缺省为 false，直接 API 同名可选参数也缺省 false。此模式当前支持 `zh-CN` 的“图／表”和 `en` 的“Figure／Table”，其他语言明确报错。
- `[epub].bodymatter` 可选填真实正文 section／div 的稳定 ID，适用于 Markdown、LaTeX 和 Typst EPUB。共享规范化核定唯一目标并派生唯一正文地标；缺失、重复、错误节点或与底稿显式起点冲突会失败，QA 核对语义与地标的实际落点。未声明时保留旧调用和原生显式属性，不增加前提。
- 显式关闭自动书名页时，共享构建按正式 AST、unlisted 和目录深度完整比较 nav 的顺序、父子、深度、标签、ID 与真实标题落点，并按 OPF spine／DOM 核对落点阅读顺序。Markdown 独立 QA 同样执行；旧 toc-only API 保留；自动编号仅在实际 writer 启用时按完整 AST 推导，保留 unnumbered／unlisted 和既有 Lua 内联编号语义。全部 Adapter 的构建核对普通／富代码完整字符、缩进和高亮范围及代码外教学 Span，Code 内 NBSP 不与普通空格混同。受控路径显式启用数学标题双表示：nav／NCX 复用闭合 TeX 可读映射，未知反斜杠公式拒绝；正文另核原数学对象字面及文本位置，禁止相邻文字／公式补回错误 alt。存在或声明的 NCX 与唯一 nav 严格同序、同层级、同完整目标及标签；NCX 仍可选，旧纯 API 缺省不新增前提。
- Pandoc 的首标题外层 Div／section 可作为该标题的目录别名；来源 Div 首块须为同一 Header，实际标题之前只允许连续标准空 pagebreak span，节点／文字／尾文不放宽，完整真实标题及原注身份仍核对。
- 受控目录标签排除标准 Link 的 epub:type=noteref，原注仍在正文标题中。共享层只对源 AST 含此语义的标题核对 writer 完整标签后清理 nav／NCX；普通链接、数字及上下标不删除，非匹配原标签失败。真实标题比较跳过标准 noteref 并保留其后文字；正文标题、注号、ID、href、层级均不改；QA 另核对实际标准标题注的数量、可见注号、显式源 ID 和目标与源一致，无显式源 ID 的旧调用仍可生成 ID。
- 新原生 Note 标题注同样不进入导航标签，正文保原位。调用新路径须提供实际 writer split_level、真实 documents/spine 及 title override 上下文；按 Pandoc 每实际分卷计入普通正文注、不计显式 noteref Link，绑定 fnrefN／fnN、可见 writer 号、完整注文的文字／Math／样式／支持的块结构及正文链接目标。原印注号由单书来源另证，不把 writer 新号冒称原号。支持 Para/Plain、CodeBlock、BlockQuote、BulletList、OrderedList，其他块明确失败；旧无原生 Note API 的条件不变。
- Typst EPUB 和 PDF 来源的 LaTeX EPUB 可从分页入口或来源 PDF 第一页临时生成封面，不在工作目录保存派生图片；其他来源的 LaTeX EPUB 必须显式设置 `[epub].cover`。
- `entry` 和 `glossary` 必须位于书籍目录内；输出必须位于该书的 `output/` 内。
- 所有输出路径必须唯一，不得与 manifest、状态、页码表、入口、术语表或其他输入文件相同。
- 来源必须是仓库内现存的 PDF 或 EPUB 文件，`source.sha256` 必须是 64 位十六进制摘要；正式来源通常位于 `Books/`。PDF 使用 `[pdf].pages` 和 `page-map.tsv`；带连续 `Page_N` 锚点的 EPUB 可使用 `source.pages`、`source.anchor_pages` 和 `source.anchor_set`；没有可靠固定页码的 EPUB 使用 `source.units = "source-units.tsv"`，校验器验证 OPF spine 与逻辑单元表完全一致。
- 非扫描 PDF 中的正式底稿如果直接使用来源图像对象，可设置 `[source.images].mode = "embedded"`。`check` 用 `pdfimages -all` 提取来源对象并按 SHA-256 验证底稿栅格图片闭包；该模式需要 `pdfimages`，裁切、重采样或替换都会阻止工站通过。
- CLI 从 `tools/translator.py` 的真实路径确定仓库根目录，不依赖当前工作目录。
- 所有 containment 检查都在解析 symlink 或 junction 后执行；越出允许目录立即失败。
- Typst 支持 PDF，以及经实验性 HTML 导出再由 Pandoc 生成的 EPUB3；Markdown 支持 HTML、EPUB3 和 Typst PDF；LaTeX 支持 PDF 和 EPUB3；HTML 只支持 HTML。

`manifest.toml` 只保存稳定机器配置和来源事实。来源覆盖、异常裁决和最新验收结果只写入 `STATUS.md`。

## 流程 CLI

- `uv run python tools/translator.py prepare WORK --activity ...` 对中文 Markdown 入口依次运行 AutoCorrect 修复、lint 和 `check`；随后只能使用目标级 `refresh` 或一次性 `finalize`。
- AutoCorrect 对明确入口使用 quiet stdin，避免私有路径被 ignore 跳过或计时信息写入底稿。机器属性形式的列表先用不碰撞占位保护，覆盖原生 ID／class／值、混合 unnumbered 简写与 Unicode 属性名；同形表达式出现在普通段落时也按数据字面量保留，fix／lint 调整其余正文；写回前精确恢复 ID、class 与属性值，占位损坏则失败且不写。写入前仍以正式 reader 比较代码／公式字面量，改变则失败且不写正式文件；lint 不新增 Pandoc 前提。
- `check` 会在存在可识别状态声明时比较 manifest 与 `STATUS.md`；状态只解析冒号后的第一个明确值，不从后续说明中的“完成／验收”推断。状态未知时不推断完成；两者不一致必须先修正，不能继续构建。LaTeX 作品声明 EPUB 输出时，`check` 还会复用正式展开、规范化和 Pandoc/Lua 过滤链做快速预检，但不生成公式或写正式输出。
- 同一作品／会话的工站 lane 由 CLI 互斥；锁文件只在 `.local/translator/state/`，由进程退出自动释放。
- PDF 来源页数、来源页渲染和 QA 统一以 `mutool` 为入口，不依赖 `pdftoppm.cmd` 包装器。
- `doctor` 仅探测所选用途需要的工具；`doctor --for all` 还扫描 `README.md`、`AGENTS.md`、`CONTEXT.md` 和 `docs/**/*.md` 中的本地 Python 入口；不存在或不唯一的路径属于文档入口漂移，必须先修复。
- 共享回归使用项目 uv 环境，迭代优先 pytest 文件／node ID／-k 选择受影响模块，--lf 仅用于已知失败复跑；首次和终态覆盖冻结范围的完整相关套件，不常规重跑仓库无关全量。unittest discover 保留为无插件后备入口。
- 官方 EPUBCheck 源码固定为 `tools/epubcheck` 子模块；运行文件由 `uv run python scripts/setup_epubcheck.py` 校验并安装到子模块忽略的 `target/bookloom/`。主仓库不保存 JAR，缺少子模块或运行文件时 EPUB QA 必须给出准备命令并停止。

## 派生 EPUB 与证据

LaTeX、Typst、Markdown 或 HTML 中声明的正式底稿格式只有一种；EPUB 若未由 manifest 声明为正式目标，只能从已验收底稿派生，不能成为第二份可编辑来源。项目 QA 必须解析全部正文 XHTML，检查包内资源、链接和片段目标，并自动运行项目固定的官方 EPUBCheck；最终哈希对应的包还必须由 `agent-browser` 直接执行解包后的 XHTML、CSS 和 SVG，渠道专版才追加目标阅读器检查。生成和解包证据只留在仓库 `.tmp/`。

共享 EPUB 的可重排样式、导航、脚注、公式、资源和验收契约由 `docs/workflow/EPUB.md` 定义。所有 Adapter 使用同一最终契约，输出文件名只由 manifest 决定；单本书不得另设隐式兼容路径。

共享 SVG 公式缓存位于 `Works/<书名>/.cache/svg-math/<工具链版本>/`，按工作目录和固定工具链版本隔离，不进入版本控制，也不属于 manifest 输出。首次构建可以从现存正式 EPUB 预热可无歧义匹配的公式；后续构建只调用 GladTeX 编译缓存未命中项。公式子进程失败时，允许在抛回原错误前原子检查点当前 GladTeX v2 缓存中路径受限、尺寸有限且 SVG 有效的完整条目；损坏、越界或不完整条目不得进入缓存。正文 AST、导航和 EPUB 包仍完整重建，失败时仍保留旧正式输出。

固定 GladTeX 3.1.0 通过模块局部适配将原生线程池限制为最多 8，较小的库请求不扩大；内部 Popen 复用共享无窗口参数，不改全局 CPU／stdlib、命令、运行时或 20 秒子进程限制。原生行号 SVG 在失败后仅给本次输入已生成且结构有效的条目补当前版本戳，未生成／过期／损坏项不冒充命中；原错误继续抛回。正常成功路径仍要求完整输入，渲染表示及缓存目录契约不变。

正式浏览器验收只选择 manifest 声明的 EPUB；默认全 XHTML，迭代可用可重复的 --member 选择精确包内 XHTML。使用项目 namespace 和单次唯一 session，记录哈希、进度、DOM 及 coverage 的 full／selected、available_members／selected_members／unselected_members／checked_members／unchecked_members；默认 --screenshot-policy failures 仅为具体失败成员截图，representative 须显式选择，--screenshots 保原非负数量上限参数语义（默认 6）。横向溢出失败同时记录 offending selector、元素边界、`clientWidth` 和 `scrollWidth`。命令只关闭自己的 session，不得全局清理 daemon、Chrome 或其他 session；精确参数以 `browser-qa --help` 为准。

## page-map.tsv

必需列保持为：

```text
pdf_page	original_printed_page	section	chapter_id	chapter_title	note
```

`pdf_page` 必须从 1 到来源 PDF 总页数连续且唯一。该表仅适用于 PDF 来源；没有固定页码的 EPUB 使用下述 `source-units.tsv`，不伪造 `pdf_page`。`original_printed_page` 可以为空，也可以使用罗马数字。`section`、标题和备注允许使用书籍工作语言，不作为自动语义判断依据。

映射表可在原列末尾追加 `disposition`：retain（缺列或空值的兼容默认）、merge、rebuild、exclude。后三者必须在 note 写理由；全来源行和原始身份不删除。显式声明后，Markdown 等底稿的标记只严格覆盖 retain／merge，rebuild／exclude 不向无关正文搬移标记。没有该列的旧表保持原覆盖契约，已纳入内容的位置仍须人工核定。

## page-navigation.json

PDF EPUB 可在 [epub.page_navigation] 设 mode="source-pages"、非空 source_label，并在其 locations 子表用语言代码绑定书内 JSON 路径；每种 EPUB 输出语言须声明完整位置。JSON 为 schema_version=1 与 pages 数组；每项含 pdf_page 和 position，页集合／顺序必须严格等于 page-map 的 retain／merge。

- position="marker"：已绑定来源对象的精确原位 marker；存在歧义时定点回源。
- position="next-code-line"：marker 后且下一 marker 前的代码块，block 为 1-based 序号（默认 1），line 为 1-based 原行号，line_text 为含全部缩进的完整原行。
- position="code-line"：用 target 指定稳定代码容器 ID，再声明 line／line_text。
- position="before-target"：用 target 指定稳定非代码块 ID；不得伪造代码内部页界。整页图表为保连续段落延后时，可同时声明 after_page 与非空 reason；此时 target 只能为真实 Figure／Table，after_page 必须为同一内容 XHTML 中更后且未转置的纳入原页，目标不得重复。

pages 始终保来源页序；显式转置页从该序列移除后，在其 after_page 后按原相对顺序插回，唯一派生 DOM 页点序。page-list 仍按来源页序精确链接真实目标；声明外逆序、漂移、错误目标及跨文件锚拒绝，无配置旧调用仍严格按原序。只有已核定位置才可声明。最终空 span 保留既有稳定 ID，无 ID 时使用 source-page-N、epub:type="pagebreak"、doc-pagebreak 与可访问标签；page-list 顺序、href、标签及代码行首偏移均由 QA 核对。标签优先 original_printed_page，空值明确为 PDF N；OPF dc:source 采用 manifest 来源 SHA-256 URN 与 source_label，并以 property="source-of"、值为 pagination 的元数据精化绑定来源。

EPUB 来源可显式设置 mode="native-pagebreaks"，同样要求 source_label 与按语言绑定的 locations。sidecar 为 schema_version=1 与 pages 数组，每项必含 target（原稳定 ID）和 label（原页标签字符串），可选 before_target；不含 pdf_page／position，不读 page-map，也不从 source-unit 推算页码。完整清单按来源阅读顺序只列阅读版纳入点，标签可以是罗马数字或非连续值；排除点须在 source-units／STATUS 说明，不搬移凑数。

正式底稿在标题外保留标准空 span 的 epub:type="pagebreak"、原 ID／title／class／文字位置及既有 href；缺省归一化只补 doc-pagebreak／可访问标签，派生唯一 page-list 与上述 OPF 来源。若 Pandoc 把章首空点留在前一分片，可显式声明 before_target，绑定唯一 h1–h6 或直接以标题为首内容的 section／div ID；已核定来源图注末、图像前的点也可显式绑定原生 figure ID，不接受任意 img／p。完整 Table 或无前导正文、首个有效子对象为 Table 的 section／div 表组也可由稳定 ID 显式绑定；表组前仅跳过标准空页点及空白，拒绝行／单元格、代码及嵌套于另一表中的目标。表格新用法的暂寄点不得位于代码或表内，以免迁出后改变原对象；旧标题／Figure 条件不变。不把长页点 Span 插入管道表以免改变列宽，可用原生 fenced Div 表达真实表组。共享层复用原点恢复到目标之前，保留文字／尾文本，旧本地 href 跟随跨文件迁移。同一目标可由来源顺序中连续的显式页点共享，最终组成同文件／同父级的有序相邻链，最后一点紧邻声明目标；非连续声明、链中其他节点、未声明点、非空白尾文本及错序／漂移均失败，单点行为保持。标题容器在首标题前不能有非空白文字，不向标题内塞点或删除 nav 克隆规避。最终 QA 对清单与内容 spine 中的全部页点严格核对，并拒绝 nav／非 spine 孤立页点、页点自身或祖先的 hidden／aria-hidden="true"。共享空段清理在原位置保留被删段落的 tail，不删相邻正文；CSS 不删除或隐藏标准页点；实际位置仍由原来源和真实浏览器落点核定。

稳定 Python API 的 load／apply／validate 调用签名不变，PageLocation 原参数保留并追加缺省为 None 的 after_page／reason；显式转置必须传完整 config 验证，结果 targets 按 page-list 来源序返回；PageNavigationConfig 原四个位置参数保留，新增 mode 默认 source-pages；NativePageLocation 原 target／label 两参保留，新增 before_target 默认 None。PageNavigationResult.targets 的首项在 PDF 模式仍为物理页 int，在 native-pagebreaks 模式为原 label 字符串，并显式返回 mode。

旧书不声明时不新增前提。无可靠页点的来源可设 mode="not-applicable" 与非空 reason，不需 sidecar，也不生成 page-list。

## chapter handoff v1／v2

新 Markdown 分片合入前显式调用 handoff-check WORK --lang LANG --root ROOT --scope-file scope.json --package package.json --activity agent-review。ROOT 默认本书 Works，也可位于本书 scratch；JSON、条目与资产都用根下相对路径，解析 symlink 后越界会失败。其他 Adapter 保留原交接流程。

主 Agent 的 scope 包含 chapter_id、header_level（默认 1）、languages，以及 source={kind:"page"|"unit",ids:[完整递增集合],start:{id,anchor},end:{id,anchor}}。package 的 schema_version 为整数 1 或 2，chapter_id／source 必须与 scope 完全一致；entries 每项含 language／path／start_anchor／end_anchor，必须覆盖全部语言和实际章节首尾。

package 同时声明 assets（精确图片引用集合）、terms（glossary 四列增量）、issues（kind 为 question／erratum，含 source_id／anchor／detail／status="resolved"），非空 review.validations=[{name,result:"pass"}]。v1 保原 review.visual={reviewer,source_ids} 完整来源声明；v2 使用 review.automatic={reviewer,source_ids} 覆盖完整来源，review.visual 为可空的异常数组，每项含 reviewer、非空有序唯一的来源子集 source_ids 和非空 reason。无变化用空数组，不省字段。正式 reader 核对完整来源 marker、辅助来源集合、唯一章节边界与真实首尾；术语与当前目标语言 glossary 冲突、资产缺失或未裁决疑点均失败。回执声明不自动证明测试已经运行或实际位置正确；visual_review_owner_required 在 v1 为 true、v2 为 false，仅表示是否要求完整视觉声明，自动断言不足以裁决时由主 Agent 定点回源。

## source-units.tsv

没有可靠固定页码的 EPUB 必须使用下列原列，可追加上述 disposition：

```text
unit_id	spine_order	xhtml_path	linear	kind	title	note
```

`unit_id` 和 `spine_order` 均从 1 连续编号；每行对应一个 OPF spine `itemref`，`xhtml_path` 为包内规范路径，`linear` 为 `yes` 或 `no`。`kind`、`title` 和 `note` 用于人工交接，不作为自动语义判断依据。正式 Markdown 用 `[]{.source-unit data-unit="N"}` 在相同 OPF 顺序登记逻辑溯源单元；Pandoc filter 在 EPUB 成品中移除该标记。

纯标题单元可用原生 `Div.source-unit` 包住唯一 Header：容器无 ID、仅 source-unit 类和数字 data-unit 属性，不承载样式或正文；标题保持原 ID／级别／内联语义。reader JSON 保留元数据，实际 writer 只展开 Header；EPUB 的 JSON 中间路线在 writer 边界执行同一展开，不改变来源审计 AST。例：

```markdown
::: {.source-unit data-unit="10"}
# 第一部分 {#part-one}
:::
```

## glossary.tsv

统一列名：

```text
kind	source	target	note
```

`kind` 可使用 `title`、`author`、`translator`、`chapter`、`name`、`person`、`place`、`institution`、`term`、`concept` 和 `work`。`name` 保留给尚未细分的现有专名；新条目优先使用更具体的类型。同一目标语言使用一个术语表。

同一精确 `source` 只能对应一个 `target`。重复记录使用相同 `target` 时允许保留不同页段或备注；出现不同 `target` 时，`check` 必须失败并报告冲突行。检查区分大小写且不做模糊归一；确有不同语义时应把 `source` 写得更精确，而不是依赖备注隐藏差异。

## 共同语义

| 语义 | Typst | Pandoc Markdown | HTML | LaTeX |
| --- | --- | --- | --- | --- |
| 标题 | `=`, `==` | `#`, `##` | `h1`, `h2` | `chapter`, `section` |
| 强调 | `#emph[...]` | `*...*` | `em` | `\emph{...}` |
| 展示引文 | `#quote(block: true)` | `>` | `blockquote` | `quote` |
| 脚注 | `#footnote[...]` | `[^id]` | `aside` 或脚注链接 | `\footnote{...}` |
| 诗行 | `#verse-block[...]` | Pandoc line block | `.verse` | `translatorverse` |
| 源页 | `#source-page(23)` | `[]{.source-page data-page="23"}` | `span.source-page[data-page]` | `\sourcepage{23}` |
| 源单元 | 不适用 | `[]{.source-unit data-unit="3"}` | `span.source-unit[data-unit]` | 不适用 |
| 中文概念 | `#cn-concept[译名][原文]` | `[译名]{.cn-concept data-original="原文"}` | `span.cn-concept[data-original]` | `\cnconcept{译名}{原文}` |

连续斜体、跨页段落和展示结构绑定真实来源，扫描疑点以相关原图裁决；节点数量一致不能单独证明语义正确。图片的完整图注与短 alt 分开：单段沿 Pandoc 图片说明／alt 属性，多段由 native-figure Div 和唯一末尾 figure-caption Div 映射原生 Figure／Caption，保块、ID／属性和链接；空、额外或嵌套图注及隐式嵌套 Figure 均拒绝。native-figure 缺省仍要求 Image；显式 data-figure-body="blocks" 才接受非空原生块（含 Math、代码、列表、Table、HorizontalRule），拒绝 Raw、纯空白／源点与未知模式。新模式的图注及其有意义内层内容不得由 hidden、aria-hidden 或明确内联样式隐藏；空隐藏元数据不误拒，任意外部 CSS 可见性由浏览器消费。块图示宽公式等比保留并局部横滚，旧图不改。可直接使用的写法见 [Lua 注释示例](../../formats/pandoc/semantics.lua)。

## CLI 稳定行为

精确参数、选项和默认值只由 `tools/translator.py --help` 及对应子命令 `--help` 定义。当前稳定语义是：

- `init` 只创建不存在的单书工作目录和初始契约文件，不覆盖已有工作。外部只读来源须显式 `--import-source`，复制到新作品 `assets/source/`，manifest 绑定该副本，STATUS 保留原路径，不写 Books 或原文件。缺少该选项仍拒绝仓库外来源。EPUB 无 `Page_N` 时按 OPF spine 初始化 `source-units.tsv`，不伪造页码；此路径使用 Markdown 或 HTML。损坏包、重复锚点或非法 spine 阻断，不用 fallback 掩盖。
- `scratch WORK --agent NAME` 输出 JSON 路径和来源哈希，创建安全命名的作品临时分片；不清理已有文件。`handoff-check` 只验证显式章节交接包，不写正式稿或推断视觉完成，字段见 chapter handoff v1／v2。
- `source-probe WORK --agent NAME` 只读探查全部 EPUB spine，在 scratch 的独立目录保存 `profile.json`：来源哈希、逐单元元素／class／显式语义／ID／链接／样式依赖，以及可定位的不支持项。class 只作为来源事实，不推断引文或尾注。`ready_for_draft` 只表示探查未发现已知阻塞；不保证后续转换、内容或视觉验收通过。
- `source-draft WORK --agent NAME` 先保存同一完整探查报告；存在阻塞时停止并返回报告路径。通过初步探查后，在独立目录生成 `draft.md` 和 `source-map.json`，不覆盖旧草稿，不写正式底稿、进度或译文。映射保留 `href` 与原 `source_id`，以确定的 `target_id` 重写合并范围内的片段和文件入口链接；同文件重复 ID、缺失目标仍阻断。带查询参数的包内链接、独立显式脚注和复杂图表／MathML 本轮仍须先研究。
- 草稿转换检查未消费来源文字，再以正式 reader 核对正文非空白字符保全、各注释的有序文字、标题层级、实际锚点和链接目标；正文字符检查不证明阅读顺序、样式或全部语义正确。明确尾注容器的标题与回链正文保留，非空未识别 Notes 不输出为空容器。机器通过仍返回 `verified: false`，需要来源绑定的内容核定和异常裁决；纯标题单元内的 source-unit 草稿标记会警告，正式底稿仍按翻译流程安置。
- `source-figures` 只在 scratch 生成 PDF 图形候选，不改正式书籍或终态证据；原生对象、图注归属、runtime、失败状态和逐页复用见 [来源图形工站契约](SOURCE_FIGURES.md)。
- `station [WORK]` 是不运行书籍检查或构建的工站边界；没有具体作品的文档、代码和协调活动使用默认 `project` lane。
- `render` 把指定来源页渲染到该书隔离的临时证据目录。
- `check` 验证 manifest、来源哈希、路径、映射表、术语表和受支持的底稿结构，不写正式输出；声明来源图像闭包时还按 SHA-256 比较正式底稿栅格图片与 PDF 原始图像对象。Markdown 编号标题在同一一级标题范围内不得重复或偏离该范围的编号层级，并汇总错误；显式编号图注与连续图片一一对应时，每幅图片的替代文本必须保留同号并设置 `#fig-` 标识。该检查只核对已声明图号，不猜测原书编号；活动作品不一致会阻断，已完成作品报告兼容性候选。正式 reader 的 AST 中，溯源标记吞并标题或进入标题节点、打断列表都会阻断；另报告行内中文源页空格候选。代码围栏内可保留 Git 冲突示例，围栏外冲突标记阻断。LaTeX EPUB 在此阶段预检正式展开、规范化和 Lua 兼容，公式生成留在构建。构建归一化图注和中文源页空格，QA 阻断重复图注和列表碎片，浏览器负责渲染验收。
- `boundary-audit` 只读解析底稿中的源页标记，生成跨页连续性候选的 `report.json`、`review.tsv` 和 `summary.txt`；`--pages` 可缩小复核页对，候选不会阻断 `check` 或构建。
- `build` 只构建 manifest 声明且被选择的输出；每个文件原子替换，失败保留未被替换的旧文件。直接构建会先失效旧 `refresh`／`final` 摘要，防止摘要继续绑定已变化的输出。
- `qa` 重建所选输出的自动 QA 证据，保留未选择目标的证据；全量选择时先清空整组证据。EPUB 额外写出 `outline.txt`，逐行保存 toc 深度、可见标题和 href。
- `browser-qa` 只对所选正式 EPUB 执行隔离浏览器验收，默认运行窄屏、宽屏读者深色配色、窄屏关闭出版社样式并覆盖用户字体与 200% 字号三种模式。禁用出版社样式时保留基础适配，中文全宽标点要求实际 `text-spacing-trim: space-all`，不因关闭样式豁免资源、内容或溢出检查。每模式执行本次全部选中 XHTML 的资源、链接和脚注往返，未选成员仍可作为跨文件目标加载；选择为空、重复或未知成员失败。结果绑定同一成品哈希，保留未选择语言的证据且不清理其他 session。
- `compare-epub OFFICIAL CANDIDATE` 排除两端已知的 `META-INF/calibre_bookmarks.txt` 及共享写出器生成的 OPF 构建时间后逐成员比较两个 EPUB；其余成员集合和内容全部相同时返回 0。它单独报告阅读器书签、易变构建时间和真实成员差异，但不复制或覆盖文件。
- `benchmark` 只聚合本机历史流程日志，不运行或修改书籍任务；可按作品目录名或 `work.id` 过滤。
- `refresh WORK --lang LANG --target TARGET` 固定运行 `check → build → qa`，EPUB 再运行 `browser-qa`；--member 仅可用于 EPUB 并传递局部浏览器范围，完整构建和静态 QA 不变，随后写目标级 `refresh.json`。入口失效旧 refresh 摘要，通过 check 后再失效旧 final 摘要；语言和目标必须显式选择。它是迭代证据，不是全书完成证明。
- `finalize WORK [--browser-timeout SECONDS] [--browser-page-timeout SECONDS]` 可显式设置正整数的每模式整体预算及单页加载预算，默认分别为 600 秒和 30 秒；范围和通过条件不变，无新属性的旧 Python Namespace 保原默认。非法预算在载入作品与清理证据前拒绝。预算有效后先失效旧 refresh/final 摘要，再对全部 manifest 输出固定执行 `doctor → check → build → qa`，存在 EPUB 时运行 `browser-qa`；首个失败立即停止且不保留成功摘要。成功后写 `.local/translator/evidence/<work-key>/final/final.json`，绑定来源、manifest、正式输出的路径、大小和 SHA-256，以及对应临时证据目录与无正文验收摘要。它只证明机械链通过，不代表内容核定或异常裁决完成；final 要求与实际 EPUB 的完整 XHTML 清单相符的三模式结果，拒绝 selected 局部回执，旧无 coverage 的完整结果仍按实际包数量核验。
- `complete WORK` 只在 manifest 为 `active`、`STATUS.md` 已明确本地完成、当前 `final.json` 有效且其中全部正式输出未变化时，把 manifest 原子迁为 `complete` 并写 `completion.json`；它不编辑 STATUS，也不推断人工内容。迁移保持 manifest 原换行与注释，写入后再次核验全部输出；发现竞态时只在 manifest 仍为本次写入值时精确回滚。`final.json` 同时保存忽略 `work.status` 的构建配置摘要，因此仅此状态迁移不会让成品证据失效，其他 manifest 变化仍会阻断。
- `deliver WORK --lang LANG --target TARGET --to FILE` 只允许 manifest 与 `STATUS.md` 都已完成的作品、唯一正式输出和明确目标文件。它要求来源、manifest 构建配置、输出路径、大小和 SHA-256 与当前 `final.json` 一致；先在该语言／目标的独立证据目录原子写 `attempt.json`，再复制到目标目录内的 staging 文件，核验后才原子替换目标，最后再次复核并原子更新 `delivery.json`。失败尝试不删除同目标旧成功回执；未运行 `complete`、缺少或过期 finalize 证据时不会触碰目标文件，交付另一输出也不会删除已有回执。
- `clean` 只删除该书由已解析绝对路径哈希确定的 `.tmp/translator/` 目录，保留正式底稿、输出、`.cache/svg-math/`、本机流程日志和 `.local/translator/evidence/` 中的 final／completion／delivery／attempt 回执。清理后仍可交付，来源、构建配置或成品变化仍阻断；旧版已被清理的回执不会凭空恢复，需重新 finalize。

所有临时安全键使用已解析工作目录绝对路径的 SHA-256，不信任 `work.id`；清理前必须验证路径身份与 containment，同根内指向兄弟目录的 symlink／junction 也拒绝。CLI 子进程共用 run_hidden 的 Windows 隐藏标志并继承仓库临时根作为 `TEMP` 和 `TMP`；浏览器 runtime 托管本次 headless Chrome，以标准 CDP 附加并精确关闭本 session／profile，保留 namespace 隔离。TRANSLATOR_AGENT_BROWSER 指向既有原生 CLI，TRANSLATOR_BROWSER_EXECUTABLE 或 AGENT_BROWSER_EXECUTABLE_PATH 可指定已安装浏览器，缺省解析现有缓存，不修改全局配置。嵌套 uv 使用 frozen 模式，工具临时依赖不得重写项目锁文件。启动统一入口建议使用 `uv run --locked python tools/translator.py`。

## 流程日志与 benchmark

除 `benchmark` 自身外，统一 CLI 每次运行都在 `.local/translator/runs/` 原子写入 schema v2 JSON；读取器兼容既有 schema v1。运行标识保留时间戳和 PID，并附加随机后缀，不能依赖系统时钟分辨率保证唯一。日志只记录命令、结果、错误类型、机器耗时、revision、平台、作品属性和实际目标，不记录正文、完整命令或错误消息。

解析既有作品的命令必须显式提供一个或多个 `--activity`；`none` 只能单独使用。可选 scope、outcome、issue、actor 和 session 都是最长 80 字符的不透明标签，拒绝绝对路径；session 默认取 `CODEX_THREAD_ID`。工站比较同一 lane 上一份退出快照与本次入站快照，只处理 Git 脏文件的仓库相对路径、状态、大小和 SHA-256，并排除 `Books/`、输出、缓存、`.tmp/` 和 `.local/`。完整退出快照只覆盖写入 `.local/translator/state/` 的 lane 状态；永久运行日志只保留站间／站内净变化、数量和摘要，避免在脏工作树中重复复制同一清单。若 revision 已变化，原未跟踪文件在当前路径存在且已离开脏快照时记为 `clean_or_committed`，不误记为删除。开放工站、等待、`none`、未知身份和超过四小时的间隔不计入已声明投入；`human_elapsed_upper_bound_seconds` 始终只是站间上界。真正的同 lane 并发由操作系统互斥锁拒绝；成功取得锁后发现的同 lane 开放标记属于异常退出遗留，工站会回收并记为 `stale_open_station_recovered`。工站建立、结束或落盘失败会阻止工作命令成功，未落盘的开放标记供后续诊断。

`refresh` 和 `finalize` 额外记录各内部步骤。`benchmark` 按父命令分别聚合步骤，同时保留只含 finalize 的兼容字段；它还分别聚合命令机器耗时、结果、作品、活动的站间上界和摩擦代码，并把活动重复三次或跨两本书的情况列为改进候选。连续失败、失败等待超过 120 秒、blocked outcome、未归因文件变化和并发工站都写成结构化摩擦；它们只触发调查或 change 候选，不自动修改流程。首批小样本只用于发现趋势，不作稳定性能承诺。

## Adapter 命令

- Typst PDF：从仓库根目录运行 `typst compile --root <repo> <entry> <temp-output>`。新书可从仓库 root import `/formats/typst/...`，旧书的相对 import 保持有效。静态检查只支持字面量 `#include "path"`，动态 include 返回非零并要求调整，不静默跳过。
- Typst EPUB：先用 `typst compile --features html --format html` 导出完整 HTML，入口中的共享语义宏必须按 `target() == "html"` 输出标题、强调、引文、诗歌、源页和概念节点；结构化 HTML 解析器处理脚注和空溯源节点，再进入共享 EPUB 写出路径。显式 `[epub].cover` 优先；未声明时才用同一分页入口第一页临时渲染为 PNG。Typst HTML 尚属实验功能，版本升级后必须重新运行 Adapter 测试和全书 EPUB 验收。
- Markdown：从仓库根运行 Pandoc，显式处理 source-page／source-unit 并转义中文概念原文。HTML 使用 --standalone --embed-resources、formats/pandoc/semantics.lua、formats/html/book.css 与入口 resource path；EPUB 使用相同语义 filter、formats/epub/book.css 及 [epub].cover，进入共享写出器；PDF 使用相同 filter 与 --pdf-engine=typst。测试核对 HTML／EPUB／Typst 的 marker、概念及特殊字符，EPUB 覆盖三种 Adapter 和共享契约。
- HTML：入口必须是完整 HTML 文档；Adapter 原样复制，仅把可选的 `<!-- translator:book-css -->` 标记替换为含共用 CSS 的 `<style>` 元素，不经 Pandoc，不承诺内嵌其他资源。HTML QA 只检查完整文档、非空产物和遗留 CSS 标记，不替代真实浏览器验收。
- LaTeX PDF：构建器在仓库 `.tmp/translator/` 的临时目录展开字面量 LaTeX include，规范化独占行 `\\sourcepage` 的边界后运行 `latexmk -xelatex`；正式底稿不变，编译工作目录仍为入口目录，并通过 `TEXINPUTS` 加入工作目录和仓库级 `formats/latex//`。
- LaTeX EPUB：在工作目录边界内递归展开字面量 `\input`、`\include`、`\graphicspath`，经 Pandoc latex+raw_tex reader 和 `formats/pandoc/latex.lua` 映射源页、中文概念、分离脚注、交叉引用，再进入共享写出器。allowlist 外 raw TeX、缺失引用、动态路径和越界资源阻断；`tools/latex_epub.py` 仅为兼容入口。`\pageref` 链接并显示目标编号，不伪造固定页码。
- LaTeX 编号：含 `\part` 时缺省按二级章拆分；Lua 保原 reader 计数层级，仅按活动分部映射显示层级，分部前及 frontmatter/mainmatter/backmatter 切换后的独立章为根。`\appendix` 重置章/节状态并同步字母章前缀、子节、图表和引用；set/add/step/refstep 计数器继续有效。原生 equation/align 按自动编号行计数（含无 label 行），显式 tag 不递增，notag/star 行不自动编号；派生 tag 供 SVG 绘制，正式底稿不变。其他旧路径保留。
- LaTeX 原生图／表编号仅在 opt-in 模式启用：计数沿用来源 `setcounter`、格式及附录状态，有无 ID 均按同一计数器处理。已有同号前缀保留，不同图号或图表类型报错；空 caption 与 `\caption*` 不编号、不递增，普通短图注及 `\caption[]` 仍编号，`figure*` 只表示宽浮动。短图注标记只存在临时 EPUB reader 输入并由 Lua 消费，不改 PDF 或正式底稿。影响 figure/table 的 `setcounter`、`addtocounter` 等应位于浮动体外，相关格式应在该浮动体 caption 前定义；footnote 等其他计数仍保原位置和行为；浮动体内部 figure/table 计数操作、caption 后的本类型格式定义及内部跨图表类型格式定义在新模式下明确拒绝。表内本类型格式只影响该浮动体；注释、代码环境和行内 `\verb` 的字面命令不参与新模式判断。旧模式不增加这些前提。
- LaTeX `\epubfallback` 支持 SVG、PNG、JPG 和 JPEG，要求非空纯文本 alt、相对且位于工作目录内的现存资源；构建保留图像编码和像素，PDF 分支不要求 EPUB 回退资源。
- LaTeX 显式以 `\begin{epubtablescroll}…\end{epubtablescroll}` 包围需要横向滚动的原生表；EPUB reader 恢复其中唯一 Table，以原生 `.table-scroll` Div 包裹后继续已有计数、引用和语义处理，保留图注、单元格、链接、ID 和必要来源定位；原 table／longtable 内被 reader 提升出的完整表注按原顺序保留。原表外正文、额外表格、嵌套声明及无法识别内容会报错。PDF 中该环境透明，不改变表格，也不为普通表自动添加滚动容器。
- LaTeX 非打印声明：真实不编号题名用 `\chapter*{题名}``\label{reader-title}` 配合 `\epubunlisted{reader-title}`；目标须唯一原生不编号 Header，声明前后均可，未声明标题不变。quote 首个 `\epubcodeblock` 将富文本行映射 code-example/LineBlock，保 Math、Code、强调及 quad/qquad 缩进，普通引文不变；两 helper 的 PDF 路线均不打印标记。纯来源/空标签 Para 转 Plain，保 ID/页号和位置，不制造段距；正文及真实空白段不动。

所有 EPUB Adapter 的 QA 必须核对实际封面资源；声明 `[epub].cover` 时额外按字节比较，封面与清单不一致会阻断。正式构建先写入仓库 `.tmp/translator/`。发布时允许在正式输出目录创建唯一的临时 sibling，复制完成并刷新后用 `os.replace` 原子替换，`finally` 必须清理 sibling；这是保护旧输出的唯一例外。
