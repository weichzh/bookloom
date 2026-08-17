---
name: project-workflow
description: Use when starting, resuming, planning, reviewing, or implementing work in this repository. Loads only the fixed core and one task-specific document bundle.
---

# Project Workflow

1. 完整读取仓库 `AGENTS.md`，并严格执行其中唯一的启动顺序。
2. 从 `docs/ACTIVE.md` 命中当前工作流；没有命中时，只从 `docs/INDEX.md` 选择一个 Context Bundle。
3. 任务分类和门槛只按 `docs/workflow/PROTOCOL.md`，不要在计划或进度消息中重写规则。
4. `audit` 保持只读；`book` 只更新目标书 `STATUS.md`；`shared-change` 必须先有 accepted change 和用户明确批准。
5. 保留其他活动工作流和用户已有改动，只编辑当前所有权范围；主 Agent 复核所有并行分片。

若共享改动缺少 accepted change 或明确批准，停止生产编辑并报告缺口；不要用额外模板或临时状态文档绕过门槛。
