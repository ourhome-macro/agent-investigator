# 文档目录

从 [项目首页](../README.md) 了解 Agent Investigator；下面按用途查阅。

## 使用与运行

| 文档 | 用途 |
| --- | --- |
| [Windows PowerShell 启动](COMPETITIVE_RESEARCH_WINDOWS_SETUP_ZH.md) | 首次配置、安装依赖、双终端启动和常见问题 |
| [DeerFlow 通用运行参考](../DEERFLOW_REFERENCE.md) | 标准启动、容器部署、沙箱、记忆、技能和渠道 |
| [安装说明](../Install.md) | 继承的基础环境准备流程；本仓库地址以首页为准 |
| [后端配置说明](../backend/docs/CONFIGURATION.md) | 数据库、模型和基础设施配置 |

## 当前规则与架构

以下文档描述当前实现；历史分析中的建议可能已被后续规则替代。

| 文档 | 负责的内容 |
| --- | --- |
| [竞品研究规格](COMPETITIVE_RESEARCH_V1_SPEC.md) | 产品范围、证据约束、完成条件、预算与迁移 |
| [编排与恢复](COMPETITIVE_RESEARCH_MULTI_AGENT_ORCHESTRATION.md) | 阶段执行、审计动作、提交协议和恢复 |
| [来源分级与工作台](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md) | 官方单来源、厂商自述、用户反馈、必答项目与界面解释 |
| [总体架构](ARCHITECTURE.md) | DeerFlow 全栈与运行时背景；研究子系统以以上文档为准 |
| [根目录开发约定](../AGENTS.md) | 仓库地图与跨模块规则 |
| [后端约定](../backend/AGENTS.md) / [前端约定](../frontend/AGENTS.md) | 模块结构、测试与开发规范 |

## 验证与变更记录

这些记录说明某次检查实际做了什么，不等同于持续生产验收。

- [研究闭环改造记录](COMPETITIVE_RESEARCH_CONTROL_LOOP_IMPLEMENTATION_ZH.md)：候选准入、审计驱动修订、预算预留、历史材料只读重放。
- [来源分级与界面验证](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md#迁移与验证)：当前策略对应的回归结果与环境限制。
- [本次提交说明](CHANGE_RECORD_2026-09-15_ZH.md)：提交范围、文档整理方式与推送前检查。
- [数据库前向版本恢复](database-forward-revision-recovery.md)：通用数据库运维背景。
- [Git 仓库初始化](GIT_REPOSITORY_INITIALIZATION.md)：仓库来源、忽略规则与提交卫生。

## 历史案例、评估与方案

按研究过程保留以下文件，避免丢失决策依据。它们不覆盖当前规格和来源规则。

1. [B站播放器案例](BILIBILI_PLAYER_COMPETITIVE_RESEARCH_ZH.md) 与 [案例输入](BILIBILI_PLAYER_CASE_INPUT.json)：真实历史运行未完成 Audit，部分结果与独立研究材料需区分阅读。
2. [初期 V1 实现说明](COMPETITIVE_RESEARCH_V1_COMPLETION_ZH.md)：早期门槛、开发记录和评测目标，部分策略已被后续实现替代。
3. [项目优势与改进评估](COMPETITIVE_RESEARCH_ASSESSMENT_2026-09-15_ZH.md)：改造前的评估。
4. [研究闭环问题复核](COMPETITIVE_RESEARCH_CONTROL_LOOP_REVIEW_2026-09-15_ZH.md)：B站案例对应的代码原因。
5. [下一轮实施建议](COMPETITIVE_RESEARCH_NEXT_ITERATION_2026-09-15_ZH.md)：随后改造采用的计划，具体完成情况看变更记录。
6. [置信度门槛复核](COMPETITIVE_RESEARCH_CONFIDENCE_POLICY_REVIEW_ZH.md)：来源分级调整的决策依据。

`plans/`、`superpowers/` 与 `agents/` 保留既有通用运行时方案；`pr-evidence/` 保存历史检查材料。工作区中被 Git 忽略的本机配置、日志、源码启动笔记不属于公开文档集。

## 文档维护约定

- 产品行为写入首页与当前规格；内部协议写入对应架构或模块约定。
- 测试结果注明执行范围和限制，不把规则测试称为真实模型评测。
- 历史记录保留日期和适用阶段，不继续向旧计划叠加当前操作说明。
- 新增文档从本目录建立入口。保留现有文件路径，已有引用可继续访问。
- 凭据、数据库、完整 Provider 请求与运行快照保留在忽略目录，不纳入文档提交。
