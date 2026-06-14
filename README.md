# 📚 企业知识库 — Enterprise Knowledge Base

基于 **MiniMax M3 + embo-01** 的企业级 RAG 知识库系统。

## 🏗️ 架构

```
用户 → 聊天界面/API → FastAPI → 文档切片 → embo-01 向量化 → FAISS 检索
                                                      ↓
                                       MiniMax-M3 生成回答 ← 拼装 Prompt
```

## ⚡ 5 分钟跑起来

```bash
git clone https://github.com/john-2016/enterprise-kb.git
cd enterprise-kb
./install.sh
```

`install.sh` 会自动：

1. 生成 `.env`（含随机 `JWT_SECRET_KEY` 和 `ENCRYPTION_KEY`）
2. 问你是否要添加自定义 LLM provider（可跳过，用内置 MiniMax）
3. 启动 PostgreSQL + FastAPI 容器
4. 等待服务健康检查通过
5. 打印随机生成的 admin 密码

完成后访问 `http://localhost:8000`，用打印的密码登录。

> 📘 **详细使用教程**（含终端用户 / 管理员 / API 三类读者、FAQ、smoke 验证过的安装踩坑清单）：[docs/TUTORIAL.md](docs/TUTORIAL.md)

### 后续添加 provider

```bash
./scripts/add-provider.sh
# 交互式选择模板 (OpenAI / Anthropic / Gemini / DeepSeek / Qwen / GLM / Local)
# 填 API key, 一行加完 provider + model
```

## 🚀 快速启动

> 想要自动 5 分钟跑起来？看上一节 [⚡ 5 分钟跑起来](#-5-分钟跑起来)。本节是手动部署详细步骤。

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 MiniMax API Key (其他密钥 install.sh 会自动生成)
```

### 2. Docker 部署（推荐）

```bash
docker compose up -d
```

服务跑在 `http://localhost:8000`。

### 3. 手动部署

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化数据库
python -m scripts.init_db

# 导入测试数据（可选）
python -m scripts.seed

# 启动服务
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

## 📖 使用指南

### 默认账号

> 首次运行 `./install.sh` 时，admin 密码会**随机生成**并打印到控制台，同时保存到 `data/.admin_password`（chmod 600）。请立即登录并修改。

| 角色 | 用户名 | 密码 |
|:----|:------|:----|
| 管理员 | admin | (随机，见 install.sh 输出或 `data/.admin_password`) |
| 编辑者 | editor | editor123 |
| 查看者 | viewer | viewer123 |

### 功能流程

1. **登录系统** → 进入聊天界面
2. **上传文档** → 在「文档管理」上传 MD/PDF/TXT/DOCX
3. **自动索引** → 文档自动切片 → embo-01 向量化 → 存入 FAISS
4. **智能问答** → 在聊天界面提问，RAG 引擎自动检索并回答

## 🔧 技术栈

| 组件 | 技术 |
|:----|:----|
| 后端框架 | FastAPI (Python 3.11+) |
| 数据库 | SQLAlchemy + SQLite (可切换 PostgreSQL) |
| 向量库 | FAISS (可切换 Milvus/Qdrant) |
| 嵌入模型 | MiniMax embo-01 (1536 维) |
| 生成模型 | MiniMax-M3 |
| 前端 | 原生 HTML/CSS/JS SPA |
| 部署 | Docker Compose |

## 📁 项目结构

```
enterprise-kb/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── config.py             # 配置
│   ├── database.py           # 数据库连接
│   ├── models/               # ORM 模型
│   ├── routers/              # API 路由
│   ├── services/             # 业务逻辑
│   └── core/                 # 核心工具
├── frontend/
│   ├── index.html            # SPA 入口
│   ├── css/style.css         # 样式
│   └── js/app.js             # 前端逻辑
├── scripts/                  # 工具脚本
├── data/                     # 数据存储
├── Dockerfile                # Docker 构建
├── docker-compose.yml        # 一键部署
└── requirements.txt          # Python 依赖
```

## 📡 API 文档

启动后访问 `http://localhost:8000/docs` 查看 Swagger 文档。

### 核心 API

| 方法 | 路径 | 说明 |
|:----|:----|:----|
| POST | /api/v1/auth/login | 登录 |
| POST | /api/v1/auth/register | 注册 |
| POST | /api/v1/documents/upload | 上传文档 |
| POST | /api/v1/documents/{id}/index | 索引文档 |
| POST | /api/v1/chat/query | 智能问答 |
| GET  | /api/v1/admin/stats | 系统统计 |

## Multi-Model Support (v1.1)

v1.1 在原有单一 LLM 接入的基础上引入 **多模型路由 + A/B 测试 + 加密凭据 + 反馈闭环**，所有配置都可通过 Admin API 动态调整，无需重启服务。

### 关键能力

- **多 Provider 接入**：内置 `minimax` (minimaxi.com / MiniMax-M3 + embo-01)，可继续追加 OpenAI / Anthropic / Gemini 等兼容 provider
- **凭据加密存储**：所有 API key 通过 Fernet (AES-128-CBC + HMAC-SHA256) 落库，避免明文泄露
- **A/B 分流**：基于 `user_hash_mod` / `random_weight` 等策略对 chat 流量按 user_id 哈希分桶到不同模型
- **FallbackChain**：上游模型失败时按优先级自动重试，最终失败统一抛 `AllModelsFailedError`
- **反馈闭环**：每次 chat/query 写入 `ab_metrics` 表，用户反馈 (👍/👎) 通过 `/chat/feedback` 回写

### 端到端配置流程

1. **登录管理员**
   ```bash
   TOKEN=$(curl -s http://localhost:8000/api/v1/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"admin","password":"<安装时打印的随机密码>"}' | jq -r .access_token)
   ```

2. **添加 Provider**（API key 加密落库）
   ```bash
   curl -X POST http://localhost:8000/api/v1/admin/providers \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{
       "name": "minimax",
       "display_name": "minimax (built-in)",
       "provider_type": "minimax",
       "api_base_url": "https://api.minimaxi.com/v1",
       "api_key": "YOUR_KEY",
       "enabled": true
     }'
   ```

3. **添加 Model**（绑定到 provider）
   ```bash
   curl -X POST http://localhost:8000/api/v1/admin/models \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{
       "provider_id": 1,
       "model_name": "MiniMax-M3",
       "model_type": "chat",
       "is_default_chat": true
     }'
   ```

4. **连通性测试**
   ```bash
   curl -X POST http://localhost:8000/api/v1/admin/models/test \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{"provider_id":1,"model_name":"MiniMax-M3","test_message":"hi"}'
   ```

5. **建 A/B 规则**（user_id 哈希 mod 2 决定走哪条模型）
   ```bash
   curl -X POST http://localhost:8000/api/v1/admin/ab-rules \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{
       "name": "chat-ab-test",
       "target": "chat",
       "strategy": "user_hash_mod",
       "config": {"mod": 2, "mapping": {"0": "MiniMax-M3", "1": "embo-01"}}
     }'
   ```

6. **普通用户聊天**（后端自动按 A/B 规则选模型）
   ```bash
   curl -X POST http://localhost:8000/api/v1/chat/query \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{"question":"hi"}'
   ```
   响应中 `model_used.metric_id` 是本次调用的指标 id（用于反馈回写）。

7. **用户反馈**
   ```bash
   curl -X POST http://localhost:8000/api/v1/chat/feedback \
     -H "Authorization: Bearer *** \
     -H 'Content-Type: application/json' \
     -d '{"metric_id": 1, "feedback": 1}'
   ```

8. **管理员看指标汇总**
   ```bash
   curl "http://localhost:8000/api/v1/admin/metrics/summary?days=1" \
     -H "Authorization: Bearer *** \
   ```

### 关键环境变量

| 变量 | 必填 | 说明 |
|:----|:----|:----|
| `ENCRYPTION_KEY` | ✅ | Fernet 密钥，用于加密 provider 的 `api_key` 落库。**没有它服务无法启动** |
| `MINIMAX_API_KEY` | ⚠️ 内置 provider 需要 | 内置 minimax provider 的真实 API key（被 `ENCRYPTION_KEY` 加密后入库） |
| `DATABASE_URL` | ✅ | PostgreSQL DSN |

#### 生成 `ENCRYPTION_KEY`

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

输出形如 `P1bpcHvVWWQ696WirBRFSyTPbdyeQGfv3-cNiM_-bEw=` 的 44 字节 url-safe base64 字符串，写入 `.env` 后重启服务即可。

> ⚠️ **轮换 `ENCRYPTION_KEY` 会让所有已存 provider 的 `api_key` 全部失效**，需要重新 seed 或用新 key 重新写入。

### 端口说明

- **统一端口 `8000`**：docker compose 已将容器内 `8000` 映射到宿主机 `8000`
- 访问 Swagger UI：`http://localhost:8000/docs`
- 访问前端：`http://localhost:8000/`（由后端静态托管）

### 已知限制

- A/B 规则中的 `mapping` 只会路由到 `model_name`（**不区分** `model_type`），所以 `chat` 目标规则里应只放 chat 模型，否则会被上游以 "unknown model" 拒绝
- `/chat/feedback` 必须收到 `metric_id` 才能回写 —— chat 失败时无 `model_used`，前端应隐藏反馈按钮
- 负载压测参考 `scripts/load_test_chat.py`（20 并发，P95 < 10s 验收）

## 📝 License

MIT
