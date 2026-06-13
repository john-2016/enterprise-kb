# 📚 企业知识库 — Enterprise Knowledge Base

基于 **MiniMax M3 + embo-01** 的企业级 RAG 知识库系统。

## 🏗️ 架构

```
用户 → 聊天界面/API → FastAPI → 文档切片 → embo-01 向量化 → FAISS 检索
                                                      ↓
                                       MiniMax-M3 生成回答 ← 拼装 Prompt
```

## 🚀 快速启动

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 MiniMax API Key
```

### 2. Docker 部署（推荐）

```bash
docker compose up -d
# 访问 http://localhost:8000
```

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

| 角色 | 用户名 | 密码 |
|:----|:------|:----|
| 管理员 | admin | admin123 |
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

## 📝 License

MIT
