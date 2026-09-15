# VerdaAI-Investigator 源码对照笔记

评估日期：2026-09-15。

对象：[kangjiayao14/VerdaAI-Investigator](https://github.com/kangjiayao14/VerdaAI-Investigator)，固定提交 `c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56`。

范围：README、后端编排/审计/结构化数据/指标、前端报告和批注代码的静态阅读。没有运行该项目、调用其 Provider 或测量真实报告质量；本次没有引入其源代码。

## 判断

值得学习，主要价值在产品层：把用户的决策目标、研究深度、章节结构和报告交互连接起来。其“48 位专家”等描述不能单独证明研究质量或运行可靠性。对我们最有价值的是改进用户组织研究和消费结果的方式。

## 可以借鉴的设计

| 设计 | 代码中的实现 | 对本项目的建议 |
| --- | --- | --- |
| 研究模式 | `MODE_CONFIG` 联动搜索角度、抓取数量、章节、输出额度和返工轮数 | 把快速了解、常规研究、深入研究定义成服务端资源策略，展示预计范围；各模式共用事实校验 |
| 面向使用者的报告 | 根据产品、运营、销售、用户等视角加入专属章节 | 在开题时询问决策目标，以同一事实集合输出功能优先级、销售对比或用户选择建议 |
| 业务结构化视图 | 功能树、套餐表、用户画像各有数据整理函数和 React 组件 | 将现有基础矩阵扩展为可筛选的功能对比，保留未知、版本条件和逐项来源；完善画像中的需求、场景和迁移成本 |
| 章节职责 | 按章节选择相关 Claim，返回核心判断和重点发现，逐章更新进度 | 保留我们的事实渲染门禁，同时让每章直接回答一个业务问题；判断与建议继续标明依据 |
| 段落批注入口 | 报告页收集选中文字与批注，并调用章节更新接口 | 将用户对某一句的疑问绑定到具体 Claim/版本，驱动补采或修订，再审计后更新 |
| 来源定位与过程回看 | 报告内跳转并高亮证据，按阶段回看 Trace | 用“发现了什么、改了哪条结论、为什么改”组织时间线，原始协议详情留在诊断层 |

依据：

- [模式、章节分配与视角映射](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/core/orchestrator.py)
- [业务对象整理](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/core/schemas.py)
- [结构化视图组件](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/frontend/src/components/VStructured.tsx)
- [报告页交互](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/frontend/src/pages/ReportPage.tsx)
- [过程回看组件](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/frontend/src/components/VDecisionReplay.tsx)

## 不应直接沿用的实现

### 批注深化目前是重写

`refine_section` 将现有章节、已有证据摘要和用户批注交给模型，然后保存新段落。本函数没有新增检索、Claim 修订或重新审计步骤。可以借鉴精确到段落的入口，但不能把这条实现当作已完成的二次研究闭环。

### 质量指标有明显的口径问题

`evaluate_quality` 在某维度未匹配时，只要存在其他有效 Claim 就可能将它算作已覆盖。`make_claim` 根据引用数量和域名数量分级；`metrics.py` 又把高置信 Claim 占比称为 accuracy。这不是经过人工标注验证的事实正确率。

见 [审计逻辑](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/core/audit.py)、[Claim 分级](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/core/models.py) 和 [指标计算](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/core/metrics.py)。

### 结构完整不等于事实可靠

结构整理会过滤无效引用 ID，但仍允许引用为空的条目。定价缺失时会填默认币种和周期；功能状态没有独立的未知项。我们的实现应继续保留未知值，并验证金额、周期与原文，避免默认值变成事实。

此外，当前编排先审计分析中间结果，再自由生成章节后保存报告；源码中未看到该主路径对新生成正文再次执行逐条事实门禁。

### 运行方式不适合替换我们的持久任务基础

其 SSE 入口直接遍历 `run_pipeline`，检测连接断开后结束迭代。持久化报告不等于独立后台任务的租约、恢复和幂等执行。我们的运行基础应继续采用已有持久任务和预算约束。

依据：[SSE 入口](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/backend/app/main.py)。

## 建议优先级

1. 先完善报告的信息组织：业务视角、核心结论、功能/价格对比及可点击原文。
2. 再做段落批注驱动的定向补研，接到我们现有审计动作和版本关系。
3. 最后开放研究深度档位，把额度、覆盖目标、预计交付范围作为统一配置。

以上是源码阅读后的设计建议，不是双方同题实测排名。对方仓库声明采用 [AGPL-3.0](https://github.com/kangjiayao14/VerdaAI-Investigator/blob/c6d6bfbe4da75bb506fa1de7f742d9db5e92dc56/LICENSE)；后续若涉及源码复用，需单独核对许可条件。
