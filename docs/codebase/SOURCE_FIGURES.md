# 来源图形工站契约

## 保留完整图组，只复核异常

一幅图含多个图板时，不逐块手动合并。工站先用原生 PDF 调用、完整图注与独立区域恢复图组，再保留未归组候选供检查。

```shell
uv run python tools/translator.py source-figures "Works/<书名>" --pages 12,18-20 --agent figures --runtime .local/translator/source-figures.toml --activity source-review
```

`source-figures` 从 manifest 声明的 PDF 来源定位图形，只写作品 scratch。不会改正式底稿、资产、STATUS 或 final／delivery 回执。页选择使用 PDF 物理页；固定渲染为 225dpi，拒绝空、重复、倒置区间、越界页和非 PDF 来源。精确参数以子命令 `--help` 为准。

布局模型只提供种子，原生图像 placement 和 Form XObject 的完整调用优先恢复。图组保留原生画布与图内标签；不能因为模型把左右图板或九宫格分别检出，就改成多个语义 figure。只有原生调用、独立图注区域和唯一归属一致时才归组；整页容器、多图多注歧义、未知裁剪及剩余候选仍保留问题。

白面板不定义语义归属。无法核定的图内文字、regionless 图注、裁切跨界或多图归属进入复核，不自动应用未经验证的空白分割。分割用的深色核心与完整边界分开处理，不把浅灰抗锯齿边缘当空白删除。

## 配置已经安装的运行环境

runtime TOML 的显式路径相对配置文件目录解析；不下载模型、不替换引擎，也不自动安装依赖。

| 字段 | 类型与行为 |
| --- | --- |
| `python` | 必填路径，指向已有重工具 Python。 |
| `layout_model` | 必填路径，指向本地 PP-DocLayoutV3 ONNX 模型。 |
| `native_jar` | 必填路径，指向含 NativeAuditRunner 的 PDFFigures2 JAR。 |
| `java`、`mutool` | 可选路径；未设置时从 PATH 解析。 |
| `use_cuda` | 可选布尔，默认 false；显式 GPU 模式核验并记录实际 provider。 |
| `cuda_dll_dir` | 可选目录，仅适用于 `use_cuda=true`。 |

重工具环境需要 RapidDoc、ONNX Runtime、NumPy、OpenCV 和 Pillow；基础 CLI 导入不要求这些依赖。已有验证配置使用 RapidDoc 0.9.12、ONNX Runtime 1.23.0；报告记录实际版本。子进程隐藏执行，TEMP／TMP、工具缓存与临时 home 均指向本次 scratch。

原生 Form 增强使用配置 Python 中已有的 `pypdf`，无需修改 runtime TOML。缺失时明确记录该能力不可用，保留既有路线及其疑点，不自动安装或替换解析器。该可选能力只绑定后处理依赖，不让有效模型张量失效。

原生提取使用 AllenAI PDFFigures2 0.1.0 的 commit `3d7ad46753d4a315cccd1c2bcab398380e88c534`。重建时把本仓库 `tools/pdffigures2/NativeAuditRunner.scala` 放入该版源码的 `src/main/scala/org/allenai/pdffigures2/`，再执行上游 `sbt assembly`，让 runtime 指向生成的 `pdffigures2.jar`。上游和桥接文件按随附 Apache 2.0 许可使用，命令不自动获取或构建它们。

## 消费报告中的真实状态

CLI 返回报告路径、页数、对象数、状态和 `verified=false`：

- `candidates`：已生成候选，没有触发当前复核条件。
- `needs_review`：已生成报告，但有明确归属或完整性疑点。
- `failed`：工具或产物失败，命令非零退出；失败报告保留恢复线索。

前两者退出 0 只表示工站完成，不表示内容或全书验收通过。页级与对象级疑点、来源 glyph、模型原始行身份、原生 placement、裁切框和实际工具配置均在报告中；提取文本和原始张量只留在 scratch。裁图保原页像素和原生彩底，不由识别文字重画。

先看 CLI 的 `review` 简表和报告的 `review_summary.queue`：

- 每个待解决对象只列一项，页级冲突随对象附上；失败页和没有对应对象的异常另列。工站失败保留独立恢复项，页失败及错误不能被图注等内容告警遮住。
- `run_issues`、`review_pages`、`review_objects` 与 `page_only_issues` 表示实际问题队列，`native_form_groups` 和 `consolidated_model_seeds` 记录自动归组量。
- 无候选且无冲突的页写入 `no_candidates` 信息，不生成逐页人工任务。`coverage=not_established` 仍表示来源覆盖未知，不能把它当已证明无图或整书完整。
- 归组对象保留真实 `model_seed` 兼容字段及全部 `model_seeds`，不把原生边界伪装成模型框。
- 已证明属于 Form 的字形，若完整原生画布和有效裁剪均已保留，名义字体度量越界只记入 `native_form_metric_overhangs`；未证明归属或裁剪不完整时仍保留遗漏警戒。

图注优先沿用完整、唯一的原生文本对齐；跨文本块及数学字形转码差异可通过独立 caption boundary 和精确编号定位完整 glyph 行。副提取器的未知字形和数学转码只在对应的原生 glyph 位置有界匹配，不修改原文，也不吞掉具体字母数字冲突或额外正文。报告区分定位方式和字符串是否等价；正文引用、多个匹配或不完整边界不能强行配注。

候选必须另以来源绑定的适用检查核定遗漏、外来内容、完整图注身份和唯一归属，才可按既有书籍流程使用。严格目标像素损失不能用一像素容差抵消；前景 alpha、模型框或节点计数不能证明内容完整。

## 按有效依赖复用，不重复有效推理

`--reuse REPORT` 只消费同一作品临时根内的报告。正式入口还校验报告中的依赖、raw、来源提取和候选图片路径，拒绝越界及重定向产物。原始 PDF、工具和模型路径按各自显式配置处理，不误作 scratch 产物。

- 来源路径／大小／修改时间、manifest 声明身份和逐页输入绑定原始结果。
- 推理绑定模型、预处理、渲染、DPI 及相关运行依赖；变化时对应 raw 失效。
- 图注、几何等后处理依赖单独绑定；只改后处理时，从有效 raw 重算，不再 forward。
- 同源且渲染依赖未变时，逐文件核对后复用既有 PNG／SVG／structured text／trace 和旋转信息，`reused_source_pages` 记录实际复用页；不反复渲染相同页面。
- 选页变化复用交集，仅新增页运行；依赖产物缺失或变化会明确失效，旧报告不覆盖。

复用仅在 scratch，不建立书级持久缓存。来源、配置和依赖不变的证据继续有效；未知覆盖保持未知，不升级为通过。
