# Competitive Research V1 完成说明

本文记录 Deep Research Agent 的 Competitive Research V1 可验收实现。V1
仍只支持 `competitive_research`，其他 Investigation Type 必须复用同一
Evidence、Claim、Audit、Workflow 和 Report 内核后再开放。

## 完整执行链路

```text
Planning Run
→ 开题审批
→ Collect Durable Batch
→ Snapshot / Chunk / Source Router
→ Analyze Durable Batch
→ Claim / Verbatim Quote / PriceObservation
→ Audit Run
→ 定向 Rework Durable Batch（最多两轮）
→ Synthesis Run
→ 发布审批
→ Web / Markdown / PDF
```

Agent 只能通过 `submit_scope`、`submit_evidence`、`submit_claims`、
`submit_audit` 和 `submit_report_section` 提交领域数据。工具根据当前
Runtime User 查找活动的 owner-scoped `StageTask`，并验证 Stage、Kind、
Task、Workflow 和幂等性。Agent 不能直接推进状态机或写业务表。

## 可信证据硬门槛

- Evidence 创建时保存不可变 Snapshot，并验证 SHA-256。
- Evidence 摘录必须是 Snapshot 的逐字 substring；只允许 Unicode 和
  空白字符归一化。
- Snapshot 自动切分为带原始字符 Offset 的 Chunk，并保存本地哈希
  n-gram Embedding。
- Analyst 使用按维度检索的 Top-K Chunk，不重复加载所有正文。
- 每个事实 Claim 必须提交 Evidence Binding：Evidence ID、逐字 Quote、
  Snapshot Hash、Relation 和可选 Offset。
- Claim 中的数字、日期、百分比和价格必须在 supporting Quote 中出现。
- Material Claim 不允许使用 Search Snippet 作为 supporting Evidence。
- Audit 必须对每条 Binding 返回
  `entails / partially_supports / contradicts / unrelated`；只有
  `verified + entails` 才计入独立来源门槛。
- 关键 Claim 至少两个独立域名；审计前和证据不足时始终为
  `uncertain`。

## 定价管线

定价 Claim 必须包含结构化 `PriceObservation`：套餐、金额、币种、周期、
计费单位、席位下限、地区、税、促销、生效时间、官方状态和逐字 Quote。
金额、币种和周期必须出现在 Quote 中。`official=true` 只有在 Evidence
域名属于开题 Scope 中确认的竞品官方域名时才接受，否则拒绝写入。

Source Router 按 `pricing / documentation / github / news / web` 进行服务端
分类。Pricing Page 总是进入高优先级强抓取路径；Jina 或直接抽取质量不足
时使用 Playwright。生产模式禁止使用无法拦截重定向的裸 Chromium 抓取，
避免 SSRF 绕过。

## 预算、恢复和隔离

- `ci_budget_entries` 持久化每个 Run/Batch Item 的 Token 使用，幂等累计
  到 Investigation。
- Collect/Analyze 最多使用总预算的 80%，为 Audit/Rework/Synthesis
  保留 20%。
- 80% 和 100% 分别产生预算告警和耗尽事件。
- 每次启动前验证预算和 30 分钟 Deadline；Batch 超时会取消 Batch，Run
  超时会请求中断。
- Batch Item 独立失败和自动重试；普通 Run 为首次加最多两次重试。
- Workflow Lease、Heartbeat、Stage Attempt、持久化 Submission 和稳定
  Idempotency Key 支持进程重启后接管，不重复调用模型或重复写 Claim。

## 材料、存储和导出

- `/api/investigations/{id}/materials` 支持 20 MB 内的文本、Markdown、
  CSV、JSON、PDF、Word、PowerPoint 和 Excel；文档复用 MarkItDown 转换后
  进入相同 Snapshot/Chunk/Evidence 管线。
- 开发环境默认使用 owner-controlled 本地 Artifact Storage。
- 生产环境使用内置 SigV4 S3-Compatible Storage，保存上传原件、Snapshot 和 PDF。
- PDF 使用服务端 Chromium 从统一 HTML 模板打印；Docker 镜像安装
  Chromium 和 Noto CJK 字体。
- 报告内容在 HTML 渲染前转义，避免导出 XSS。

## 黄金评测

`backend/tests/fixtures/competitive_research_golden.v1.json` 包含 12 个专门
针对证据不足和诱导编造的 Case：计划与已上线混淆、最高值与平均值、月付
与年付、联系销售、新闻转载、旧价格、SPA 空壳、Prompt Injection、竞品
错绑和虚构席位数。

评测器输出 Label Accuracy、Publish Precision 和 Unsupported Publish
Count。发布基线要求：

- Quote 完整性 100%；
- 数字、日期和价格逐字一致率 100%；
- Claim-Evidence 支持准确率不低于 90%；
- 关键 Claim 双独立来源覆盖不低于 95%，其余必须显示 `uncertain`；
- Unsupported Publish Count 必须为 0。

## 生产 Compose

`docker/docker-compose.deep-research.yaml` 在基础 Compose 上增加 PostgreSQL
和 MinIO，并复用原有 Redis。所有基础设施只有内部网络地址，不发布数据库
端口；PostgreSQL、Redis、MinIO 和 Gateway 均使用健康依赖。

启动前：

1. 在 `config.yaml` 设置 `database.backend: postgres`、
   `database.postgres_url: $DATABASE_URL`、`run_events.backend: db` 和 Redis
   StreamBridge。
2. 在未提交的 `.env` 设置 `CI_POSTGRES_PASSWORD`、`CI_S3_ACCESS_KEY`、
   `CI_S3_SECRET_KEY`、Bocha/Tavily/Jina 和模型凭据。
3. 执行：

```bash
docker compose -f docker/docker-compose.yaml \
  -f docker/docker-compose.deep-research.yaml up -d --build
```

生产模式缺失 PostgreSQL、DB Events、Redis、S3 或严格搜索/抽取 Provider
时，Gateway 启动检查会失败，不会以降级状态伪装就绪。

## 验证范围

当前自动验证覆盖严格 Quote、数字/价格、Chunk 和混合检索、Source Router、
SSRF、预算幂等、工具 Owner 隔离、Lease 接管、存储路径逃逸、HTML XSS、
完整 Durable Workflow 和 Gateway 生命周期。真实 Provider 的内容质量仍需
持续用黄金集回归；模型或抓取 Provider 变更不得绕过上述硬门槛。
