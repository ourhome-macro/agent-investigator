# B站播放器案例：Research Control Loop 复核

> 历史诊断，供追溯改造原因。当前状态以 [现行规格](COMPETITIVE_RESEARCH_V1_SPEC.md) 和 [验证记录](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md) 为准。

日期：2026-09-15。范围：针对用户提出的八项问题核查当前代码与已有运行记录；本次不修改业务代码，不重新发起真实模型研究。

## 结论

用户的主要判断成立：该次运行应归类为研究未完成后生成部分结果，不能当作完整竞品研究成功案例。工程瓶颈集中在证据准入、覆盖率驱动、审计动作执行和最终交付门禁。

更精确地说：当前已有持久化编排闭环和有限的审计补搜闭环，但还没有建立完整的研究质量闭环。模型能指出问题，不等于系统会执行修订、验证修订效果并阻止问题进入报告。

本文没有重新读取运行数据库逐条统计噪声，因此用户提出的“至少 8/14 条无关”和具体 Claim 示例作为运行观察保留，不把它们包装为本次独立重测结果。

## 八项问题的代码核对

### 1. 检索污染：成立，付费搜索不能替代准入门禁

- `_collect` 每个竞品发起一条拼接多个维度和时间范围的查询，返回最多 10 个候选；这并非逐个研究问题检索。
- `ResearchProviderRegistry.search` 合并 Provider 结果后按 URL 去重、按返回顺序截断，未见实体和研究问题相关性重排。
- Collector 提示要求只选择搜索候选，但 `_apply_evidence` 没有将提交 URL 与任务候选集合逐一强校验，也没有服务端实体相关性准入。
- `_apply_evidence` 验证抓取和引文，随后绑定任务的 competitor_id；正文确实存在不代表正文属于目标竞品。

因此问题并不限于免费 Provider。必须把“可读取”“相关”“来源合格”“可用于支持某类结论”作为不同条件。

### 2. Coverage 缺口：成立，但并非完全没有补搜

当前 `_rework` 已经针对 Audit Issue 搜索、补证据和再次审计。限制是：它只选择前 20 个 Issue 中绑定到已有 Claim 的项目，没有 Claim 的竞品或维度缺口不会生成任务。

采集门槛是约 60% Item 成功、证据总数至少竞品数的两倍。分析门槛也是约 60% Item 成功且至少生成一个 Claim。它们不验证每个竞品和关键维度的覆盖。

应增加独立于 Claim 的 CoverageCell/ResearchQuestion，允许某格没有任何 Claim 时仍驱动采集。每格记录证据要求、当前缺口和尝试历史。

### 3. Atomic Claim：成立，应保留事实与推断的区别

`ClaimCreate` 主要是 text、dimension、material、claim_type 和 evidence_bindings。没有将主体、单一谓词、适用条件、时间和版本作为明确结构约束。逐字引用和数字检查无法阻止非数字的语义扩张。

建议事实结论采用单一可验证命题，并让跨产品比较引用多个原子事实。推断可以保留，但必须标识为推断、绑定前提，不得继承事实结论的 supported 标签。

### 4. Audit 没有充分驱动事实修订：核心问题成立

需要精确区分两个实现：

- `apply_audit_verdicts` 会写入绑定的 entails/contradicts 等结果，并重算 Claim 状态，不能说审计完全不修改图谱。
- `replace_audit_issues` 记录 semantic-support、source-independence 等问题，但这些开放 Issue 不参与上述 Claim 状态重算。因而“Claim supported，同时有未解决的来源或语义问题”在架构上可能出现。

进一步发现：

1. `_rework` 将各类 Issue 都交给 Collector 补证据，没有 revise/split/reject 的动作分派。
2. 补采后执行状态从 reworking 切到 analyzing，再立即切到 auditing，中间没有调用 `_analyze`；状态名不代表实际运行过修订分析。
3. `supplement_claim_evidence` 为新链接统一设置 supports/pending_audit；缺少冲突证据作为独立调查分支的建模。
4. `_rework` 的成功条件是至少一个 Receipt 成功；即使 accepted_evidence 为零也可能完成返工阶段。
5. `replace_audit_issues` 先将上一轮所有开放问题标为 resolved，再保存新问题；审计输出遗漏旧问题也可能表现为已解决，未要求逐项提供解决依据。

应将 AuditIssue 转为类型化动作，绑定 Claim 版本；动作执行和复核完成后才关闭 Issue。Claim 应保留修订历史，通过 superseded/rejected 从最终可用集合排除，而非物理删除审计记录。即使已有 version 字段，也仍需建立实际修订协议和版本关系。

### 5. 指定工具调用导致失败：历史问题已有部分修复

当前普通 Run 的 `_execute_run_task` 和 Batch Adapter 都支持合法最终 DomainSubmission 经服务端验证后持久化，因此“忘调工具必然整轮失败”已不是准确的当前描述。

但普通 Run 在没有可用提交时仍将错误统一覆盖为 `Agent did not use its required domain submission tool`，可能掩盖原来的超时、模型失败或 JSON 解析原因。

建议保留统一的 Runtime 提交边界与现有 owner/stage/task/idempotency 校验，按失败原因区分提交协议修复、模型执行重试和业务返工。协议无效不应直接重跑整轮研究。提交已接受与 Run 最终结束应分别记录。

### 6. 预算：历史超额成立，当前不是完全没有控制

已有记录的 569,726 / 450,000 相当于约 26.6% 超额。当前已有阶段前估算、80% 早期预算限制、Batch Item 30K/35K 上限、截止时间和账本，不能概括为纯显示型预算；但这些并不能自动组成全调查的强额度约束。

固定的阶段百分比可以作为初始配置，不应直接视为最优方案。预算应随竞品数、Coverage 缺口、输入规模和模型费用调整。核心是全局预留/结算、在途执行额度、重试成本、每次模型调用前约束，并为 Audit 和最终交付保留额度。

当前 Batch Item 已改为 max_turns=20；继续单纯增加轮数不能解决准入污染和协议错误。

### 7. 重复报告：已定位到失败路径，不能混同正常 Synthesis

`partial_report.py::build_partial_report` 在章节循环中默认 `section_claims = claims`，仅对摘要、方法、风险、附录单独处理。剩余七个业务章节会遍历同一组 Claim，直接解释“赛道、画像、功能矩阵、定价等重复全文”的现象。

正常 `_synthesize` 使用独立 Report Editor Run，并非这一确定性复制函数。该次 Report Editor 未完成，不能用部分报告证明正常 Synthesis 也在机械复制。

但正常报告也存在独立问题：正文事实和引用关联没有强校验。因此需要同时改进两条路径：

- 未完成研究输出简明的研究状态、已验证发现、缺口与后续动作，明确标为未完成，不能用 11 个重复章节制造完整交付的外观。
- 完整报告仅消费经版本冻结和资格校验的最终 Claim 集合，按章节路由、去重和合成，矩阵由结构化数据渲染。

### 8. 来源质量：成立，但 Source Tier 也必须结合事实类型

`classify_source_type` 主要依靠主机名和 URL 路径识别 GitHub、pricing、documentation 等类别；`_apply_evidence` 根据类别赋权。价格路径不等于官方来源，GitHub URL 也不等于官方仓库或维护者确认。

freshness 按是否存在发布日期赋分；specificity 固定为 7；corroboration 初始为 0。当前分数是粗粒度启发式，不应被当作校准后的可信概率。

应验证 publisher/官方归属、作者身份、文档类型、发布日期及证据适用范围。官方 Release 可证明已发布能力，普通 Issue 可证明用户报告过问题，但不能自动证明问题普遍存在。相关性、来源权威性和语义支持必须分别判断。

## 建议的最小工程闭环

```text
Coverage Gap → Targeted Query → Candidate Admission → Evidence
                                                     ↓
                                               Atomic Claim vN
                                                     ↓
                                                Audit Decision
                           ┌─────────────────────────┼─────────────────────┐
                        recollect                revise / split       reject / qualify
                           └─────────────────────────┼─────────────────────┘
                                                 Re-audit
                                                     ↓
                              Eligible Claim Set → Section Synthesis → Publication Gate
```

关键不变量：

1. 已验证引文不等于已验证结论；候选材料不自动成为可引用证据。
2. 没有 Claim 的 Coverage 缺口也能生成研究动作。
3. 有阻断性开放 Issue 的 Claim 不得进入完整报告的事实集合。
4. 旧 Issue 只有明确复核结果才能关闭，不能因下一轮没提到就解决。
5. 返工成功须体现问题解决或缺口减少；执行成功且零新增证据不能冒充研究进展。
6. 任何重试都纳入预算；没有可执行额度时停止，不把部分结果包装成完整研究。

## 实施优先顺序

先补“候选准入 + 逐格 Coverage”，保证研究输入；同时阻止含阻断问题的 Claim 晋级。随后实现类型化审计动作与 Claim 修订/复核，完成后再接章节合成。预算和故障归因贯穿上述各阶段。

这比继续增加 Agent 数量或单纯放大 max_turns 更直接对应本案例暴露的原因。

## 本次检查

四个相关测试文件执行结果：**9 passed in 2.24s**。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_investigation_partial_report.py tests/test_investigation_orchestration.py tests/test_investigation_batch_adapter.py tests/test_investigation_run_adapter.py -q -p no:cacheprovider
```

其中部分报告测试只要求 partial 标记、11 章节、不确定标签和来源链接，没有检测章节重复。这说明现有测试可以通过，同时产品交付质量仍不达标。本次未新增业务测试、未修改实现、未重新运行真实 Provider。

主要代码依据：`backend/app/investigations/` 下的 `orchestrator.py`、`repository.py`、`partial_report.py`、`providers.py`、`batch_adapter.py`、`run_adapter.py`、`contracts.py` 和 `persistence/models.py`。
