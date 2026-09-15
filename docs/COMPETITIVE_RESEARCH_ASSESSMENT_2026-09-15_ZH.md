# 竞品分析项目优势与改进评估

> 历史评估，描述改造前的状态。当前实现与剩余限制请查阅 [文档目录](README.md)。

评估日期：2026-09-15。

范围：当前仓库 Competitive Research 子系统的代码、测试和已有连调记录。未进行外部竞品市场排名，未调用付费模型或搜索服务，未验证实际生产集群。本文为评估记录，不改变业务实现。

## 一、总体判断

项目已经具备结构化竞品研究产品的基础：证据快照、事实结论、语义审计、持久化编排、人工审批和报告导出均有实现。适合继续开展有人工审核的受控试点；现有证据不足以认定已经达到稳定的生产交付标准。

最值得继续投入的能力是“结论可追溯、执行可恢复、质量可验证”。当前最优先的工作是把这些约束贯穿最终报告，并通过真实执行证明效果。

## 二、已有优势

| 优势 | 实现依据 | 实际价值 |
| --- | --- | --- |
| 证据和结论结构化 | `contracts.py`、`evidence_validation.py`、`repository.py` 中包含 Snapshot、逐字引文、Hash、Offset、Claim 与语义支持状态 | 可以定位结论依据，保留不确定性，便于复核 |
| 工作流由服务端控制 | `state_machine.py`、`orchestrator.py`、`orchestration_repository.py` | Agent 提交数据，阶段推进有确定性规则，支持独立任务失败和恢复 |
| 人工审核有明确入口 | 开题审批、发布审批、退回返工 API 和独立工作台 | 在采集前校正范围，在对外交付前确认内容 |
| 定价有专门数据模型 | `PriceObservationCreate` 与定价校验 | 能区分币种、计费周期、席位、官方来源及第三方估计，减少常见价格误读 |
| 生产基础设施有约束 | `production.py`、Provider 校验、S3 Storage、生产 Compose | 已考虑持久化、运行恢复和部署条件；配置检查仍需配合运行验收 |
| 已有针对性测试 | 引文、预算、恢复、存储、检索、领域隔离等测试文件 | 为继续加固提供可回归的工程基础 |

以上是当前实现的优势，不代表已通过与其他商业产品的同题实测。

## 三、改进优先级

### P0：把事实校验延伸到报告正文

`orchestrator.py::_validate_report_submission` 检查 11 类章节是否齐全，并筛选合法 Claim/Evidence ID，但允许空引用和空正文，也没有验证 Markdown 中每条事实是否由所关联的已审计 Claim 支持。非法引用会被过滤，而不是使提交失败。

Synthesis 提示中要求不得把不确定结论升级为事实，但这项要求尚未成为正文级硬校验。`repository.py::approve_report` 主要验证归属、报告版本存在和状态迁移，没有再次执行正文质量门禁。

影响：上游 Claim 即使经过严格审计，报告合成仍可能新增无依据的数字或把不确定结论写成确定事实。这是当前最应优先闭合的质量边界。

建议：引入段落级结构化内容，将事实、推断、建议、未知项明确区分；事实绑定已审计 Claim，数字和价格从结构化数据渲染；缺失或无效引用直接拒绝；最终正文发生事实改写时再次审计。发布前校验同一报告版本的质量结果，部分报告必须显式保留未完成项。

验收：构造“章节齐全但无引用”“引用正确但正文捏造数字”“把 uncertain 改写为确定事实”等提交，均不能作为完整可信报告通过。

### P0：用真实模型评测证明研究质量

当前黄金集有 12 个案例。`test_investigation_golden_evaluation.py` 验证样本类别、规则门禁和评分器；评分器测试以标准标签构造预测，再人为修改一个错误。它没有运行真实模型来测量语义判断准确率。

`test_investigation_durable_e2e.py` 使用 `ScriptedProviders`、`ScriptedRuns`、`ScriptedBatches`。这能证明领域工作流衔接，但不能证明真实搜索、模型输出和生产基础设施下的完整成功率。

建议：建立版本固定的模型评测流水线，保存实际预测、引用、模型配置、输入快照、耗时、Token 和失败原因。先扩充一套人工标注的跨场景样本，再持续加入真实失败案例。分别统计完整交付率、部分报告率、事实支持准确率、漏报率、单份合格报告成本和 P95 耗时。

已有文档中的准确率与覆盖率指标应作为验收目标，不应作为已取得的结果。

### P0：验证全流程预算约束和真实闭环

已有 B站案例记录 14 条 Evidence、16 条 Claim，完整 Audit 未完成，Token Ledger 为 569,726 / 450,000，应用交付为 `partial=true`。这是历史连调结果，不能直接视为当前修复后仍会发生同样的问题。

当前代码已隔离 StageTask Thread，并为 Batch Item 设置 Token 上限；但编排的调查总预算主要在阶段启动前估算、在 Receipt 返回后记账。普通 Run Adapter 未见把调查剩余预算显式传入启动调用，不能仅凭预算账本测试认定全局硬上限已成立。

建议：以调查剩余预算作为统一额度，在执行前预留、执行后结算，把重试和并发任务纳入同一账本；每次模型调用前检查剩余额度，并为最终响应预留合理超调空间。对正在执行的 Run 验证耗尽后的取消和费用归集。

验收应覆盖：严格搜索 Provider 的真实完整报告、429/超时、Gateway 重启、Worker 中断、预算耗尽，以及 PostgreSQL/Redis/S3 实际环境中的恢复。记录修复后的完整 Audit 和完整报告成功样本。

### P1：从域名数量升级为来源独立性与时效判断

`scoring.py::independent_source_count` 使用简化的可注册域名规则，仅列举少量复合后缀。它衡量域名多样性，不能充分识别同一原始稿件的跨站转载、同一发布主体的多域名内容。

此外，`orchestrator.py::_apply_evidence` 的 freshness 当前按“是否带发布日期”赋分，并未根据日期距今多久计算；specificity 固定为 7，corroboration 初始化为 0。因此当前分数应理解为启发式质量提示，不宜表达为经过校准的可信概率。

建议：使用完整公共后缀规则，记录原始发布方和转载关系，结合近重复正文聚类计算独立来源；按价格、功能、公司背景等信息类型设置不同有效期。区分“官方一手来源已确认”和“多个独立来源已交叉验证”。

### P1：按竞品和维度验收覆盖率

采集阶段当前以成功任务比例和证据总数作为门槛：至少约 60% Item 成功，且证据总数不低于竞品数量的两倍。总量达标不保证每个竞品、每个关键维度都具备足够证据。

建议：建立“竞品 × 维度”的覆盖矩阵，每格记录支持结论、证据、时间、缺口及下一步采集动作。按缺口定向补采，避免资料多的竞品掩盖资料少的竞品。实体匹配应结合官方域名、别名和产品版本进行验证。

### P1：让工作台直接服务产品决策

当前详情页已经可以查看阶段、证据、Claim、审计问题和价格，但报告主体用 `<pre>` 展示 Markdown 原文；PDF 转换仅支持基础标题、列表和段落，表格和引用尚未形成完整的排版能力。

建议：优先增加竞品对比矩阵、关键差异、证据缺口，以及从结论直达来源和原文位置的交互。报告使用真正的 Markdown 渲染和可追溯引用，PDF 支持表格与分页。机会建议应明确目标用户、依据、验证动作和优先级。

详情页每三秒重新请求调查及六类附属数据，完成后仍持续轮询。应增加终态停止、隐藏页面暂停、增量获取和请求取消。此次在 `frontend/tests` 中按 investigation 搜索未发现专属测试，应补审批、返工、部分报告提示与轮询行为测试。

### P2：建立持续情报资产与团队协作

当前领域契约明确定位为单用户试点，organization_id 为预留字段。调查子系统中未看到完整的竞品订阅、快照差异、变更通知和团队审阅闭环；底层通用 Scheduler 并不自动构成这些产品能力。

建议在完整报告质量稳定后，增加持续竞品档案、版本比较、价格和功能变更提醒，再完善组织权限、共享审批、评论、配额与审计留存。

## 四、推荐实施顺序

1. 先修报告正文事实约束与发布质量门禁。
2. 建立真实模型评测，并跑通严格 Provider 的完整研究样本。
3. 验证统一预算、故障恢复和生产基础设施。
4. 完善来源独立性、时效性和逐格覆盖率。
5. 改善矩阵、报告阅读和引用导航，随后扩展持续监控与团队协作。

## 五、本次验证

执行六个测试文件：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_investigation_evidence_validation.py tests/test_investigation_golden_evaluation.py tests/test_investigation_budget.py tests/test_investigation_durable_e2e.py tests/test_investigation_production_config.py tests/test_investigations_domain.py -q -p no:cacheprovider
```

结果：**21 passed in 3.33s**。首次沙箱内执行因 pytest 临时目录权限出现 6 个 setup errors；获准在沙箱外重跑后全部通过。这些检查不代表完整后端套件、前端交互测试、真实模型评测或生产部署验收通过。

文档一致性也需维护：V1 Spec 仍写固定 300K Token，而当前创建逻辑按竞品数设置 300K–525K；编排说明仍写按竞品和维度展开 Collector，当前实现已调整为每竞品一个 Collector。建议随下一次对应实现更新同步修订，避免错误容量预期。

## 六、主要依据

- [V1 规格](COMPETITIVE_RESEARCH_V1_SPEC.md)
- [V1 完成与连调加固说明](COMPETITIVE_RESEARCH_V1_COMPLETION_ZH.md)
- [B站案例的真实运行记录](BILIBILI_PLAYER_COMPETITIVE_RESEARCH_ZH.md)
- [编排与报告校验](../backend/app/investigations/orchestrator.py)
- [领域持久化、预算和审批](../backend/app/investigations/repository.py)
- [来源评分](../backend/app/investigations/scoring.py)
- [普通 Run 执行适配](../backend/app/investigations/run_adapter.py)
- [黄金集测试](../backend/tests/test_investigation_golden_evaluation.py)
- [脚本化端到端测试](../backend/tests/test_investigation_durable_e2e.py)
- [详情工作台](../frontend/src/app/workspace/investigations/%5Bid%5D/page.tsx)
- [PDF 渲染](../backend/app/investigations/exports.py)
