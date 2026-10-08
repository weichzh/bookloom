# 项目工作协议

本文件独占任务分类与生命周期。默认同时最多推进一本书和一个共享变更；发现范围外问题先登记，不把当前工作无限扩张。

## 任务分类

### audit

有明确对象和停止条件的只读调查。只读取事实所有者与必要证据，默认在最终回复中报告；不修改书籍、共享实现或外部交付，也不因发现问题自动升级为完整单书工作。

### book

在既有 manifest 和共享契约内恢复、翻译、修复或验收一本书。全部进度、疑点、证据和下一步只写该书 `STATUS.md`，不创建 milestone、spec、plan、review 或 change。若需要改变跨书行为、schema、共享样式或验收门槛，把共享部分单独列为 `shared-change`。

### shared-change

改变长期规则、公共代码、schema、共享格式或验收行为。实施前必须有一份状态为 `accepted` 的 `docs/changes/<slug>.md`，并获得用户对具体范围的明确批准。

## Change 记录

状态只有 `proposed`、`accepted`、`implemented`。一份文件至少保存 Status、Problem、Scope、Acceptance Criteria、Result 和 Review；需要时再写非目标、回滚和风险。同一事实不复制到其他状态文档。

实施完成后进行一次独立 review pass。发现分为：

- blocking：违反范围、契约、数据安全或验收条件；修复后只复验相关阻塞项及必要回归。
- non-blocking：可在不影响本次验收时留作候选，不扩大冻结范围。
- out-of-scope：登记到所有者或路线图，不纳入本次变更。

所有 blocking 关闭且验收条件满足时停止审查。先以 `implemented` 记录结果和证据；获准提交并进入 Git 历史后删除 change、清空 `ACTIVE` 指针。未经授权不自动提交，保留 implemented 记录供用户审阅；Git 是唯一历史档案。

## 工站与持续改进

解析既有作品的工作型 CLI 每次调用都是一个工站；没有具体作品的共享活动使用 `station` 工站。调用者必须声明自同一 actor、session、work 的上一工站以来的活动；紧邻的纯机器步骤写 `none`。同一作品／会话 lane 使用互斥锁，不能并发入站；异常退出由操作系统释放锁。CLI 只记录 UTC 时间、相邻工站、相对文件状态/摘要和结果，不记录正文或对话内容。站间时间是人工投入上界；等待、并发和未归因变化必须单列，不能伪称专注人时。

流程监控遵循“观察 → 候选 → accepted change → 最小修复 → 代表样本复测 → review”：

- 来源或输出完整性有风险、相同失败连续两次，或失败等待超过 120 秒时，暂停当前实现并调查；
- 同类人工活动重复三次或跨两本书出现时，生成改进候选，不自动修改流程；
- 性能回归至少需要五个可比成功样本，才可按长尾趋势提出候选；
- 任何长期行为变化仍走 `shared-change`，成功率和终态验收不得因提速下降。

单书输出顺序固定为 `prepare`（AutoCorrect、lint、check）→ 目标级 `refresh` 或一次性 `finalize` → 人工把 `STATUS.md` 写为本地完成 → `complete` 收口 manifest → `deliver` 外部交付。`complete` 不替代人工验收，只在当前 final 证据与全部正式输出仍有效时原子迁移机器状态；`deliver` 要求唯一正式输出与当前 `final.json` 完全一致，以原子替换写入明确目标文件，并复核大小和 SHA-256。只有比较非字节相同的既有候选时才使用 `compare-epub`。PDF 来源渲染统一使用 `mutool`。`check` 同时拒绝可识别的 manifest／`STATUS.md` 状态漂移；LaTeX 作品声明 EPUB 输出时，还会在公式生成前运行正式 Pandoc/Lua 兼容层预检。

## 协作

主 Agent 独占 change、`ACTIVE`、共享入口和最终合入；子 Agent 只处理互不重叠的代码、测试、章节或只读 review。所有分片由主 Agent 复核，不能把子 Agent 的结论直接当作完成。

委派必须写明对象、文件边界、CWD、预算、成功条件及返回证据。子 Agent 用 `scratch` 取得 `.tmp/translator/<work-key>/scratch/<agent>/`；截图、提取、脚本和草稿均在此目录。主 Agent 收齐分片、检查实际 diff／来源／关键验证，确认所有写入者结束后统一 `clean`。不逐个用 shell 删除，不新增任意路径清理入口；终态、完成和交付回执保留在 `.local/translator/evidence/`。

当前范围已获批准时，段落、术语、内部审查和临时文件管理由 Agent 自行处理，不要求用户重复审核。发现实质范围变化或未授权外部写入才请求用户决定。安全层拒绝的操作不得换工具重试；统一入口减少重复操作，不承诺任何命令永不触发审核。

## 完成门槛

- 当前所有者文档与实际代码一致。
- 与风险相称的测试、CLI seam 或真实目标检查已经运行。
- 至少完成一次独立 review pass，blocking 已关闭。
- 触碰的中文 Markdown 通过 AutoCorrect，当前文档通过结构检查。
- `git diff --check` 通过，范围外改动与 `.tmp/` 产物已排除。
- 未运行或未覆盖的真实书籍验收明确记录，不用静态或本地证据替代。

历史只从 Git 读取；新任务不得恢复 milestone、spec、plan、review 或 archive 文档树。
