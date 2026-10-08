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

约束：

- `work.status` 只能是 `planned`、`active` 或 `complete`。
- `authoring.format` 只能是 `typst`、`markdown`、`html` 或 `latex`。
- 语言代码唯一，且恰有一个 `role = "source"`。
- 源语言和目标语言只从 `languages` 的 `role` 推导，不设置重复字段。
- 交付目标只从各语言的 `outputs` 推导，不设置重复的 `build.targets`。
- Markdown 需要 PDF 时允许在 `authoring.pdf_engine` 中写 `typst`；其他格式不得设置该字段。
- Markdown 声明 EPUB 输出时必须设置 `[epub].cover`，且封面路径位于书籍目录内并在构建前存在。Typst 或 LaTeX 声明 EPUB 输出时必须设置 `[epub].title`，可选设置 `[epub].author`；语言可用 `epub_title`、`epub_author` 和 `epub_identifier` 覆盖。标识符未设置时稳定生成为 `urn:translator:<work.id>:<language.code>`。
- Typst EPUB 和 PDF 来源的 LaTeX EPUB 可从分页入口或来源 PDF 第一页临时生成封面，不在工作目录保存派生图片；其他来源的 LaTeX EPUB 必须显式设置 `[epub].cover`。
- `entry` 和 `glossary` 必须位于书籍目录内；输出必须位于该书的 `output/` 内。
- 所有输出路径必须唯一，不得与 manifest、状态、页码表、入口、术语表或其他输入文件相同。
- 来源必须是仓库内现存的 PDF 或 EPUB 文件，`source.sha256` 必须是 64 位十六进制摘要；正式来源通常位于 `Books/`。PDF 使用 `[pdf].pages` 和 `page-map.tsv`；带连续 `Page_N` 锚点的 EPUB 可使用 `source.pages`、`source.anchor_pages` 和 `source.anchor_set`；没有可靠固定页码的 EPUB 使用 `source.units = "source-units.tsv"`，校验器验证 OPF spine 与逻辑单元表完全一致。
- 非扫描 PDF 中的正式底稿如果直接使用来源图像对象，可设置 `[source.images].mode = "embedded"`。`check` 用 `pdfimages -all` 提取来源对象并按 SHA-256 验证底稿栅格图片闭包；该模式需要 `pdfimages`，裁切、重采样或替换都会阻止工站通过。
- CLI 从 `tools/translator.py` 的真实路径确定仓库根目录，不依赖当前工作目录。
- 所有 containment 检查都在解析 symlink 或 junction 后执行；越出允许目录立即失败。
- Typst 支持 PDF，以及经实验性 HTML 导出再由 Pandoc 生成的 EPUB3；Markdown 支持 HTML、EPUB3 和 Typst PDF；LaTeX 支持 PDF 和 EPUB3；HTML 只支持 HTML。

`manifest.toml` 只保存稳定机器配置和来源事实。逐页核定、待处理问题和最新验收结果只写入 `STATUS.md`。

## 流程 CLI

- `uv run python tools/translator.py prepare WORK --activity ...` 对中文 Markdown 入口依次运行 AutoCorrect 修复、lint 和 `check`；随后只能使用目标级 `refresh` 或一次性 `finalize`。
- `check` 会在存在可识别状态声明时比较 manifest 与 `STATUS.md`；状态只解析冒号后的第一个明确值，不从后续说明中的“完成／验收”推断。状态未知时不推断完成；两者不一致必须先修正，不能继续构建。LaTeX 作品声明 EPUB 输出时，`check` 还会复用正式展开、规范化和 Pandoc/Lua 过滤链做快速预检，但不生成公式或写正式输出。
- 同一作品／会话的工站 lane 由 CLI 互斥；锁文件只在 `.local/translator/state/`，由进程退出自动释放。
- PDF 来源页数、来源页渲染和 QA 统一以 `mutool` 为入口，不依赖 `pdftoppm.cmd` 包装器。
- `doctor --for all` 还扫描 `README.md`、`AGENTS.md`、`CONTEXT.md` 和 `docs/**/*.md` 中的本地 Python 入口；不存在或不唯一的路径属于文档入口漂移，必须先修复。
- 共享回归使用项目 uv 环境：`uv run pytest -q`；`uv run python -m unittest discover -s tests -p "test_*.py"` 作为无插件后备入口。
- 官方 EPUBCheck 源码固定为 `tools/epubcheck` 子模块；运行文件由 `uv run python scripts/setup_epubcheck.py` 校验并安装到子模块忽略的 `target/bookloom/`。主仓库不保存 JAR，缺少子模块或运行文件时 EPUB QA 必须给出准备命令并停止。

## 派生 EPUB 与证据

LaTeX、Typst、Markdown 或 HTML 中声明的正式底稿格式只有一种；EPUB 若未由 manifest 声明为正式目标，只能从已验收底稿派生，不能成为第二份可编辑来源。项目 QA 必须解析全部正文 XHTML，检查包内资源、链接和片段目标，并自动运行项目固定的官方 EPUBCheck；最终哈希对应的包还必须由 `agent-browser` 直接执行解包后的 XHTML、CSS 和 SVG，渠道专版才追加目标阅读器检查。生成和解包证据只留在仓库 `.tmp/`。

共享 EPUB 的可重排样式、导航、脚注、公式、资源和验收契约由 `docs/workflow/EPUB.md` 定义。所有 Adapter 使用同一最终契约，输出文件名只由 manifest 决定；单本书不得另设隐式兼容路径。

共享 SVG 公式缓存位于 `Works/<书名>/.cache/svg-math/<工具链版本>/`，按工作目录和固定工具链版本隔离，不进入版本控制，也不属于 manifest 输出。首次构建可以从现存正式 EPUB 预热可无歧义匹配的公式；后续构建只调用 GladTeX 编译缓存未命中项。公式子进程失败时，允许在抛回原错误前原子检查点当前 GladTeX v2 缓存中路径受限、尺寸有限且 SVG 有效的完整条目；损坏、越界或不完整条目不得进入缓存。正文 AST、导航和 EPUB 包仍完整重建，失败时仍保留旧正式输出。

正式浏览器验收只选择 manifest 声明的 EPUB，使用项目 namespace 和单次唯一 session 串行执行全部 XHTML，并把哈希、进度、DOM 结果和代表页截图写入该书隔离的 `.tmp/translator/` 证据目录。横向溢出失败同时记录 offending selector、元素边界、`clientWidth` 和 `scrollWidth`。命令只关闭自己的 session，不得全局清理 daemon、Chrome 或其他 session；精确参数以 `browser-qa --help` 为准。

## page-map.tsv

必需列保持为：

```text
pdf_page	original_printed_page	section	chapter_id	chapter_title	note
```

`pdf_page` 必须从 1 到来源 PDF 总页数连续且唯一。该表仅适用于 PDF 来源；没有固定页码的 EPUB 使用下述 `source-units.tsv`，不伪造 `pdf_page`。`original_printed_page` 可以为空，也可以使用罗马数字。`section`、标题和备注允许使用书籍工作语言，不作为自动语义判断依据。

## source-units.tsv

没有可靠固定页码的 EPUB 必须使用：

```text
unit_id	spine_order	xhtml_path	linear	kind	title	note
```

`unit_id` 和 `spine_order` 均从 1 连续编号；每行对应一个 OPF spine `itemref`，`xhtml_path` 为包内规范路径，`linear` 为 `yes` 或 `no`。`kind`、`title` 和 `note` 用于人工交接，不作为自动语义判断依据。正式 Markdown 用 `[]{.source-unit data-unit="N"}` 在相同 OPF 顺序登记逻辑溯源单元；Pandoc filter 在 EPUB 成品中移除该标记。

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

连续斜体、跨页段落和展示结构的判断仍以扫描图为准。节点数量一致只代表静态检查通过，不代表视觉或语义验收完成。

## CLI 稳定行为

精确参数、选项和默认值只由 `tools/translator.py --help` 及对应子命令 `--help` 定义。当前稳定语义是：

- `init` 只创建不存在的单书工作目录和初始契约文件，不覆盖已有工作。EPUB 无 `Page_N` 时按 OPF spine 初始化 `source-units.tsv`，不伪造页码；此路径使用 Markdown 或 HTML。损坏包、重复锚点或非法 spine 阻断，不用 fallback 掩盖。
- `scratch WORK --agent NAME` 输出 JSON 路径和来源哈希，创建安全命名的作品临时分片；不清理已有文件。
- `source-draft WORK --agent NAME` 在 scratch 的每次独立运行目录生成原生 XHTML 的 Markdown 草稿和来源映射；不覆盖旧草稿，复验不需先删除分片。使用正式 reader flags，保留原生语义并报告不支持或歧义，不写正式底稿、进度或译文。草稿仍需逐单元视觉核定。
- `station [WORK]` 是不运行书籍检查或构建的工站边界；没有具体作品的文档、代码和协调活动使用默认 `project` lane。
- `doctor` 只探测所选用途实际需要的工具；`--for all` 另外执行权威文档入口漂移检查。
- `render` 把指定来源页渲染到该书隔离的临时证据目录。
- `check` 验证 manifest、来源哈希、路径、映射表、术语表和受支持的底稿结构，不写正式输出；声明来源图像闭包时还按 SHA-256 比较正式底稿栅格图片与 PDF 原始图像对象。Markdown 编号标题在同一一级标题范围内不得重复或偏离该范围的编号层级，并在一次检查中汇总全部此类错误；显式编号图注与连续图片一一对应时，每幅图片的替代文本必须保留同号并设置 `#fig-` 标识。该检查只核对底稿已经声明的图号，不从正文或图片顺序推断原书编号；活动作品的不一致会阻断，已完成作品只报告兼容性候选。源页／源单元标记打断列表时阻断工站，并报告中文字符两侧的行内源页空格候选。正式 Markdown 可在代码围栏内保留 Git 冲突示例，但围栏外的 `<<<<<<<`／`>>>>>>>` 仍由语义检查阻断。LaTeX EPUB 的正式展开、规范化和 Pandoc/Lua 过滤也在此阶段预检，但 GladTeX／SVG 公式生成留在构建阶段。EPUB 构建阶段归一化自动图注和中文源页空格，`qa` 在真实 EPUB 结构上阻断残留重复图注和列表碎片，`browser-qa` 负责渲染验收。
- `boundary-audit` 只读解析底稿中的源页标记，生成跨页连续性候选的 `report.json`、`review.tsv` 和 `summary.txt`；`--pages` 可缩小复核页对，候选不会阻断 `check` 或构建。
- `build` 只构建 manifest 声明且被选择的输出；每个文件原子替换，失败保留未被替换的旧文件。直接构建会先失效旧 `refresh`／`final` 摘要，防止摘要继续绑定已变化的输出。
- `qa` 重建所选输出的自动 QA 证据，保留未选择目标的证据；全量选择时先清空整组证据。EPUB 额外写出 `outline.txt`，逐行保存 toc 深度、可见标题和 href。
- `browser-qa` 只对所选正式 EPUB 执行隔离浏览器验收，默认运行窄屏、宽屏读者深色配色、窄屏关闭出版社样式并覆盖用户字体与 200% 字号三种模式。每模式执行全部 XHTML、资源、链接和脚注往返；结果绑定同一成品哈希，保留未选择语言的证据且不清理其他 session。
- `compare-epub OFFICIAL CANDIDATE` 排除两端已知的 `META-INF/calibre_bookmarks.txt` 及共享写出器生成的 OPF 构建时间后逐成员比较两个 EPUB；其余成员集合和内容全部相同时返回 0。它单独报告阅读器书签、易变构建时间和真实成员差异，但不复制或覆盖文件。
- `benchmark` 只聚合本机历史流程日志，不运行或修改书籍任务；可按作品目录名或 `work.id` 过滤。
- `refresh WORK --lang LANG --target TARGET` 固定运行 `check → build → qa`，EPUB 再运行 `browser-qa`，并写目标级 `refresh.json`。入口失效旧 refresh 摘要，通过 check 后再失效旧 final 摘要；语言和目标必须显式选择。它是迭代证据，不是全书完成证明。
- `finalize WORK` 先失效旧 refresh/final 摘要，再对全部 manifest 输出固定执行 `doctor → check → build → qa`，存在 EPUB 时运行 `browser-qa`；首个失败立即停止且不保留成功摘要。成功后写 `.local/translator/evidence/<work-key>/final/final.json`，绑定来源、manifest、正式输出的路径、大小和 SHA-256，以及对应临时证据目录与无正文验收摘要。它只证明机械链通过，不代表人工内容或视觉验收完成。
- `complete WORK` 只在 manifest 为 `active`、`STATUS.md` 已明确本地完成、当前 `final.json` 有效且其中全部正式输出未变化时，把 manifest 原子迁为 `complete` 并写 `completion.json`；它不编辑 STATUS，也不推断人工内容。迁移保持 manifest 原换行与注释，写入后再次核验全部输出；发现竞态时只在 manifest 仍为本次写入值时精确回滚。`final.json` 同时保存忽略 `work.status` 的构建配置摘要，因此仅此状态迁移不会让成品证据失效，其他 manifest 变化仍会阻断。
- `deliver WORK --lang LANG --target TARGET --to FILE` 只允许 manifest 与 `STATUS.md` 都已完成的作品、唯一正式输出和明确目标文件。它要求来源、manifest 构建配置、输出路径、大小和 SHA-256 与当前 `final.json` 一致；先在该语言／目标的独立证据目录原子写 `attempt.json`，再复制到目标目录内的 staging 文件，核验后才原子替换目标，最后再次复核并原子更新 `delivery.json`。失败尝试不删除同目标旧成功回执；未运行 `complete`、缺少或过期 finalize 证据时不会触碰目标文件，交付另一输出也不会删除已有回执。
- `clean` 只删除该书由已解析绝对路径哈希确定的 `.tmp/translator/` 目录，保留正式底稿、输出、`.cache/svg-math/`、本机流程日志和 `.local/translator/evidence/` 中的 final／completion／delivery／attempt 回执。清理后仍可交付，来源、构建配置或成品变化仍阻断；旧版已被清理的回执不会凭空恢复，需重新 finalize。

所有临时安全键使用已解析工作目录绝对路径的 SHA-256，不信任 `work.id`；清理前必须验证路径身份与 containment，同根内指向兄弟目录的 symlink／junction 也拒绝。CLI 子进程统一继承仓库临时根作为 `TEMP` 和 `TMP`；嵌套 uv 使用 frozen 模式，工具临时依赖不得重写项目锁文件。启动统一入口建议使用 `uv run --locked python tools/translator.py`。

## 流程日志与 benchmark

除 `benchmark` 自身外，统一 CLI 每次运行都在 `.local/translator/runs/` 原子写入 schema v2 JSON；读取器兼容既有 schema v1。运行标识保留时间戳和 PID，并附加随机后缀，不能依赖系统时钟分辨率保证唯一。日志只记录命令、结果、错误类型、机器耗时、revision、平台、作品属性和实际目标，不记录正文、完整命令或错误消息。

解析既有作品的命令必须显式提供一个或多个 `--activity`；`none` 只能单独使用。可选 scope、outcome、issue、actor 和 session 都是最长 80 字符的不透明标签，拒绝绝对路径；session 默认取 `CODEX_THREAD_ID`。工站比较同一 lane 上一份退出快照与本次入站快照，只处理 Git 脏文件的仓库相对路径、状态、大小和 SHA-256，并排除 `Books/`、输出、缓存、`.tmp/` 和 `.local/`。完整退出快照只覆盖写入 `.local/translator/state/` 的 lane 状态；永久运行日志只保留站间／站内净变化、数量和摘要，避免在脏工作树中重复复制同一清单。若 revision 已变化，原未跟踪文件在当前路径存在且已离开脏快照时记为 `clean_or_committed`，不误记为删除。开放工站、等待、`none`、未知身份和超过四小时的间隔不计入已声明投入；`human_elapsed_upper_bound_seconds` 始终只是站间上界。真正的同 lane 并发由操作系统互斥锁拒绝；成功取得锁后发现的同 lane 开放标记属于异常退出遗留，工站会回收并记为 `stale_open_station_recovered`。工站建立、结束或落盘失败会阻止工作命令成功，未落盘的开放标记供后续诊断。

`refresh` 和 `finalize` 额外记录各内部步骤。`benchmark` 按父命令分别聚合步骤，同时保留只含 finalize 的兼容字段；它还分别聚合命令机器耗时、结果、作品、活动的站间上界和摩擦代码，并把活动重复三次或跨两本书的情况列为改进候选。连续失败、失败等待超过 120 秒、blocked outcome、未归因文件变化和并发工站都写成结构化摩擦；它们只触发调查或 change 候选，不自动修改流程。首批小样本只用于发现趋势，不作稳定性能承诺。

## Adapter 命令

- Typst PDF：从仓库根目录运行 `typst compile --root <repo> <entry> <temp-output>`。新书可从仓库 root import `/formats/typst/...`，旧书的相对 import 保持有效。
- Typst EPUB：先用 `typst compile --features html --format html` 导出完整 HTML，入口中的共享语义宏必须按 `target() == "html"` 输出标题、强调、引文、诗歌、源页和概念节点；结构化 HTML 解析器处理脚注和空溯源节点，再进入共享 EPUB 写出路径。显式 `[epub].cover` 优先；未声明时才用同一分页入口第一页临时渲染为 PNG。Typst HTML 尚属实验功能，版本升级后必须重新运行 Adapter 测试和全书 EPUB 验收。
- Markdown HTML：从仓库根目录运行 Pandoc，固定使用 `--standalone --embed-resources`，显式传入 `formats/pandoc/semantics.lua`、`formats/html/book.css` 和入口目录的 resource path，生成单文件 HTML。
- Markdown EPUB：从仓库根目录运行 Pandoc，显式传入 `formats/pandoc/semantics.lua`、`formats/epub/book.css` 和入口目录的 resource path，进入共享 EPUB 写出路径；使用 `[epub].cover` 作为封面。
- Markdown PDF：从仓库根目录运行 Pandoc，使用相同 Lua filter 和 `--pdf-engine=typst`。
- HTML：入口必须是完整 HTML 文档；Adapter 原样复制，仅把可选的 `<!-- translator:book-css -->` 标记替换为含共用 CSS 的 `<style>` 元素，不经 Pandoc，不承诺内嵌其他资源。HTML QA 只检查完整文档、非空产物和遗留 CSS 标记，不替代真实浏览器验收。
- LaTeX PDF：构建器在仓库 `.tmp/translator/` 的临时目录展开字面量 LaTeX include，规范化独占行 `\\sourcepage` 的边界后运行 `latexmk -xelatex`；正式底稿不变，编译工作目录仍为入口目录，并通过 `TEXINPUTS` 加入工作目录和仓库级 `formats/latex//`。
- LaTeX EPUB：先在工作目录边界内递归展开字面量 `\input`、`\include` 和 `\graphicspath`，再由 Pandoc `latex+raw_tex` reader 和 `formats/pandoc/latex.lua` 映射源页、中文概念、分离脚注和交叉引用，最后进入共享 EPUB 写出路径。含 `\part` 时按二级章节拆分，由 Lua 按实际章节层级编号，保持章节、图表及交叉引用编号连续；无分部时保留原路径。`\pageref` 在可重排成品中链接并显示目标编号，不伪造固定页码。allowlist 外的 raw TeX、缺失引用、动态路径和越界资源均阻止构建。`tools/latex_epub.py` 只保留兼容命令行入口。

所有 EPUB Adapter 的 QA 必须核对实际封面资源；声明 `[epub].cover` 时额外按字节比较，封面与清单不一致会阻断。

正式构建先写入仓库 `.tmp/translator/`。发布时允许在正式输出目录创建唯一的临时 sibling，复制完成并刷新后用 `os.replace` 原子替换，`finally` 必须清理 sibling；这是保护旧输出的唯一例外。

Markdown Adapter 必须通过 Lua filter 显式处理 `source-page` 和 `source-unit`，并正确转义中文概念原文。测试必须断言 HTML、EPUB 和 Typst 中的 marker、概念及特殊字符行为；EPUB 测试还必须覆盖三种 Adapter 和 `docs/workflow/EPUB.md` 的共享契约。

Typst 静态检查只支持字面量 `#include "path"`。检测到动态 `#include` 时返回非零并要求人工调整，不静默跳过。
