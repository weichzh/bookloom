---
description: 统一 Bookloom 的许可证、跨平台入口、持续集成和 EPUBCheck 依赖方式。
---

# 跨平台发布与依赖整理

## Status

implemented

## Problem

公开仓库缺少许可证、跨平台安装说明和自动验证，项目元数据仍使用旧名称；EPUBCheck 以 36 MB 发行包直接进入主仓库，难以追踪来源和升级。

## Scope

- 添加以 `weichzh` 为版权人的 MIT 许可证。
- 将项目元数据改名为 `bookloom`，并同步锁文件。
- 统一 Windows、macOS 和 Linux 可执行的 Python 入口，补充三平台 CI。
- 用固定版本的官方 `w3c/epubcheck` Git submodule 替换内置发行包，并提供最小的跨平台构建和定位方式。
- 补齐克隆、安装、外部依赖、子模块、测试和书籍隐私边界文档。
- 删除根目录三个误生成文件及其专用忽略规则。

不打包或公开 `Books/`、`Works/`，不发布 PyPI 包，不改动书籍内容。

## Acceptance Criteria

- 根目录包含标准 MIT `LICENSE`；`pyproject.toml` 与 `uv.lock` 的项目名均为 `bookloom`。
- `tools/epubcheck` 是固定提交的官方子模块，父仓库不再保存其 JAR；未初始化或未构建时给出明确操作提示。
- README 中的主入口和辅助脚本可在 PowerShell、bash 与 zsh 下按相同参数运行。
- GitHub Actions 在 Windows、macOS 和 Linux 上执行锁文件检查、文档检查和测试。
- 当前 Windows 环境全量测试通过；干净检出验证不依赖本机 `Works/`。

## Result

- 项目元数据和锁文件已统一为 `bookloom`，根目录使用 MIT 许可证。
- 主入口统一为显式 Python 调用；三平台 GitHub Actions 执行锁文件、依赖准备、文档与完整测试。
- 官方 EPUBCheck v5.3.0 源码固定为子模块；标准库安装脚本验证官方发行包 SHA-256，运行文件不进入父仓库。
- README 和现有事实所有者已补充跨平台安装、依赖、命令、验证与隐私边界。
- Windows 本地验证包括 EPUBCheck 真实启动，以及 117 项测试和 28 个子测试；未使用或修改 `Books/`、`Works/`。

## Review

- Spec 轴无发现：许可证、改名、三平台 CI、子模块、文档、隐私边界和误生成文件清理均符合范围。
- Standards 轴发现并阻断了 `target` 链接可能导致安装清理越界的问题；`a8f3354` 增加解析后 containment、链接拒绝和回归测试，复核确认 blocking 已关闭。
- 没有 non-blocking 或 out-of-scope 发现。
