# 竞品研究闭环改造记录

> 本文记录 ci_0010 阶段的改造与验收。来源分级、报告完成条件和工作台措辞随后由 ci_0011 调整，当前规则见 [来源分级与工作台](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md)。下面的测试数量对应当时执行范围。

日期：2026-09-15。状态：代码改造与离线验证完成；严格 Provider 的真实研究及生产集群验收尚未完成。

## 已实现

- 搜索按竞品和维度生成查询，候选 URL、实体和问题相关性在接纳时校验，决策留存到领域事件。
- 采集材料必须取得正文且逐字引文匹配；搜索摘要不再进入新采集的事实证据集合。
- 分析按竞品隔离检索上下文，提交结构化原子命题；审计要求明确检查原子性。
- 审计按最多四条 Claim 分块，普通 Run 的三次尝试按任务分别计数；重启时恢复持久化输入，避免重建提示导致重复执行。
- Coverage 包含完全没有 Claim 的研究单元，缺口触发补采。
- 审计旧问题保留，明确复核才能关闭；revise/split/reject 执行后保留历史及替代关系事件。
- Report Editor 选择 Claim 与机会假设；事实正文和矩阵由服务端渲染，部分报告不再重复十一章节。
- 新增 ci_0010：预算预留表、候选快照表、Claim 提交幂等映射及调查 token_reserved；普通 Run 与 Batch 使用调用前预算检查。
- 前端展示覆盖状态、结论可发布资格、部分结果标记，并渲染 Markdown；PDF 支持矩阵表格。

## 验证基线

改造前全量普通套件在 Windows 上因 `tests/test_acceptance_checks.py` 的 `os.geteuid` 收集失败。严格 I/O 套件基线为 115 passed / 5 failed：三项 POSIX 文件权限、一项 Lark CLI 调用及一项平台不支持的技能导出。

最终相关后端回归：**373 passed in 16.84s**。覆盖调查子系统、预算中间件、Gateway 服务、Subagent Executor/Batch Service 和 harness 边界。

前端：**3 项测试通过**，包含工作台 DOM 测试（部分发布、阻断结论和已验证计数）；`pnpm check` 的 ESLint 与 TypeScript 检查通过。改动 Python 文件的 Ruff lint/format 与 `git diff --check` 通过。

改造后重新执行全量普通套件和严格 I/O 套件：仍分别遇到上述 Windows 收集问题和同样的 5 项失败，未宣称全库测试通过。

完整离线工作流包括四个场景：正常两竞品、首轮缺失一个竞品后自动补采、五竞品分块审计，以及过度概括结论经 revise 替代并重新审计。均验证完整报告、实际矩阵、预算未超额且预留额度已结算。另测试并发预算、旧问题漏报、拒绝结论不能复活、发布资格、独立重试计数及 ci_0009 → ci_0010 保留旧调查升级。

本地 `.env` 与当前进程未提供 Bocha/Tavily/Jina/语义 Embedding 凭据，尚未执行严格 Provider 的真实研究验收。不将离线脚本化测试标为真实模型成功案例。

## 关键行为与限制

候选准入的实体与维度词匹配是保守的输入门槛，不能替代语义审计。来源时间元数据未经确认时保留未知，不使用模型自填日期加分。

研究 Batch 禁用隐藏的自动执行重试，恢复/返工必须重新取得调查额度；普通 Run 保留最多三次尝试。调用前预算按文本请求 UTF-8 字节保守估计输入，限制最大输出；无法取得实际 usage 的调用保留额度，不视作免费。

真实 Provider 的准确率、召回率、单份合格报告费用和跨进程故障恢复仍须在配置完善后验收。

## 原始 B站记录重放

对 `8d3758454f7f48cfa87cb1b5b102d8c9` 的 SQLite 记录执行只读检查，确认原始数据为 14 条 Evidence、569,726 / 450,000 Token、`partial=true`。新准入门槛对保留摘要的重放结果为 **8 / 14 拒绝**。

此结果验证了当前门槛能够拦截该记录中的主要噪声，但不是人工标注准确率，也不表示其余六条已获得语义支持。原始调查、Claim、报告和生产数据库均未被本次重放改写。SQL 读取采用 `mode=ro`。

本地结果保存在忽略目录：

- `logs/research-control-loop/historical-replay.json`
- `logs/research-control-loop/preflight.json`

重放结果清单 SHA-256：`c0fe184c7a8142816d33d062cfe3a38854141e8a1d3e28f86a27155b897d3b3e`。

可复现命令（在 backend 目录执行）：

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark.competitive_research preflight
.\.venv\Scripts\python.exe -m scripts.benchmark.competitive_research inspect-recording --database .deer-flow/data/deerflow.db --investigation-id 8d3758454f7f48cfa87cb1b5b102d8c9 --output ../logs/research-control-loop/historical-replay.json
```

`preflight` 仅检查配置存在性，不声称 Provider 连通；缺少配置时返回非零。历史检查器也不调用搜索或模型。

## 生效与剩余验收条件

Gateway 下次启动会自动执行 ci_0010；本轮没有主动重启或部署服务。旧调查保留，新调查标记 `competitive-research-v2`。旧的完整报告需要重新生成才能通过新发布门禁；历史部分报告仍保持其部分结果身份。

继续真实验收需要在忽略的 `.env` 配置 Bocha 或 Tavily、Jina，以及语义 Embedding 的 BASE_URL/API_KEY/MODEL，并保证 Durable Batch 可用。生产环境还需既定 PostgreSQL、Redis、DB Events、S3 和受 SSRF 保护的抓取环境。随后执行真实 B站任务与不同领域任务，保留开题和发布审批，记录质量及费用结果。
