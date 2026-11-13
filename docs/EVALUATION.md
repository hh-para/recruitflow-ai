# 评测说明（EVALUATION）

> 本文件只记录实际执行结果，区分不同评测边界，不把资料检查/单元测试的通过率称为模型准确率。

## 执行环境

- 平台：Windows 11，Python 3.13.3。
- 依赖：`pydantic>=2`、`anthropic>=1`、`streamlit>=1.36`、`pytest`。
- 时区：Asia/Shanghai。

## 命令与结果

| 评测项 | 命令 | 实际结果 |
|---|---|---|
| 资料一致性校验 | `cd recruitflow-data && python scripts/validate_fixtures.py` | PASS |
| 单元/集成测试 | `python -m pytest -q` | **84 passed** |
| 业务集成评测 | `python -m pytest tests/test_e2e_business.py -q` | 1 passed（20 次投递重放，最终 6 候选人/7 应聘/3 面试/17 业务事件/0 未解决确认） |
| 界面检查（AppTest） | `python -m pytest tests/test_app.py -q` | 5 passed（页面启动/提交出现台账/重复不新增/待确认更新/空看板） |

## 评测边界与区分

1. **资料检查**：`validate_fixtures.py` 仅校验 fixtures 自身一致性，不代表业务正确。
2. **单元测试**：存储层、Schema、适配器、Harness、看板、同步各模块测试。
3. **业务集成评测（A）**：`tests/test_e2e_business.py` 从空库注入**人工标注**事件，经过真实 Harness + 存储层；用 expected 文件核对最终状态。事件在 `harness/demo_events.py`，标注为人工标注，不按消息 ID 在生产代码硬编码处理结果。
4. **界面检查**：AppTest 验证页面启动与关键交互；**不等同于完整浏览器人工验收**。
5. **真实模型评测（B）**：**已执行单条消息验证（DeepSeek 官方 API）** —— 最小请求 HTTP 200（reasoning_tokens 0）、招聘提取字段正确（耗时 1.08s、1299 tokens）、自然语言→Harness→台账端到端 applied。完整 20 投递的模型提取未批量执行。

## 失败项与修复记录

- 业务集成评测初现“一面通过”未同步面试结果（stage 变更但面试 result/status 未更新）→ 修复 Harness `_apply_update_stage`，复用面试变更逻辑，评测通过。
- 无降低标准或修改标准答案的情况。

## 仍影响最终交付的阻塞

- 真实 AI 端到端评测（B）仅做单条消息验证，未批量跑 20 条 fixtures，故暂无完整 AI 提取准确率/延迟/成本（不编造）。
- 智谱账户限流（429/1302）未解除，但已切换到 DeepSeek，不再是主路径。
- 无浏览器实测条件：界面仅经 AppTest 验证，未做完整浏览器人工验收。

## 未执行 / 不声称

- 未发布网站、未上传仓库、未录制视频。
- 未声称腾讯文档/企业微信已接通。
