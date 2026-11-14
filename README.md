# RecruitFlow AI

招聘数据自动记录 Demo：接收（模拟）企业微信招聘消息，调用大模型提取结构化招聘事件，经业务校验后写入 SQLite 台账，并通过 Streamlit 展示台账、面试安排、待确认事项、变更历史、招聘看板与同步管理。
> 全部人物、消息、组织与安排均为虚构。**未接入企业微信或腾讯文档**；同步为“模拟外部表格”。
> 这是一个用于测试的 Demo，不是已上线的 AI 应用。

---

## 项目简介

招聘进展通常散落在群聊消息里（新增候选人、安排/改期面试、结果反馈、更正、退出等），人工逐条登记容易漏记、记错、难以追溯“为什么是这个状态”。RecruitFlow 演示了一条自动化链路：

```
招聘消息 → 大模型提取结构化事件 → 代码校验/匹配/事务写入 → 台账与看板展示 → 模拟同步
```

核心原则：**模型只负责“理解”，代码负责“校验与落地”**。模型输出未经 Schema 与业务校验，不得写入业务台账。

---

## 功能特性

- **消息处理**：接收模拟企微消息，去重、提取、校验、匹配、事务写入；歧义转“待确认”。
- **两种模式**：真实模型模式（DeepSeek 默认）与手动结构化事件模式（离线验证业务）。
- **招聘台账**：候选人、应聘记录、面试，按姓名/岗位/阶段筛选，查看变更历史与原始消息。
- **待确认**：同名/多岗位歧义的人工确认，走统一确认流程（保留操作者、时间、原消息引用）。
- **招聘看板**：候选人数、应聘数、待确认数、待同步数、阶段分布、待进行面试、超期跟进。
- **同步管理**：模拟外部表格的同步任务与失败重试（有上限退避、幂等 upsert、超时恢复）。
- **业务规则**：姓名非唯一、未提及字段保留、明确清空、建议/否定/已安排/已完成/更正、面试改期不重复创建、更正保留历史、相对日期按消息时间解释、去重、事务审计。

---

## 架构

```
app.py（Streamlit 五页面）
  └─ harness/   领域层
       processor.py   消息处理 Harness（去重→匹配→事务写入/待确认）
       schema.py      结构化事件 Schema（Pydantic）
       dates.py       相对/绝对时间解析（Asia/Shanghai）
       stats.py       看板统计（纯计算，只读）
       syncer.py      同步执行器（单 worker，退避重试）
       demo_events.py 人工标注事件（离线演示/业务评测，非模型输出）
  └─ adapters/  适配器层
       deepseek_adapter.py  DeepSeek 官方 API（默认真实模型，OpenAI 兼容）
       zhipu_adapter.py     OpenAI 兼容适配器基类（智谱 GLM 默认配置）
       anthropic_adapter.py Anthropic 官方 SDK
       fake_adapter.py      测试用假适配器（不实现自然语言理解）
       mock_sync.py         模拟外部表格（独立 SQLite，upsert 幂等）
       base.py / errors.py / prompt.py
  └─ storage/   持久化层
       db.py        建表 + 旧库安全迁移
       repository.py 仓储（七类表 CRUD / 事务 / 局部更新 / 显式清空）
  └─ prompts/extract.md   提取提示词
  └─ scripts/   extract_one.py（单条提取）、init_demo.py（离线演示初始化）
  └─ tests/     87 个测试（含 AppTest 与端到端业务评测）
```

分层原则：业务规则集中在 `harness`，页面（`app.py`）只做展示与编排，不在页面重复实现业务。

---

## 技术栈与依赖

| 组件 | 说明 |
|---|---|
| Python | 3.13（开发环境） |
| Streamlit | 界面（五页面 + AppTest 测试） |
| SQLite | 数据持久化（标准库 `sqlite3`） |
| Pydantic v2 | 结构化事件 Schema 校验 |
| pytest | 测试（含 Streamlit AppTest） |
| DeepSeek / Zhipu / Anthropic | 真实模型适配器 |

依赖见 `requirements.txt`

---

## 快速开始

```bash
# 1) 安装依赖（测试另需 pytest）
python -m pip install -r requirements.txt
python -m pip install pytest

# 2) 离线演示（无需任何密钥）
python -m scripts.init_demo --reset
python -m streamlit run app.py
```

打开页面后顶部显示「**人工标注事件演示**」，即可在“手动结构化事件模式”下体验完整业务流程。

---

## 配置（环境变量）

### DeepSeek 适配器（默认 `deepseek-flash`）

| 变量 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | API 密钥 |
| `DEEPSEEK_BASE_URL` | 否 | 自定义 API 地址，默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 否 | 模型名称，默认 `deepseek-flash` |
| `DEEPSEEK_TIMEOUT` | 否 | 请求超时秒数，默认 60 |


未配置密钥时，真实适配器会**明确报错，不会静默切换到 Fake 或离线结果**。

---

## 运行

```bash
# 资料校验（在 recruitflow-data/ 目录执行）
cd recruitflow-data && python scripts/validate_fixtures.py

# 全部测试（项目根目录）
python -m pytest -q

# 离线演示初始化（人工标注事件，通过 Harness 重放，不调用大模型、不导入最终状态）
python -m scripts.init_demo --reset

# 启动 Streamlit 界面（项目根目录）
python -m streamlit run app.py

# 单条消息提取（项目根目录；默认 provider=deepseek，需先配置 DEEPSEEK_API_KEY）
python -m scripts.extract_one "新增候选人C101周晓，应聘产品运营，来源内推。" \
    --provider deepseek --model deepseek-flash \
    --sent-at "2026-11-19T09:00:00+08:00" --sender-name 林悦
```

`extract_one.py` 常用参数：`--provider {deepseek,zhipu,anthropic}`、`--model`、`--base-url`、
`--timeout`、`--max-retries`（0 表示关闭重试）、`--context-json`。

---

## 两种处理模式

### 手动结构化事件模式（默认，离线）

- 不调用大模型，仅验证业务流程。
- 用户填写消息与结构化事件 JSON，经 Pydantic 校验后交给同一 Harness。
- 页面标注「未调用大模型，仅验证业务流程」。

### 真实模型模式

- 调用 DeepSeek 官方 API（`DEEPSEEK_API_KEY`，默认 `deepseek-flash`）。
- 非思考模型 + JSON 输出（`response_format=json_object`），本地 Pydantic 校验仍保留。
- 未配置或调用失败会明确报错，**不自动切换模式、不降级为 Fake**。
- 页面顶部明确显示当前供应商与模型。

---

## 核心业务规则

1. **候选人与应聘记录分离**：一名候选人可应聘多个岗位，按应聘记录定位。
2. **姓名不能作为唯一标识**：同名或同人多岗位无法唯一定位时创建待确认任务，不更新台账。
3. **未提及字段保留**：局部更新只覆盖消息明确给出的字段。
4. **未提供 vs 明确清空**：`clear_fields` 表达显式清空，与“未提供”严格区分。
5. **建议 / 否定 / 已安排 / 已完成 / 更正** 语义区分；“建议安排”不等于已安排。
6. **面试改期更新原安排**，不创建重复面试。
7. **更正保留历史**：保留原事件，新增更正事件并记录来源与变更前后值。
8. **相对日期**以业务消息 `sent_at` 与 Asia/Shanghai 时区为基准；确认引用原消息时仍按原消息时间解释。
9. **去重**：键为 `source + group_id + message_id`；重复投递不产生重复业务更新或同步任务。
10. **事务与审计**：业务事件 + 台账更新 + 同步任务同一事务；模型网络调用期间不持有数据库写事务。

---

## 数据模型

| 表 | 说明 |
|---|---|
| `candidates` | 候选人（candidate_id 主键、name） |
| `applications` | 应聘记录（application_id、candidate_id、position、channel、hr_owner、stage；唯一 candidate+position） |
| `interviews` | 面试（interview_id、application_id、round、scheduled_at、interviewer、status、result） |
| `raw_messages` | 原始消息（去重键 source+group_id+message_id；processed_at 标记处理状态） |
| `business_events` | 业务事件（message_id、action、application_changes、interview_after、变更前后值、references_message_id） |
| `reviews` | 待确认任务（message_id、status、reason、possible_application_ids、pending_event、resolved_by） |
| `sync_tasks` | 同步任务（application_id、status、attempts、next_retry_at、completed_at、last_error） |

枚举值：

- 事件类型：`create_application` / `update_stage` / `schedule_interview` / `reschedule_interview` / `correct` / `withdraw` / `confirm` / `irrelevant`
- 阶段（stage）：`新投递` / `初筛通过` / `一面待进行` / `一面通过` / `待定` / `未通过` / `候选人退出`
- 面试状态：`待进行` / `已完成` / `已取消`；面试结果：`通过` / `待定` / `未通过`
- 同步状态：`pending` / `processing` / `retry_wait` / `succeeded` / `failed`


## 文档索引

- `docs/SOLUTION.md`：痛点、方案、Harness 职责、架构、已实现/模拟/待接入能力、成本构成、接入条件。
- `docs/DEMO_SCRIPT.md`：3 分钟演示脚本。
- `docs/EVALUATION.md`：评测环境、命令、结果、失败项与边界。
- `SPEC.md`：需求与业务规格（数据模型、业务规则、验收条件）。
- `TASKS.md`：任务顺序与验收条件（T01–T09）。
- `PROGRESS.md`：当前状态与下一步。
