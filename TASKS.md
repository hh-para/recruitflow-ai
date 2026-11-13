# TASKS.md — 任务顺序与验收条件

> 依赖顺序推进。每个任务完成后更新本文档状态与 PROGRESS.md。
> 状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 完成。

## 总览

| ID | 任务 | 状态 | 依赖 |
|---|---|---|---|
| T01 | 模拟数据及资料校验 | [x] 完成 | — |
| T02 | SQLite 数据模型与存储层 | [x] 完成 | T01 |
| T03 | 结构化事件 Schema 与模型适配器 | [x] 完成 | T02 |
| T04 | 消息处理 Harness | [x] 完成 | T02, T03 |
| T05 | 歧义确认、更正及异常处理 | [x] 完成 | T04 |
| T06 | Streamlit 消息页面与台账 | [x] 完成 | T04 |
| T07 | 看板及变更历史 | [x] 完成 | T06 |
| T08 | 模拟同步适配器与失败重试 | [x] 完成 | T04 |
| T09 | 端到端评测及交付文档 | [x] 完成 | T05–T08 |

---

## T01 模拟数据及资料校验 — [x] 完成

- **目标**：提供可复现的模拟消息与人工标准答案，并用脚本校验资料一致性。
- **产物**：`recruitflow-data/`（fixtures + `validate_fixtures.py`）。
- **验收方法**：`python scripts/validate_fixtures.py` 输出 PASS。
- **完成条件**：✅ 已通过 —— 20 次投递、19 条唯一消息、6 候选人、7 应聘记录、3 面试、17 业务事件、0 未解决确认。

## T02 SQLite 数据模型与存储层 — [x] 完成

- **目标**：按 SPEC 第 3 节建立 SQLite 表结构与存储访问层。
- **依赖**：T01。
- **内容**：
  - 表：candidates、applications、interviews、raw_messages、business_events、reviews、sync_tasks（`storage/db.py` 建表脚本）。
  - 建表约束：去重键唯一索引 `(source, group_id, message_id)`；外键；`UNIQUE(candidate_id, position)`；`UNIQUE(application_id, round)`。
  - 读写接口（`storage/repository.py`）：候选人/应聘/面试/原始消息/业务事件/待确认任务/同步任务的新增、查询、局部更新；事务上下文管理器 `LedgerRepository.transaction()`。
  - “字段未提供”与“显式清空”区分：局部更新只改 `changes` 中的键；`CLEAR` 哨兵置为 NULL。
- **产物**：`storage/`（`db.py`、`repository.py`、`errors.py`、`__init__.py`）、`tests/test_storage.py`、`pytest.ini`。
- **验收方法**：`python -m pytest -q`。
- **完成条件**：✅ 已通过 —— 10 个测试通过（两个同名候选人独立、一人两岗位、重复消息/重复应聘唯一约束、局部更新保留未提及字段、显式清空可空字段、事务回滚、持久化重开）。
- **实际验证**：`python -m pytest -q` → `10 passed`；`recruitflow-data` 下 `python scripts/validate_fixtures.py` → PASS。

## T03 结构化事件 Schema 与模型适配器 — [x] 完成

- **目标**：定义 Pydantic 结构化事件 Schema，实现统一模型适配器接口（真实 + 测试用 Fake），并提供提取提示词与独立调用脚本。
- **依赖**：T02。
- **内容**：
  - `harness/schema.py`：`StructuredEvent`（`event_type`、`candidate_id`/`candidate_name`/`position`、`application_changes`、`clear_fields`、`interview`、`references_message_id`、`evidence`、`uncertainty`、`suggested`、`cancel_interview`）；`EventType` 覆盖 8 类；`extra="forbid"` 严格校验。
  - 区分“未提供=不修改”与“明确清空=clear_fields”；“建议安排”用 `suggested` 标记，不等价于已安排。
  - `prompts/extract.md`：只提取原文支持信息、区分建议/否定/确认/更正、相对日期以 sent_at + Asia/Shanghai 为基准、保留时间原文、不依赖固定消息 ID。
  - `adapters/`：`AnthropicAdapter`（官方 anthropic SDK，`output_config` json_schema，密钥仅从环境变量读取，API/超时/非法 JSON/Schema 错误明确区分）；`FakeAdapter`（仅测试用，不做自然语言理解）。
  - `scripts/extract_one.py`：单条消息提取 CLI，不写数据库。
- **产物**：`harness/`、`adapters/`、`prompts/extract.md`、`scripts/`、`tests/test_schema.py`、`tests/test_adapters.py`、`.env.example`、`requirements.txt`、`README.md`。
- **验收方法**：`python -m pytest -q`；`python -m scripts.extract_one ...`（未配置密钥时明确报错）。
- **完成条件**：✅ 27 个测试通过（Schema 8 + 适配器 9 + 存储 10）。
- **实际验证**：`python -m pytest -q` → `27 passed`；`validate_fixtures.py` → PASS；CLI 无密钥 → 报错退出码 1。
- **备注**：真实接入已验证（DeepSeek 官方 API，`deepseek-flash`）：最小请求 HTTP 200、招聘提取字段正确、自然语言→Harness→台账端到端 applied。智谱适配器保留（代码完成），其账户限流 429/1302 不再阻塞，已停止探测。

## T04 消息处理 Harness — [x] 完成

- **目标**：实现「去重 → 加载上下文 → 提取校验 → 匹配 → 事务写入或待确认」主流程。
- **依赖**：T02, T03。
- **内容**：
  - `harness/processor.py`：`DeliveryHarness.process_delivery`；事件分发 create_application / update_stage / schedule_interview / reschedule_interview / irrelevant。
  - 去重：source+group_id+message_id；`raw_messages.processed_at` 标记已处理（与业务写入同事务），已处理重复投递返回 duplicate。
  - 模型失败可重试：原始消息保存时 processed_at 为空，失败不标记已处理；重试不因“消息已保存”而跳过。
  - 模型网络调用期间不持有数据库写事务（写事务仅在应用事件时开启）。
  - 唯一匹配 → 事务内更新台账 + 保存业务事件 + 创建同步任务；无法确定 → 创建待确认任务（candidate_ambiguous / application_ambiguous）。
  - 存储层增补：`get_raw_message` / `mark_raw_message_processed` / `in_transaction`；`raw_messages.processed_at` 列。
- **产物**：`harness/processor.py`、`tests/test_harness.py`（8 个测试）、`storage` 增补。
- **验收方法**：`python -m pytest -q`（FakeAdapter 注入事件，不读标准答案、不按消息ID返回预设结果）。
- **完成条件**：✅ 45 个测试通过（新增 8 个 Harness 测试）。
- **备注**：confirm / correct / withdraw 事件类型留待 T05；本轮不开发页面、不执行外部同步。

## T05 歧义确认、更正及异常处理 — [x] 完成

- **目标**：补齐确认、更正、退出、改期与相对日期处理。
- **依赖**：T04。
- **内容**：
  - `harness/dates.py`：集中实现相对/绝对时间解析（Asia/Shanghai；无法确定返回 None，不猜时间）。
  - 确认（confirm）：关联原消息与待确认任务；合并原事件与确认信息后重新校验目标（不把确认当空白新事件）；仍有歧义保持待确认；成功则同一事务内业务更新 + 关闭 review + 保存事件 + 同步任务；重复确认幂等；引用不存在/已解决/跨群组明确处理。
  - 更正（correct）：保留原事件、新增更正事件（`references_message_id`），同步更新阶段与面试结果。
  - 退出（withdraw）：只影响已定位岗位；取消待进行面试并保留时间/面试官；不影响同候选人其他岗位。
  - 改期（reschedule_interview）：更新同一轮面试不重复创建；相对日期以 sent_at 为基准；跨天确认按原消息时间解释。
  - 存储层：reviews 增 `pending_event` / `pending_sent_at` / `group_id`。
- **产物**：`harness/dates.py`、`harness/processor.py`（重写）、`tests/test_t05.py`、`tests/test_dates.py`、`storage` 增补。
- **验收方法**：`python -m pytest -q`（FakeAdapter 注入事件）。
- **完成条件**：✅ 59 个测试通过（新增 T05 8 + 日期解析 6）。
- **备注**：本轮不开发页面、不执行外部同步；T03 真实接入仍待验证（账户限流）。

## T06 Streamlit 消息页面与台账 — [x] 完成

- **目标**：Streamlit 三页面（消息处理 / 招聘台账 / 待确认），两种明确区分的模式，文件型 SQLite。
- **依赖**：T04。
- **内容**：
  - `app.py`：复用 `DeliveryHarness` 与存储层，不在页面重复实现业务规则。
  - 两种模式：真实模型模式（调用智谱适配器，未配置/失败明确报错不自动切换）；手动结构化事件模式（填 JSON，经 Pydantic 校验后交给同一 Harness，标注“未调用大模型”）。
  - 三页面：消息处理（来源/群组/消息ID/发送人/时间/原文 + 结果展示）、招聘台账（候选人/岗位/阶段/渠道/负责人/面试 + 姓名/岗位/阶段筛选 + 详情查看原始消息与变更历史）、待确认（原消息/歧义原因/候选记录 + 选择目标后走确认流程）。
  - 文件型 SQLite（`RECRUITFLOW_DB_PATH` 可配，默认 recruitflow-demo.db），每次 rerun 新建连接；旧库缺列由 `storage.migrate_db` 安全迁移，不删除已有数据。
  - Harness 增 `process_with_event`（手动事件路径）与可选 adapter。
- **产物**：`app.py`、`storage/db.py`（migrate_db）、`tests/test_app.py`（AppTest）、`tests/test_migration.py`、`tests/test_harness.py` 增补。
- **验收方法**：`python -m pytest -q`（AppTest 4 例）；`python -m streamlit run app.py`。
- **完成条件**：✅ 66 个测试通过；AppTest 验证页面启动、提交后出现台账、重复提交不新增、待确认处理后列表与台账更新（不调用真实模型）。
- **备注**：本轮不开发统计看板与外部同步。

## T07 看板及变更历史 — [x] 完成

- **目标**：新增“招聘看板”页面，完善按应聘记录的变更历史（业务时间/处理时间/变更前后/引用关系）。
- **依赖**：T06。
- **内容**：
  - `harness/stats.py`：`compute_stats` 纯计算（只读，不写、不调模型）。指标：候选人数（按ID去重）、应聘记录数、未解决待确认、待同步/同步失败、当前阶段分布（非转化率）、今日及未来待进行面试、超期跟进（阈值可配、默认7天、排除未通过/候选人退出/已入职）。
  - 统计基准时间可选固定值（默认当前，Asia/Shanghai）；`position_filter` 只影响应聘记录类指标，候选人数/待确认/同步为全局运维指标。
  - 变更历史：业务时间（确认依据原消息）/处理时间（实际执行）/来源消息/原文/事件类型/变更前后值/引用关系。
  - 持久化补充：`business_events` 增 `application_before`/`interview_before`（记录变更前值，不反推伪造）；`list_applications`/`list_business_events` 返回 `created_at`。
- **产物**：`harness/stats.py`、`app.py`（招聘看板 tab + 变更历史增强）、`tests/test_stats.py`、存储/harness 增补。
- **验收方法**：`python -m pytest -q`；`python -m streamlit run app.py`。
- **完成条件**：✅ 74 个测试通过（新增 stats 7 + AppTest 空看板 1）；看板只读、不修改数据库。
- **备注**：本轮不接外部平台、不发真实模型请求；统计看板使用 `st.bar_chart`（T09 若需 Plotly 可再替换）。

## T08 模拟同步适配器与失败重试 — [x] 完成

- **目标**：统一同步适配器接口 + MockSyncAdapter（独立 SQLite 文件模拟外部表格）+ 同步任务执行器（复用 sync_tasks，单 worker）。
- **依赖**：T04。
- **内容**：
  - `adapters/mock_sync.py`：`SyncAdapter` 接口、`TemporarySyncError`/`PermanentSyncError`、`MockSyncAdapter`（独立 SQLite 文件，按 application_id upsert 幂等，支持 fail_first_n 注入故障）。
  - `harness/syncer.py`：`run_due_tasks`/`retry_failed`；状态 pending/processing/retry_wait/succeeded/failed；记录 attempts/next_retry_at/completed_at/脱敏错误；临时错误有上限指数退避，永久错误直接 failed；到期才执行（不在页面 sleep）；外部调用期间不持业务写事务；processing 超时（300s）可恢复；同一应聘记录按 id 串行。
  - 设计选择：**读取最新状态（非快照）**，单 worker 串行 + upsert 幂等，避免旧数据覆盖。
  - `sync_tasks` 表增 `application_id`/`next_retry_at`/`completed_at`/`processing_started_at`；harness `_create_sync_task` 传入 application_id。
  - `app.py` 新增“同步管理”tab：任务列表、执行到期任务、重试失败任务、模拟临时失败开关、查看模拟外部表格；成功标注“模拟同步成功”。
- **产物**：`adapters/mock_sync.py`、`harness/syncer.py`、`tests/test_sync.py`、存储/harness/app 增补。
- **验收方法**：`python -m pytest -q`（注入时钟，不实际等待）。
- **完成条件**：✅ 83 个测试通过（新增 sync 9）；页面明确标注“模拟外部表格”，不宣称腾讯文档已接通。
- **备注**：本轮不调用真实模型、不接腾讯文档/企业微信。

## T09 端到端评测及交付文档 — [x] 完成

- **目标**：端到端业务评测、可重复离线演示、交付文档与最终检查。
- **依赖**：T05–T08。
- **内容**：
  - 业务执行评测（A）：`harness/demo_events.py`（人工标注事件）+ `tests/test_e2e_business.py`，从空库经真实 Harness + 存储层重放 20 次投递，核对最终 6 候选人/7 应聘/3 面试/17 业务事件/0 未解决确认，及改期/更正/退出历史与模拟同步一致性。
  - 真实 AI 端到端评测（B）：标记“未执行：真实 API 接入阻塞”（不把 A 的通过率称为 AI 提取准确率）。
  - 离线演示：`scripts/init_demo.py`（重放人工标注事件，默认拒绝覆盖，`--reset` 显式重置；页面显示“人工标注事件演示”）。
  - 文档：`docs/SOLUTION.md`、`docs/DEMO_SCRIPT.md`、`docs/EVALUATION.md`，README 更新（安装/启动/测试/无密钥演示/重置/功能清单/限制）。
- **验收方法**：`python -m pytest -q`、`validate_fixtures.py`、`python -m scripts.init_demo --db <临时库>`。
- **完成条件**：✅ 84 个测试通过（含 e2e 业务 1）；资料校验 PASS；演示初始化可复现（6/7/3/17）。
- **备注**：真实模型未验证（限流）；界面未做完整浏览器人工验收；未发布/上传/录制。
