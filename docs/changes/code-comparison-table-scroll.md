# 代码对照表的局部横滚

## Status

implemented

## Problem

窄屏代码对照表可能把数组、切片或代码示例拆成碎片。机器无溢出仍不易读；现有 code-example 按契约只能容纳 LineBlock，不能承载原生 Table。

## Scope

新增仅用于原生代码对照表的 code-table Div 类：复用已有局部横滚和 Code 保行样式，首列最小内容宽度为 4em，说明文字仍正常换行。保持 Table／Caption／合并格／链接和代码字面，不改变 code-example／binding-example 的 LineBlock 契约，不改普通表格、行内 Code 或程序。关闭出版社样式时同类保基础滚动、等宽字体及保行。

## Acceptance Criteria

1. 正式 reader／writer 保原生 Table 及有序单元格 Code 全文；原富代码容器仍拒绝 Table。
2. 三种阅读模式下长 Code 保原行、局部可横滚、末列与末字符可达、全页不溢出；说明列正常换行且可读。
3. 普通表、普通行内 Code 和既有富代码为正常对照；只检查新类与受影响成员。
4. 触碰文件检查及一次独立 review 通过后收口。

## Result

已接入 code-table 的出版社样式及关闭出版社样式基础适配，未修改富代码解析契约。原生 reader／writer 保 Table、Caption、单元格 Code 与链接，旧富代码容器仍拒绝 Table。

原生语义测试 1 项通过；三模式浏览器与 harness 定点测试 2 项通过，覆盖窄屏保行、局部滚动后末字符完整落入全部裁切祖先及视口、半字符越界反例和宽屏不强制滚动。首列最小内容宽度为 4em，连续中文说明实际换行，普通表与行内 Code 不受影响；该值针对曾出现的 36.19px 窄列反例，不放宽溢出阈值。

真实消费者的局部检查覆盖多张表和全部相关代码对象，三模式保单行，EPUBCheck 通过；之后的大字号 reader 模式复验了说明换行及末列完整可达，未重跑其他模式或全章。

## Review

独立 review 发现 reader 的 keep-all 阻止说明首列中文换行，以及末字符只以相交而非完整包含证明可达。补充局部 normal 覆盖、实际换行、完整裁切链和半字符越界断言后，两项 blocking 经定点复验关闭。
