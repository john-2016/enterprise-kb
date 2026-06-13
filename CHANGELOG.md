# 📋 Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-13

### 🎉 首次正式版发布

基于 **MiniMax M3 + embo-01** 的企业级 RAG 知识库系统，支持 Docker 一键部署。

### ✨ 核心功能

- **RAG 智能问答**：基于 MiniMax M3（200k 上下文）+ embo-01（1536 维向量）的检索增强生成
- **文档管理**：支持 MD/PDF/TXT/DOCX 上传、自动切片、向量化、索引
- **用户与权限**：JWT 认证 + RBAC（admin/editor/viewer 三级）
- **审计日志**：完整记录所有用户操作
- **单页应用**：原生 HTML/CSS/JS 聊天界面，开箱即用
- **RESTful API**：FastAPI 自动生成 Swagger 文档（`/docs`）

### 🏗️ 技术栈

- **后端**：FastAPI (Python 3.11) + SQLAlchemy 2.0 (async)
- **数据库**：PostgreSQL 15 (Docker 容器)
- **向量库**：FAISS (内存)
- **嵌入模型**：MiniMax embo-01 (1536 维)
- **生成模型**：MiniMax M3 (200k 上下文)
- **部署**：Docker Compose（一键启动）

### 🐳 Docker 部署

```bash
docker compose up -d
docker exec enterprise-kb-app python -m scripts.init_db
docker exec enterprise-kb-app python -m scripts.seed
```

访问 `http://localhost:8000`，默认账号 `admin / admin123`。

### 🔐 安全特性

- bcrypt 密码哈希（兼容旧 passlib 哈希）
- JWT 强制 iss/aud claim
- 启动时校验 secret key 长度
- CORS 白名单
- 公共注册关闭（需管理员创建）
- 非 root 用户运行容器（L3）
- 异常脱敏（500 不暴露内部信息）
- 文件路径遍历防护
- 上传大小限制（50MB）

### 📝 文档

- `README.md` —— 快速上手
- `docs/ARCHITECTURE.md` —— 系统架构
- `docs/DEPLOYMENT.md` —— 部署指南
- `docs/CHANGELOG.md` —— 变更日志（本文件）
- `/docs` —— Swagger API 文档（运行时）

### 🐛 已知问题

- `chat/query` 接口在向量检索时偶尔返回 0 条 sources（待优化检索逻辑）
- 当前仅支持单实例部署（向量库在内存中，重启丢失）

### 🔜 下一步

- 接入持久化向量库（Milvus / Qdrant）
- 集成更完善的 RAG 评估
- 支持多租户
- 流式响应（SSE）

[1.0.0]: https://github.com/john-2016/enterprise-kb/releases/tag/v1.0.0
