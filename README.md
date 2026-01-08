# AI Test Platform

一个基于 AI 的自动化测试辅助平台，支持测试用例生成、UI 自动化测试、API 测试管理等功能。

## 功能特性

- 🤖 **AI 测试生成**：基于需求文档自动生成测试用例
- 🖥️ **UI 自动化**：支持 Playwright 录制与回放
- 🔌 **API 测试**：接口测试管理与自动化执行
- 📊 **测试报告**：生成详细的测试执行报告
- 🧠 **知识库**：基于 RAG 的测试知识管理

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 16+
- MySQL 8.0+
- Redis

### 1. 后端设置

```bash
# 进入项目目录
cd ai_test_platform

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
# 复制 .env.example 为 .env 并填入您的配置
cp ../.env.example .env

# 初始化数据库 (确保 MySQL 已启动并创建了数据库)
# 您可能需要根据 models.py 自动创建表，或使用 alembic 迁移
```

### 2. 前端设置

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

### 3. 启动服务

**方式一：一键启动 (推荐)**
在根目录运行：
```bash
python ai_test_platform/start_dev.py
```

**方式二：手动启动**
需要分别启动以下服务：
1. Backend API: `uvicorn main:app --reload`
2. Celery Worker: `celery -A celery_worker.celery_app worker -P solo`
3. Frontend: `npm run dev`

## 部署

本项目包含 Docker 部署配置，支持一键部署到服务器。

```bash
# 部署脚本位于 deploy/ 目录
./deploy/deploy_aliyun.sh
```

## 技术栈

- **Frontend**: React, TypeScript, Vite, Ant Design
- **Backend**: FastAPI, SQLAlchemy, Celery
- **AI/ML**: LangChain, ChromaDB, DashScope
- **Infrastructure**: Docker, Nginx, MySQL, Redis
