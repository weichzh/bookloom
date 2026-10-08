---
name: project-workflow
description: Use when starting, resuming, planning, reviewing, or implementing work in this repository. Loads only the fixed core and one task-specific document bundle.
---

# Project Workflow

1. 使用本仓库 skill 和 `docs/workflow/PROTOCOL.md` 路由。Codex 已加载的 `AGENTS.md` 不为确认而重读；补读 CONTEXT、ACTIVE 及匹配 bundle。
2. 从 `docs/ACTIVE.md` 命中当前工作流；没有命中时，只从 `docs/INDEX.md` 选择一个 Context Bundle。
3. 任务分类和门槛只按 `docs/workflow/PROTOCOL.md`，不要在计划或进度消息中重写规则。
4. `audit` 保持只读；`book` 只更新目标书 `STATUS.md`；`shared-change` 必须先有 accepted change 和用户明确批准。
5. 保留其他活动工作流和用户已有改动，只编辑当前所有权范围；主 Agent 复核所有并行分片。
6. 工站和验证使用 `uv run python tools/translator.py`。本仓库不使用通用 project-workflow.py inspect，不为通用模板新增第二份流程契约。
7. 用 `scratch WORK --agent NAME --activity ...` 取得子 Agent 工作目录。子 Agent 不逐次清理分片；主 Agent 在验收和已授权交付结束后统一 `clean`。先核对是否仍有活跃写入者。
8. 内容与代码审查由主 Agent 和独立 reviewer 完成；当前范围已获授权时不再逐段向用户请求审核。外部交付、范围扩展或安全工具拒绝仍按用户授权边界处理，不换工具绕过。

若共享改动缺少 accepted change 或明确批准，停止生产编辑并报告缺口；不要用额外模板或临时状态文档绕过门槛。
