# PROGRESS.md — 当前状态与下一步

> 只记录事实与已验证结果，不将计划/推测写成已完成。

## 已完成

- **T01–T08** ✅（模拟数据/存储层/Schema与适配器/Harness/确认更正退出/Streamlit/看板/模拟同步）
- **T09 端到端评测及交付文档** ✅
  - 业务执行评测（`tests/test_e2e_business.py` + `harness/demo_events.py`）。
  - 离线演示 `scripts/init_demo.py`；文档 `docs/SOLUTION.md` / `docs/DEMO_SCRIPT.md` / `docs/EVALUATION.md`；README 更新。
- **真实模型切换为 DeepSeek 官方 API** ✅（`adapters/deepseek_adapter.py` 复用 OpenAI 兼容实现）

## 实际验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| 全部测试 | `python -m pytest -q` | **87 passed** |
| 业务集成评测 | `python -m pytest tests/test_e2e_business.py -q` | 1 passed（6/7/3/17/0） |
| 资料一致性校验 | `python scripts/validate_fixtures.py` | **PASS** |
| DeepSeek 最小请求（单次，无重试） | 直连 chat/completions | HTTP 200，回复“连接成功”，reasoning_tokens 0 |
| DeepSeek 招聘提取 | `--provider deepseek --model deepseek-flash` | 字段正确（C201 孙悦 后端工程师 猎聘 张敏），1.08s |
| DeepSeek 端到端 | 自然语言→提取→Harness→台账 | outcome=applied（C301 李薇 产品经理 内推 王芳） |

## 尚未实现 / 尚未验证（阻塞）

- **真实 AI 端到端评测（B）为单条消息验证**：完整 20 投递的模型提取未批量执行；不把业务评测通过率当 AI 提取准确率。
- 界面未做完整浏览器人工验收（仅 AppTest）。
- 企业微信、腾讯文档未接入（模拟）。
- 智谱账户限流（429/1302）未解除，但已不是主路径。

## 下一步

- 如需，可用 DeepSeek 批量跑 20 条 fixtures 的模型提取，量化真实提取准确率/延迟/成本（需授权真实 API 调用）。

## 待决策业务规则（需确认）

见 SPEC.md 第 7 节：候选人编号来源、明确清空语义、面试 result 枚举、stage 状态机、同步幂等键。均已按第一版默认记录，待确认后定稿。
