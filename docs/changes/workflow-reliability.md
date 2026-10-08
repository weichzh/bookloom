# 流程可靠性修复

## Status

implemented

用户于 2026-10-08 明确批准落实清理方案并解决本书暴露的流程问题。

同日后续指令「推送吧」授权提交本次流程修复并推送至 `origin/main`。此前留下的 EPUB 样式、封面及 LaTeX 改动不纳入本次提交。

## Problem

分散的临时目录和逐次 shell 删除造成重复审核；EPUB 初始化仅支持页锚点；临时转换器损失语义与来源位置；状态解析把说明文字当机器状态；浏览器默认验收缺少读者模式；清理删除终态与交付回执。

## Scope

- 提供受作品边界约束的 scratch 入口，由主 Agent 在验收后统一 clean。
- 无可靠页锚点的 EPUB 按 OPF spine 初始化 source-units，提供可复用的原生语义恢复辅助及来源位置报告。
- 精确解析 STATUS 的状态值，保留状态漂移阻断。
- 正式浏览器验收覆盖窄屏、深色与关闭出版社样式／用户字体／200% 字号。
- 将无正文的终态、完成和交付回执移出可清理区，保持哈希失效及路径保护。
- 本地 skill 明确使用本仓库协议；主 Agent 负责内部审查、来源映射和清理，用户只处理未授权的重要决定。

不改变权限、安全规则、既有书籍或既有 CSS／LaTeX 修改；只交付本次共享流程修复，不发布书籍或其他产物。

## Acceptance Criteria

1. 聚焦回归覆盖初始化、状态误判、来源语义、模式矩阵、路径逃逸及 clean 后交付。
2. 原始 EPUB 和既有成品保持不变；真实来源只读探针及正式浏览器 runner 复测通过。
3. 项目测试、文档检查、AutoCorrect 与 diff 检查通过；独立审查 blocking 关闭。
4. clean 只删除该书临时文件，保留无正文回执和工站日志，不因减少提示放宽安全边界。

## Result

- 已实现安全 scratch／每次运行独立草稿、原生 EPUB source-units 初始化与语义辅助、状态值解析、三模式浏览器验收、持久回执和本地 skill 路由。嵌套 uv 冻结项目依赖，避免验证重写锁文件。
- 最终全套测试：140 passed、53 subtests passed。命令为 `uv run --locked python` 启动 pytest；仅测试进程使用官方 PyPI 和 `UV_FROZEN=true`。原镜像一次 HTTP 514 失败已复跑通过，依赖版本和锁文件不变。
- 推送前仅暂存本次 15 个文件，并从 Git index 导出独立副本重新验证：138 passed、53 subtests passed（排除旧的两项 LaTeX 测试）；文档检查与 staged diff 检查通过。旧修改保持未暂存，证明本次提交不依赖它们；书籍正文、成品和临时诊断不进入 Git。
- Windows junction 回归修复前实际失败，修复后通过：作品临时键、回执键、final／completion、Agent 分片及 delivery 别名均拒绝，邻近文件和交付目标不变。complete → clean → deliver 及过期哈希阻断通过。
- 本书真实来源隔离初始化识别 16 spine 单元，不伪造页码；最新草稿经正式 Pandoc reader 解析：16 单元、243 Note、45 BlockQuote、0 RawBlock／RawInline，来源与 16 个 XHTML 哈希绑定有效。草稿未标为核定底稿；unit 2 的 heading-only marker 显式警告仍需人工复核。
- 实际中文成品 SHA-256 `C47073974F2784E0FA3D6DD2C702F1D881B9EC4B1A08BDC057D090A7867DD214`：三模式各 38 XHTML，共 114 次；各 243／243 注释与返回目标、61／61 实际往返，failures 为空。主 Agent 查看深色和 32px 代表图，按模式设置复核无正文回归回执。
- 子 Agent 的 4 组旧路径诊断已核实归属、无活动写入，移入正确 work-key scratch；只保留无正文回归回执，临时草稿、截图与解包由统一 clean 清理。其他书籍目录未动。
- 验证等级：共享生命周期为本地测试，初始化及 browser runner 为真实书籍来源／成品检查；没有品牌阅读器、书籍外部交付或生产发布验证。原书、正式底稿、STATUS、manifest、成品与既有 CSS／Lua 不改动；不调整权限设置。

## Review

独立 review pass 已通过。发现并关闭首块 marker 破坏引文／列表、同级 junction 导致越权清理两个 blocking；主 Agent 另关闭原生 img 拒绝及段落标点误判语义，均有正式 reader 或 Windows 真实回归。复核运行 17 项测试、25 个子测试和 4 项 browser 测试，source-unit／脚注／来源哈希再次核实；没有剩余 blocking。

按后续授权提交并推送本次范围；此 implemented 记录先进入 Git 历史，再删除活动 change 和对应指针，历史只从 Git 查询。
