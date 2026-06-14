# 多模型支持设计 — feat/multi-model-support

**版本**: v1.0 (Design)
**日期**: 2026-06-14
**作者**: Hermes Agent (via superpowers-brainstorming)
**状态**: 待用户批准

## 概述

将 enterprise-kb 从单一 MiniMax 模型升级为**多模型企业级平台**。支持云端 API（OpenAI / Anthropic / Gemini / DeepSeek / Qwen / GLM / MiniMax）+ 任意 OpenAI 兼容本地 endpoint（Ollama / vLLM / llama.cpp），含**动态加载、负载均衡、A/B 测试、主备 Fallback、Admin UI 仪表盘**。

## 目标

1. v1.0 业务代码零改动（通过迁移 seed 自动把 MiniMax 配置导入）
2. Admin 可在 Web 页面管理供应商、模型、A/B 规则
3. 按 user 维度 A/B 分流，结果稳定可复现
4. 主模型失败自动切备选，全失败统一报错
5. 用户 👍/👎 反馈沉淀为 A/B 评估数据
6. 13/13 v1 集成测试 + 新增测试 100% 通过

## 非目标

- 训练/微调任何模型
- 多租户隔离
- 计费/配额
- 模型市场的 marketplace 模式

## 决策记录

| # | 问题 | 决策 |
|:--|:----|:----|
| 1 | 多模型深度 | 全都要（动态加载 + 负载均衡 + A/B） |
| 2 | 配置位置 | Admin UI（数据库） |
| 3 | A/B 分流维度 | 按 user（`user_id % N`） |
| 4 | 适配策略 | OpenAI 兼容为主 + 单独 Anthropic/Gemini adapter |
| 5 | 本地支持 | 任意 OpenAI 兼容 endpoint |
| 6 | Fallback | 主备顺序（带指数退避重试） |
| 7 | A/B 评估 | 自动记录 + 👍/👎 反馈 + 仪表盘对比 |
| 8 | 迁移路径 | v1.0 MINIMAX_* 自动 seed 成内置 provider |

## 架构

### 高层

```
┌─────────────────────────────────────────────────────────┐
│ Frontend (SPA)                                          │
│   ├── Chat UI（👍/👎 反馈按钮 + model_used 标签）        │
│   └── Admin UI（/admin/models, /ab-tests, /metrics）   │
└────────────────────┬────────────────────────────────────┘
                     │ REST
┌────────────────────▼────────────────────────────────────┐
│ FastAPI 路由层                                          │
│   /api/v1/chat/query, /chat/feedback                    │
│   /api/v1/admin/{providers,models,ab-rules,metrics}     │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│ 核心服务层                                              │
│                                                          │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐│
│  │ ModelRouter  │──▶│ ABTestSelector│──▶│ FallbackChain││
│  │ (resolve)    │   │ (user_hash)  │   │ (主备)        ││
│  └──────────────┘   └──────────────┘   └──────────────┘│
│         │                                       │       │
│  ┌──────▼──────────────────────────────────────▼──────┐│
│  │ UnifiedModelClient (Protocol)                      ││
│  │   chat() / embed() / stream()                     ││
│  └──────┬─────────────────────────────────────────────┘│
└─────────┼───────────────────────────────────────────────┘
          │
   ┌──────┴───────┬──────────┬──────────┐
   ▼              ▼          ▼          ▼
 OpenAI         Anthropic  Gemini    Local
 Compat         原生       原生      OpenAI Compat
```

### 数据库 Schema

```sql
model_providers (id, name, display_name, provider_type, api_base_url,
                 api_key_enc, extra_config, enabled, is_builtin, ...)

model_configs (id, provider_id, model_name, display_name, model_type,
               context_window, input_price, output_price, enabled,
               is_default_chat, is_default_emb, extra_config, ...)

ab_test_rules (id, name, enabled, strategy, config JSONB, target, ...)
               strategy: 'user_hash_mod' | 'random_weight'
               target: 'chat' | 'embedding'

ab_test_metrics (id, user_id, model_id, request_type, latency_ms,
                 input_tokens, output_tokens, feedback SMALLINT,
                 feedback_text, ab_rule_id, created_at)
```

### 关键算法

**A/B 分流**：
```python
def select_model_by_ab(user_id, target, ab_rules, all_models) -> ModelConfig:
    rules = [r for r in ab_rules if r.target == target and r.enabled]
    if not rules:
        return get_default(target)

    rule = rules[0]
    if rule.strategy == "user_hash_mod":
        bucket = user_id % rule.config["mod"]
        model_name = rule.config["mapping"][str(bucket)]
        return find_model_by_name(model_name)
    elif rule.strategy == "random_weight":
        return random.choices(...).pop()
    return get_default(target)
```

**Fallback 链**：
```python
class FallbackChain:
    async def execute_with_fallback(self, primary, operation):
        chain = [primary] + primary.fallback_model_ids + [system_default]
        for model in chain:
            for attempt in range(max_retries):
                try:
                    return await operation(model)
                except RetryableError:
                    await asyncio.sleep(2 ** attempt)
                except NonRetryableError:
                    break
        raise AllModelsFailedError(...)
```

**Provider 工厂**：
```python
def get_client(model: ModelConfig) -> UnifiedModelClient:
    p = model.provider
    if p.provider_type == "anthropic":
        return AnthropicClient(api_key=decrypt(p.api_key_enc))
    if p.provider_type == "gemini":
        return GeminiClient(api_key=decrypt(p.api_key_enc))
    return OpenAICompatClient(  # OpenAI/MiniMax/DeepSeek/Qwen/GLM/Local
        api_key=decrypt(p.api_key_enc),
        base_url=p.api_base_url,
    )
```

## API 设计

| Method | Path | 用途 | 权限 |
|:----|:----|:----|:----:|
| GET    | /api/v1/admin/providers          | 列出供应商 | admin |
| POST   | /api/v1/admin/providers          | 新增供应商 | admin |
| PATCH  | /api/v1/admin/providers/{id}     | 改供应商 | admin |
| DELETE | /api/v1/admin/providers/{id}     | 删（非内置）| admin |
| GET    | /api/v1/admin/models             | 列出模型 | admin |
| POST   | /api/v1/admin/models             | 新增模型 | admin |
| PATCH  | /api/v1/admin/models/{id}        | 改模型 | admin |
| DELETE | /api/v1/admin/models/{id}        | 删模型 | admin |
| GET    | /api/v1/admin/ab-rules           | 列出 A/B 规则 | admin |
| POST   | /api/v1/admin/ab-rules           | 新建规则 | admin |
| PATCH  | /api/v1/admin/ab-rules/{id}      | 改规则 | admin |
| DELETE | /api/v1/admin/ab-rules/{id}      | 删规则 | admin |
| GET    | /api/v1/admin/metrics/summary    | 仪表盘数据 | admin |
| POST   | /api/v1/admin/models/test        | 连通性测试 | admin |
| POST   | /api/v1/chat/feedback            | 👍/👎 | 登录用户 |

## 安全设计

- API key Fernet 加密存储（key 来自 `ENCRYPTION_KEY` 单独 .env 变量）
- GET provider 响应**只回 key 末 4 位**，绝不返回完整 key
- 所有 admin API 走 `require_admin` 依赖
- 审计日志记录所有 provider/model/rule 增删改
- 异常脱敏（500 不暴露内部信息）

## 风险登记

| # | 风险 | 等级 | 缓解 |
|:--|:----|:----:|:----|
| R1 | OpenAI 兼容端点字段差异 | 🟡 | `extra_config` schema 适配；E2E 真调 |
| R2 | Fallback 链长延迟放大 | 🟡 | 链长 ≤ 3，严格 timeout |
| R3 | A/B 规则切换一致性 | 🟢 | 内存缓存原子替换 |
| R4 | API key 加密泄露 | 🟡 | 独立 ENCRYPTION_KEY；日志 redact |
| R5 | 多模型并发限流 | 🟡 | 每 provider qps_limit + semaphore |
| R6 | **Embedding 模型切换破坏旧向量** | 🔴 | UI 强提示 "需重建"；按 model_id 过滤检索 |
| R7 | 反馈冲击 DB | 🟢 | 异步 fire-and-forget 写 metrics |
| R8 | Anthropic/Gemini 协议差异 | 🟡 | 用原生 SDK，不套 OpenAI |

## TDD 计划

### Phase 1: 数据层（D1）
- [ ] `test_provider_crud.py` — ModelProvider CRUD
- [ ] `test_model_crud.py` — ModelConfig CRUD + 唯一约束
- [ ] `test_ab_rule_crud.py` — ABTestRule CRUD
- [ ] `test_metric_crud.py` — ABTestMetric CRUD
- [ ] Alembic 迁移脚本（pg 主键 SERIAL/JSONB）

### Phase 2: 加密 + 客户端（D2）
- [ ] `test_crypto.py` — encrypt/decrypt round-trip
- [ ] `test_openai_client.py` — chat + embed（mock httpx）
- [ ] `test_anthropic_client.py` — Claude 原生
- [ ] `test_factory.py` — get_client() 路由

### Phase 3: 核心逻辑（D3）— **最关键**
- [ ] `test_ab_selector.py` — user_hash_mod + random_weight
- [ ] `test_fallback.py` — 链构建 + 重试 + 全失败
- [ ] `test_error_classification.py` — Retryable/NonRetryable

### Phase 4: API 路由（D4-6）
- [ ] `test_admin_providers_api.py` — CRUD + 鉴权
- [ ] `test_admin_models_api.py` — CRUD + 默认唯一性
- [ ] `test_admin_ab_rules_api.py` — CRUD
- [ ] `test_admin_metrics_api.py` — 仪表盘 + 连通性
- [ ] `test_chat_feedback.py` — 👍/👎
- [ ] `test_chat_query_integration.py` — **改 /chat/query 接入 router**

### Phase 5: 迁移（D7）
- [ ] `test_migrate_v1_to_v2.py` — 跑后 MiniMax 默认，13/13 v1 测试还过
- [ ] `test_seed_defaults.py` — 首次启动 seed

### Phase 6: 前端（D8）
- [ ] `/admin/models` 页面（手动）
- [ ] `/admin/ab-tests` 页面（手动）
- [ ] `/admin/metrics` 页面（手动）
- [ ] chat 👍/👎 + model_used 标签（手动）

### Phase 7: E2E（D9）
- [ ] Docker 重 build + 启
- [ ] admin 加 OpenAI → A/B → chat → 反馈
- [ ] Fallback 演练（主模型配错 key）
- [ ] 100 并发压测

## 排期

| Day | Phase | 交付 |
|:----|:----|:----|
| D1  | P1    | 4 表可建可查 |
| D2  | P2    | 客户端单元测试全过 |
| D3  | P3    | 核心逻辑 100% 覆盖 |
| D4  | P4.1-4.2 | Provider/Model CRUD |
| D5  | P4.3-4.5 | A/B 规则 + 仪表盘 |
| D6  | P4.6-4.7 | chat 反馈 + /chat/query 接入 |
| D7  | P5    | v1.0 迁移无感升级 |
| D8  | P6    | UI 可用 |
| D9  | P7    | E2E + 压测报告 |
| D10 | -     | 文档 + PR |

## 验收标准

- [ ] 13/13 v1 集成测试通过
- [ ] 所有 Phase 1-7 测试通过
- [ ] Docker compose up -d 一键起
- [ ] Admin UI 可视化配 OpenAI/MiniMax 两套，A/B 跑通
- [ ] 主 MiniMax API 配错时自动切到备选 DeepSeek
- [ ] 👍/👎 数据出现在仪表盘
- [ ] 100 并发 chat/query P95 < 2s
- [ ] 文档完整（README + 本 spec + API doc）
- [ ] GitHub PR 推上 dev 分支待 review

## 待用户最终批准

本设计基于 8 个 brainstorming 决策 + 4 个设计 section 迭代得出。

**批准后下一步**：进入 `superpowers-writing-plans` 生成可执行的 bite-sized 任务清单。
