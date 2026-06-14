# 📚 企业知识库使用教程

> 一份面向**最终用户**、**管理员**、**集成开发者**三类读者的实战指南。
> 配套源码仓库：[enterprise-kb](https://github.com/john-2016/enterprise-kb)

---

## 目录

1. [项目简介](#1-项目简介)
2. [5 分钟快速启动](#2-5-分钟快速启动)
3. [第一步：管理员登录](#3-第一步管理员登录)
4. [终端用户篇：智能问答](#4-终端用户篇智能问答)
5. [管理员篇：系统管理](#5-管理员篇系统管理)
6. [开发者篇：REST API](#6-开发者篇rest-api)
7. [常见问题 FAQ](#7-常见问题-faq)
8. [安全与生产部署清单](#8-安全与生产部署清单)
9. [附录：环境变量速查](#9-附录环境变量速查)

---

## 1. 项目简介

**企业知识库（Enterprise Knowledge Base）** 是一个**开箱即用的 RAG（检索增强生成）问答平台**。
你把企业的产品手册、规章制度、运维 SOP 之类的文档丢进去，员工就能像和 ChatGPT 聊天一样直接提问，系统会从文档里挑出最相关的段落，再让大模型组织成自然语言答案，并在回答中标注引用来源。

### 1.1 核心特性

| 能力 | 说明 |
| --- | --- |
| **多模型路由** | 内置一个默认 LLM provider，可通过 Admin API 动态接入 OpenAI / Anthropic / Gemini / DeepSeek / Qwen / GLM 等多个兼容服务 |
| **A/B 分流** | 按 `user_hash_mod` 或 `random_weight` 策略将不同用户路由到不同模型，对比效果 |
| **主备降级** | 主模型失败时自动切换到备用模型，不影响业务 |
| **👍/👎 反馈闭环** | 用户对每个回答打反馈，自动汇总到指标面板，找出"胜出模型" |
| **审计与指标** | 所有聊天记录、API key 操作、A/B 分流结果都会落库可查 |
| **零重启热更新** | 新增/修改 provider、model、A/B 规则都不需要重启服务 |
| **容器化部署** | 一条 `./install.sh` 拉起 PostgreSQL + FastAPI 全栈 |

### 1.2 技术架构一览

```
┌──────────────────────────────────────────────────────────────┐
│ 用户浏览器 / API 调用方                                        │
└──────────────────────────────────────────────────────────────┘
                          │ HTTPS / JWT
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI  (backend/)                                          │
│  ├─ /auth       注册/登录/改密/JWT                            │
│  ├─ /chat       问答/历史/反馈                                │
│  ├─ /documents  上传/索引/检索                                │
│  └─ /admin/*    用户/Provider/Model/A-B/Metrics 全面管理      │
└──────────────────────────────────────────────────────────────┘
        │                       │                       │
        ▼                       ▼                       ▼
┌──────────────┐        ┌──────────────┐         ┌──────────────┐
│ PostgreSQL   │        │ FAISS 内存   │         │ LLM Providers│
│ (用户/文档/   │        │ 索引 (1536  │         │  MiniMax-M3  │
│  配置/审计)   │        │ 维向量)      │         │  OpenAI ...  │
└──────────────┘        └──────────────┘         └──────────────┘
```

### 1.3 三个角色

| 角色 | 主要任务 | 本教程对应章节 |
| --- | --- | --- |
| **终端用户**（editor / viewer） | 上传文档、向知识库提问、对回答点赞或点踩 | [第 4 节](#4-终端用户篇智能问答) |
| **管理员**（admin） | 管理用户账号、配置 LLM provider、设计 A/B 规则、查看指标仪表盘 | [第 5 节](#5-管理员篇系统管理) |
| **集成开发者** | 通过 REST API 把知识库能力嵌入到自家产品/工作流 | [第 6 节](#6-开发者篇rest-api) |

---

## 2. 5 分钟快速启动

> **目标**：从 `git clone` 到能登录 Web 界面，全程不超过 5 分钟。

### 2.1 环境要求

- Docker ≥ 20.10
- Docker Compose v2（`docker compose` 命令可用）
- 宿主机空闲 **2 GB 内存**（PostgreSQL + FastAPI + 嵌入模型缓冲）
- 能联网拉镜像（首次构建约 1-2 分钟）

### 2.2 一键安装

```bash
git clone https://github.com/john-2016/enterprise-kb.git
cd enterprise-kb

# 先给 ./data 容器内非 root 用户写权限（dockerfile 用 unprivileged 'app' 用户跑）
chmod 777 ./data

# 跑安装脚本（脚本会问一句"是否添加 LLM provider"，输 N 跳过即可）
echo "N" | bash install.sh
```

`install.sh` 会**自动**做这 5 件事：

1. 检查 `docker` / `docker compose` / `curl` 是否齐备
2. 从 `.env.example` 复制 `.env`，并**生成两个强随机密钥**（`JWT_SECRET_KEY`、`ENCRYPTION_KEY`）
3. 交互式询问：是否要立刻添加自定义 LLM provider（输 `N` 跳过，先用内置 MiniMax；脚本默认走 `N` 分支）
4. `docker compose up -d` 拉起 PostgreSQL + app 容器
5. 轮询健康检查 `/api/v1/health`，通过后**打印随机生成的 admin 密码**到终端

> ⚠️ **三个实战踩坑**（已经过端到端验证）：
> 1. **`./data` 必须是 world-writable**（`chmod 777`）。容器以非 root 用户跑，首次建 vector store 时会 `PermissionError: [Errno 13]`。
> 2. **install.sh 总是会问一句**"Add a provider?"——非交互环境（CI、管道）下不喂个输入会直接 `set -e` 退出。`echo "N" | bash install.sh` 是一行写法。
> 3. **同一台机器只能跑一份**——`docker-compose.yml` 里 `container_name: enterprise-kb-app` 是写死的，要跑多实例请先改名（或换一台机器）。

成功输出类似：

```
[install] .env generated (JWT_SECRET_KEY + ENCRYPTION_KEY random)
[install] Starting docker compose (postgres + app)...
[install] Waiting for health check ...
[install] ✓ Service is up at http://localhost:8000

============================================================
  Admin user:  admin
  Random pass: aB3-xYz_RANDOM_VALUE_HERE
  (Saved to: data/.admin_password, chmod 600)
  ⚠️  Log in and change this password immediately.
============================================================
```

> ⚠️ **请立刻保存这段 admin 密码**，关掉终端就找不到了（虽然文件里也有一份 `data/.admin_password`，但属于只读凭据，建议登录后立即修改）。

### 2.3 打开浏览器

| 入口 | URL |
| --- | --- |
| **Web 前端** | http://localhost:8000/ |
| **API 文档 (Swagger UI)** | http://localhost:8000/docs |
| **健康检查** | http://localhost:8000/api/v1/health |

> install.sh 成功 banner 写死的 URL 是 `http://localhost:8000/`。如果你改了 `docker-compose.yml` 里的端口映射，**banner 会撒谎**——以 `docker compose port app 8000` 的实际输出为准。

**自己再确认一次服务正常**（避免 banner 误判）：

```bash
curl -fsS http://localhost:8000/api/v1/health
# 期望: {"status":"ok","version":"1.0.0","service":"enterprise-kb"}
```

用 2.2 步打印的 `admin` 账号 + 随机密码登录后，你会看到左侧导航有 5-6 个菜单项。

### 2.4 停服 / 重启 / 卸载

```bash
# 查看运行状态
docker compose ps

# 看后端日志（调试用）
docker compose logs -f app

# 重启
docker compose restart

# 停服（数据保留）
docker compose down

# 彻底卸载（删数据卷）
docker compose down -v
```

---

## 3. 第一步：管理员登录

### 3.1 拿到 admin 凭据

两种方式任选其一：

```bash
# 方式 A：从终端打印复制
# （install.sh 结束时打印的那段）

# 方式 B：从文件读取
cat data/.admin_password
# 输出：aB3-xYz_RANDOM_VALUE_HERE
```

### 3.2 修改默认密码（强烈建议）

1. 用 `admin` + 随机密码登录 Web 界面
2. 右上角（如果没看到，可通过 `PUT /api/v1/auth/me/password` 接口）修改密码：

```bash
curl -X PUT http://localhost:8000/api/v1/auth/me/password \
  -H "Authorization: Bearer <登录返回的 access_token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password": "原密码", "new_password": "新密码（≥6 位）"}'
```

### 3.3 创建业务账号

Web 端 → 左侧 **用户管理** → **新建用户**：

| 字段 | 说明 |
| --- | --- |
| 用户名 | 登录名，唯一 |
| 邮箱 | 用于审计日志显示 |
| 角色 | `admin` / `editor` / `viewer` |
| 初始密码 | 首次登录后建议用户自己改 |

> 也可以在 `.env` 把 `ALLOW_REGISTRATION=true` 开放公开注册；**生产环境务必保持 `false`**。

### 3.4 三个内置角色的权限

| 能力 | admin | editor | viewer |
| --- | :-: | :-: | :-: |
| 智能问答 | ✅ | ✅ | ✅ |
| 文档上传/删除 | ✅ | ✅ | ❌ |
| 管理用户 | ✅ | ❌ | ❌ |
| 配置 LLM provider / model | ✅ | ❌ | ❌ |
| 设置 A/B 规则 | ✅ | ❌ | ❌ |
| 查看指标仪表盘 | ✅ | ❌ | ❌ |

---

## 4. 终端用户篇：智能问答

> 适用角色：admin / editor / viewer。

### 4.1 上传你的第一份文档

**Web 端流程**：

1. 左侧导航 → **文档管理**
2. 点击右上 **上传文档** 按钮
3. 选择本地文件（支持 `.pdf` `.txt` `.md` `.docx` `.html`）
4. 等待几秒，状态从 `pending` → `indexed` 即表示已建好索引

**API 方式**：

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/handbook.pdf"
```

返回示例：

```json
{
  "id": 1,
  "filename": "handbook.pdf",
  "status": "pending",
  "chunk_count": 0,
  "created_at": "2026-06-14T10:00:00Z"
}
```

### 4.2 触发文档索引

上传后默认是 `pending` 状态，需要手动触发一次索引（系统会把文档切片 → 调用 embedding 模型 → 写入 FAISS）：

```bash
curl -X POST http://localhost:8000/api/v1/documents/1/index \
  -H "Authorization: Bearer $TOKEN"
```

**Web 端** 在文档列表里点每行右侧的 **索引** 按钮即可。

> 索引是异步的；列表里看到 `status=indexed` 就代表可检索了。

### 4.3 提问

**Web 端**：左侧 **智能问答** → 在输入框敲问题 → Enter 发送。

**API 方式**：

```bash
curl -X POST http://localhost:8000/api/v1/chat/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "员工年假是多少天？",
    "top_k": 5
  }'
```

返回示例（节选）：

```json
{
  "answer": "根据《员工手册》第 4.2 条，入职满 1 年的员工每年享有 10 个工作日的年假……",
  "sources": [
    {"doc_id": 1, "filename": "handbook.pdf", "chunk_id": 17, "score": 0.82, "snippet": "……年假天数 10 个工作日……"},
    {"doc_id": 1, "filename": "handbook.pdf", "chunk_id": 22, "score": 0.79, "snippet": "……年假须提前 3 天申请……"}
  ],
  "model_used": {"id": 1, "name": "MiniMax-M3", "provider": "minimax", "metric_id": 42},
  "tokens_used": {"prompt": 612, "completion": 138, "total": 750},
  "latency_ms": 1820
}
```

> 💡 **top_k** 决定从向量库挑出几段最相关的文本喂给 LLM；一般 3-5 段效果最佳。值越大，token 消耗越高，答案可能越啰嗦。

### 4.4 给回答打分（反馈闭环）

每个回答下方有 👍 / 👎 两个按钮，点击后：

```bash
curl -X POST http://localhost:8000/api/v1/chat/feedback \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "metric_id": 42,
    "feedback": 1
  }'
# feedback: 1 = 👍 点赞，-1 = 👎 点踩，0 = 撤销
```

这条反馈会被写进 `ab_test_metrics` 表，**管理员的指标仪表盘**会自动算出"哪个模型好评率最高"。

### 4.5 查看历史

```bash
curl http://localhost:8000/api/v1/chat/history?limit=20 \
  -H "Authorization: Bearer $TOKEN"
```

Web 端在智能问答页面右侧栏可看到历史会话。

### 4.6 删除 / 重建索引

```bash
# 重建（文档内容没变但 embedding 模型换了时用）
curl -X POST http://localhost:8000/api/v1/documents/1/reindex \
  -H "Authorization: Bearer $TOKEN"

# 删除
curl -X DELETE http://localhost:8000/api/v1/documents/1 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 5. 管理员篇：系统管理

> 所有 API 都需要 admin 角色的 JWT。

### 5.1 用户管理

#### 5.1.1 列出所有用户

```bash
curl http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

#### 5.1.2 创建用户

```bash
curl -X POST http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "email": "[email protected]",
    "password": "alice_pass_2026",
    "role": "editor"
  }'
```

#### 5.1.3 调整角色 / 停用

```bash
# 改角色
curl -X PUT http://localhost:8000/api/v1/admin/users/2/role \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "admin"}'

# 删除
curl -X DELETE http://localhost:8000/api/v1/admin/users/2 \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 5.2 LLM Provider 管理

**Provider** = 一个 LLM 服务（OpenAI、Anthropic、DeepSeek、MiniMax、……）。
**Model** = 这个服务下挂的某个具体模型（如 `gpt-4o`、`claude-3-5-sonnet`、`MiniMax-M3`）。

关系：`Provider 1 — n Model`。

#### 5.2.1 内置 Provider

第一次安装时，`migrate_v1_to_v2` 会自动 seed 一个 `minimax` 内置 provider，**不能删除**。

如果你没有在 `.env` 里填 `MINIMAX_API_KEY`，该 provider 的 key 是一段占位符（仅在真的调用时才会失败，不会影响其它流程）。

#### 5.2.2 添加一个 OpenAI Provider

**Web 端**：模型管理 → 右上 **添加 Provider** → 选 `openai` 模板 → 填 API key。

**API 方式**：

```bash
curl -X POST http://localhost:8000/api/v1/admin/providers \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "openai_prod",
    "display_name": "OpenAI Production",
    "provider_type": "openai",
    "api_base_url": "https://api.openai.com/v1",
    "api_key": "sk-YOUR-OPENAI-KEY-HERE",
    "extra_config": {}
  }'
```

**支持的所有 provider_type**：

| 类型 | 默认 `api_base_url` | 备注 |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | 兼容 DeepSeek / Qwen / GLM 等所有 OpenAI 协议服务 |
| `anthropic` | `https://api.anthropic.com` | Claude 全系 |
| `gemini` | `https://generativelanguage.googleapis.com` | Gemini Pro / Flash |
| `minimax` | `https://api.minimaxi.com/v1` | 内置 |
| `local` | (自行填写) | 自建 vLLM / Ollama OpenAI 兼容服务 |

> 📌 **API key 永不返回明文**：任何 `GET /admin/providers` 调用都只返回 `key_last_4`（末 4 位），明文只在创建/更新时短暂接收并立刻用 Fernet 加密入库。

#### 5.2.3 测试 Provider 连通性

`POST /api/v1/admin/models/test` 会用一个最小 prompt 验证 provider 配置是否可用（不等同于真实业务效果）。

```bash
curl -X POST http://localhost:8000/api/v1/admin/models/test \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": 2,
    "model_name": "gpt-4o-mini",
    "model_type": "chat"
  }'
```

### 5.3 Model 管理

#### 5.3.1 列出全部模型

```bash
curl http://localhost:8000/api/v1/admin/models -H "Authorization: Bearer $ADMIN_TOKEN"
```

#### 5.3.2 接入新模型

```bash
curl -X POST http://localhost:8000/api/v1/admin/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": 2,
    "model_name": "gpt-4o",
    "display_name": "GPT-4o (Primary)",
    "model_type": "chat",
    "context_window": 128000,
    "is_default_chat": true
  }'
```

- `is_default_chat: true` —— 这就是默认聊天模型（一个 provider 下只能有一个）
- `is_default_emb: true` —— 默认 embedding 模型（系统级唯一）
- `context_window` —— 上下文长度，仅展示用

#### 5.3.3 切换默认 / 启用 / 停用

```bash
# 切默认聊天模型为 id=3
curl -X PATCH http://localhost:8000/api/v1/admin/models/3 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_default_chat": true}'

# 停用一个模型
curl -X PATCH http://localhost:8000/api/v1/admin/models/3 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

> 切换默认 / 启停都**无需重启**，下一个聊天请求立刻生效。

### 5.4 A/B 测试规则

A/B 规则让你把一部分用户路由到模型 A，另一部分到模型 B，**对比效果**。

#### 5.4.1 规则字段

| 字段 | 必填 | 说明 |
| --- | :-: | --- |
| `name` | ✅ | 规则名（仅展示） |
| `target` | ✅ | `chat` 或 `embedding`（A/B 只能用于 chat） |
| `strategy` | ✅ | `user_hash_mod`（按 user_id 取模）或 `random_weight`（按权重随机） |
| `enabled` | ✅ | 全局开关 |
| `variants` | ✅ | `[{model_id, weight, name}, ...]，weight 总和可不等于 100，按比例归一化` |

#### 5.4.2 创建一个 50/50 分流规则

```bash
curl -X POST http://localhost:8000/api/v1/admin/ab-rules \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GPT-4o vs MiniMax-M3",
    "target": "chat",
    "strategy": "random_weight",
    "enabled": true,
    "variants": [
      {"model_id": 1, "weight": 50, "name": "control"},
      {"model_id": 3, "weight": 50, "name": "treatment"}
    ]
  }'
```

开启后，**每次 `POST /chat/query`** 都会按策略选一个模型，并把 `metric_id` 写回 `ab_test_metrics`。再结合 4.4 步的 👍/👎，就能算出胜出模型。

#### 5.4.3 启停 / 删除

```bash
# 停用
curl -X PATCH http://localhost:8000/api/v1/admin/ab-rules/1 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# 删除
curl -X DELETE http://localhost:8000/api/v1/admin/ab-rules/1 \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 5.5 指标仪表盘

**Web 端**：左侧 **仪表盘** → 看每个模型的：

- **调用次数**（近 7 天）
- **平均延迟**（毫秒）
- **总 token 消耗**（prompt / completion 分开）
- **👍 / 👎 比率**
- **胜出者**（自动根据好评率计算）

**API 方式**：

```bash
curl http://localhost:8000/api/v1/admin/metrics/summary?days=7 \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

返回示例：

```json
{
  "period_days": 7,
  "models": [
    {
      "model_id": 1,
      "name": "MiniMax-M3",
      "call_count": 412,
      "avg_latency_ms": 1820,
      "total_tokens": 612048,
      "feedback_up": 287,
      "feedback_down": 14,
      "feedback_rate": 0.952
    },
    {
      "model_id": 3,
      "name": "GPT-4o",
      "call_count": 408,
      "avg_latency_ms": 1340,
      "total_tokens": 502113,
      "feedback_up": 312,
      "feedback_down": 22,
      "feedback_rate": 0.934
    }
  ],
  "winner": {
    "model_id": 3,
    "name": "GPT-4o",
    "win_rate": 0.520
  }
}
```

> 💡 当样本量太少时，winner 字段可能是 `null`，这是设计行为——避免冷启动阶段误判。

### 5.6 审计日志

```bash
curl "http://localhost:8000/api/v1/admin/audit-logs?limit=50&action=chat_query" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

记录所有用户操作：`login` / `chat_query` / `upload_document` / `provider_create` / `ab_rule_update` ……

### 5.7 平台总览

```bash
curl http://localhost:8000/api/v1/admin/stats -H "Authorization: Bearer $ADMIN_TOKEN"
```

返回 `{user_count, document_count, chunk_count, query_count}`，可做首页看板。

---

## 6. 开发者篇：REST API

### 6.1 鉴权

所有 `/api/v1/*`（除 `/health` 和 `/auth/login`、`/auth/register`）都需要 `Authorization: Bearer <token>`。

**登录拿 token**：

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}'
# 响应: {"access_token": "eyJhbG...", "token_type": "bearer", "user": {...}}
```

**Token 有效期** 默认 24 小时，刷新请重新登录。

### 6.2 完整端点索引

| 方法 | 路径 | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | 公开 | 健康检查 |
| `POST` | `/api/v1/auth/register` | 公开 | 注册（受 `ALLOW_REGISTRATION` 控制） |
| `POST` | `/api/v1/auth/login` | 公开 | 登录拿 token |
| `GET` | `/api/v1/auth/me` | 用户 | 查看当前用户 |
| `PUT` | `/api/v1/auth/me/password` | 用户 | 改自己密码 |
| `POST` | `/api/v1/chat/query` | 用户 | RAG 问答 |
| `GET` | `/api/v1/chat/history` | 用户 | 我的历史 |
| `POST` | `/api/v1/chat/feedback` | 用户 | 👍/👎 |
| `POST` | `/api/v1/documents/upload` | editor+ | 上传文档 |
| `GET` | `/api/v1/documents/` | 用户 | 列表 |
| `GET` | `/api/v1/documents/{id}` | 用户 | 详情 |
| `DELETE` | `/api/v1/documents/{id}` | editor+ | 删除 |
| `POST` | `/api/v1/documents/{id}/index` | editor+ | 建索引 |
| `POST` | `/api/v1/documents/{id}/reindex` | editor+ | 重建索引 |
| `GET` | `/api/v1/admin/users` | admin | 用户列表 |
| `POST` | `/api/v1/admin/users` | admin | 创建用户 |
| `PUT` | `/api/v1/admin/users/{id}/role` | admin | 改角色 |
| `DELETE` | `/api/v1/admin/users/{id}` | admin | 删除用户 |
| `GET` | `/api/v1/admin/providers` | admin | Provider 列表 |
| `POST` | `/api/v1/admin/providers` | admin | 新增 Provider |
| `PATCH` | `/api/v1/admin/providers/{id}` | admin | 修改 Provider |
| `DELETE` | `/api/v1/admin/providers/{id}` | admin | 删除 Provider（不可删内置） |
| `GET` | `/api/v1/admin/models` | admin | Model 列表 |
| `POST` | `/api/v1/admin/models` | admin | 新增 Model |
| `POST` | `/api/v1/admin/models/test` | admin | 连通性测试 |
| `PATCH` | `/api/v1/admin/models/{id}` | admin | 修改 Model |
| `DELETE` | `/api/v1/admin/models/{id}` | admin | 删除 Model |
| `GET` | `/api/v1/admin/ab-rules` | admin | A/B 规则列表 |
| `POST` | `/api/v1/admin/ab-rules` | admin | 新增 A/B 规则 |
| `PATCH` | `/api/v1/admin/ab-rules/{id}` | admin | 修改 A/B 规则 |
| `DELETE` | `/api/v1/admin/ab-rules/{id}` | admin | 删除 A/B 规则 |
| `GET` | `/api/v1/admin/metrics/summary` | admin | 指标汇总 |
| `GET` | `/api/v1/admin/audit-logs` | admin | 审计日志 |
| `GET` | `/api/v1/admin/stats` | admin | 平台总览 |

### 6.3 Swagger UI

**最推荐的探索方式**：浏览器打开 http://localhost:8000/docs，可以：

- 查看每个端点的完整参数 schema
- 直接在浏览器里试调（点 "Try it out" → 自动注入你的 Bearer token）
- 导出 OpenAPI JSON：`curl http://localhost:8000/openapi.json > spec.json`

### 6.4 集成示例（Python）

```python
import requests

BASE = "http://localhost:8000"

# 1. 登录
r = requests.post(f"{BASE}/api/v1/auth/login",
                  json={"username": "alice", "password": "alice_pass_2026"})
token = r.json()["access_token"]
H = {"Authorization": f"Bearer {token}"}

# 2. 上传 + 索引
with open("handbook.pdf", "rb") as f:
    doc = requests.post(f"{BASE}/api/v1/documents/upload",
                        headers=H, files={"file": f}).json()
requests.post(f"{BASE}/api/v1/documents/{doc['id']}/index", headers=H)

# 3. 提问
ans = requests.post(f"{BASE}/api/v1/chat/query", headers=H, json={
    "question": "员工的年假政策是什么？",
    "top_k": 5,
}).json()
print(ans["answer"])
for s in ans["sources"]:
    print(f"  - {s['filename']} #{s['chunk_id']} (score={s['score']:.2f})")

# 4. 反馈
requests.post(f"{BASE}/api/v1/chat/feedback", headers=H, json={
    "metric_id": ans["model_used"]["metric_id"],
    "feedback": 1,   # 👍
})
```

### 6.5 错误码

| HTTP | 含义 | 常见原因 |
| --- | --- | --- |
| 400 | 请求体不合法 | JSON 缺字段、文件类型不支持 |
| 401 | 未登录 | token 缺失 / 过期 / 篡改 |
| 403 | 无权限 | 用 viewer 调用 admin 接口；删内置 provider |
| 404 | 资源不存在 | 文档 id 写错 |
| 409 | 冲突 | 用户名重复、provider 已被 model 引用 |
| 422 | 参数校验失败 | top_k 必须是正整数 |
| 500 | 服务异常 | 提交 issue 时附 `/api/v1/admin/audit-logs` |

---

## 7. 常见问题 FAQ

### 7.1 安装相关

**Q1：`install.sh` 跑完报 `docker compose` 找不到？**
A：装 Docker Compose v2 插件。Mac/Windows 的 Docker Desktop 已自带；Linux 用 `apt install docker-compose-plugin` 或官方静态包。

**Q1.5：`install.sh` 跑完报"非交互环境 stdin 错误"？**
A：脚本里有 `read -r -p` 询问是否添加 provider。CI / 管道环境请 `echo "N" | bash install.sh`。

**Q2：健康检查卡住不返回？**
A：90% 是镜像拉不下来。`docker compose logs -f app` 看具体报错；如果是网络问题，配 Docker 代理或改用国内镜像加速器。
如果日志里看到 `PermissionError: [Errno 13] Permission denied: '/app/data/vector_store'`——是 `./data` 目录权限问题，宿主机执行 `chmod 777 ./data` 再 `docker compose restart` 即可。

**Q3：忘记 admin 密码了怎么办？**
A：删文件重来。`docker compose down -v && rm data/.admin_password && bash install.sh`，会重新生成新密码。**注意 `-v` 会清掉所有业务数据**。

### 7.2 文档相关

**Q4：上传 PDF 后状态一直是 `pending`？**
A：忘记点 **索引** 按钮。索引是显式触发的（设计选择，避免误传大文件时白烧 embedding 钱）。

**Q5：能支持哪些文件类型？**
A：当前默认支持 `.pdf` `.txt` `.md` `.docx` `.html`。新增类型需要扩展 `backend/services/document_service.py` 里的 loader 映射。

**Q6：能上传多大规模的文档？**
A：单文件上限默认 50 MB（在 nginx / 反代层调整 `client_max_body_size`）。FAISS 内存占用与 chunk 总数线性相关；经验值：**10 万 chunks ≈ 1.5 GB 内存**。

### 7.3 模型相关

**Q7：怎么加一个 OpenAI 兼容但不是 OpenAI 的服务（如 DeepSeek）？**
A：把 `provider_type` 设为 `openai`，`api_base_url` 填目标服务地址（如 `https://api.deepseek.com/v1`），其它字段照填即可。系统走 OpenAI 协议客户端。

**Q8：A/B 规则开了之后，怎么看是 A 还是 B 在回答？**
A：`POST /chat/query` 响应里 `model_used.name` 字段会告诉你当时用了哪个模型；Web 端回答下方也有一行小字标注。

**Q9：能不能给不同部门配不同默认模型？**
A：当前版本只支持平台级 `is_default_chat`。要做到部门级，需要在 `model_router.py` 里扩展用户属性 → 模型映射逻辑，然后开 issue 提需求。

**Q10：内置 MiniMax provider 没填 key 怎么删？**
A：删不掉。`is_builtin=True` 的 provider 受保护。正确做法是**在 `.env` 里补 `MINIMAX_API_KEY`**，重启后即可用。

### 7.4 性能相关

**Q11：单次问答延迟多少算正常？**
A：单纯 embedding 检索 < 100 ms；调用 LLM 视 provider 而异，MiniMax-M3 / GPT-4o 级别约 1-3 秒。指标面板能看到分模型均值。

**Q12：token 消耗怎么控制？**
A：调小 `top_k`、缩短 `question` 长度、用更便宜的模型（如 `gpt-4o-mini` / `MiniMax-M3`）。所有 token 消耗都会在 `metrics/summary` 里汇总。

---

## 8. 安全与生产部署清单

部署到生产前，**逐条勾选**：

- [ ] `JWT_SECRET_KEY` 已改为 ≥ 32 字符的随机串（`install.sh` 自动生成的就是合规的）
- [ ] `ENCRYPTION_KEY` 同上（切换它会导致旧 provider 的 api_key 全部失效，需重新录入）
- [ ] `ENV=production`（启用严格启动安全校验）
- [ ] `ALLOW_REGISTRATION=false`
- [ ] `CORS_ALLOWED_ORIGINS` 已限定为前端真实域名，**不要包含 `*`**
- [ ] `DATABASE_URL` 已切换到 PostgreSQL（SQLite 不支持并发写）
- [ ] admin 首次登录后**立刻修改默认密码**
- [ ] 反代层（Nginx / Caddy）启用 HTTPS
- [ ] 数据卷 `pgdata` 和 `./data` 已在主机做异地备份
- [ ] 防火墙只暴露 443（Web）+ 4318（OTel HTTP，可选），其它端口不外网可达
- [ ] 上线前在 `/api/v1/admin/stats` 确认 `query_count` 在合理增长

---

## 9. 附录：环境变量速查

| 变量 | 必填 | 默认 | 说明 |
| --- | :-: | --- | --- |
| `MINIMAX_API_KEY` | 选填 | 占位 | 内置 MiniMax provider 的真实 key |
| `OPENAI_API_KEY` 等 | 选填 | 空 | 仅文档提示用，**不会**自动注入；通过 Admin API 录入 |
| `DATABASE_URL` | ✅ | `sqlite+aiosqlite:///./data/kb.db` | dev 模式；生产改 PostgreSQL |
| `JWT_SECRET_KEY` | ✅ | （install.sh 生成） | ≥ 32 字符 |
| `ENCRYPTION_KEY` | ✅ | （install.sh 生成） | Fernet key，≥ 32 字符 |
| `HOST` | — | `0.0.0.0` | 监听地址 |
| `PORT` | — | `8000` | 监听端口 |
| `ENV` | — | `production` | 决定启动安全校验严格程度 |
| `CORS_ALLOWED_ORIGINS` | — | `http://localhost:3000,http://localhost:8000` | 逗号分隔 |
| `ALLOW_REGISTRATION` | — | `false` | 生产保持 false |

---

## 反馈与贡献

- 提 Issue / 讨论：[GitHub Issues](https://github.com/john-2016/enterprise-kb/issues)
- 提 PR：先开 issue 讨论，再 fork → feature branch → PR
- 安全问题：**不要** 在 issue 里贴 token / 密码，请发邮件给 maintainer

> 本教程与代码一起开源（见仓库 LICENSE）。
