# AI辅助测试平台技术栈文档

## 1. 项目概览
本项目是一个AI驱动的自动化测试辅助平台，旨在通过大模型（LLM）能力提升测试用例生成、UI自动化测试及API测试的效率与质量。系统采用典型的前后端分离架构，前端使用现代 React 技术栈，后端基于 Python FastAPI 构建。

## 2. 前端技术栈 (Frontend)
前端应用位于 `frontend/` 目录，采用单页应用 (SPA) 架构。

### 核心框架与构建工具
- **框架**: [React 19](https://react.dev/) - 最新版本的 React，提供并发渲染等高级特性。
- **构建工具**: [Vite 7](https://vitejs.dev/) - 极速的现代化前端构建工具，提供秒级热更新。
- **语言**: [TypeScript 5](https://www.typescriptlang.org/) - 强类型 JavaScript 超集，提升代码健壮性。

### UI 组件库与样式
- **UI 库**: [React Bootstrap](https://react-bootstrap.github.io/) (Bootstrap 5) - 基于 Bootstrap 的 React 组件库，提供成熟的响应式布局。
- **CSS 预处理**: [Sass](https://sass-lang.com/) (`.scss`) - CSS 扩展语言，支持嵌套规则、变量与混合。
- **样式方案**: 模块化 SCSS + Bootstrap Utility Classes。

### 状态管理与路由
- **路由**: [React Router 7](https://reactrouter.com/) - 处理客户端路由跳转。
- **状态管理**: React Hooks (`useState`, `useEffect`, `useContext`) - 利用 React 内置能力管理组件状态。
- **HTTP 客户端**: [Axios](https://axios-http.com/) - 处理 API 请求与拦截。

### 数据可视化
- **图表库**: [Chart.js](https://www.chartjs.org/) + [react-chartjs-2](https://react-chartjs-2.js.org/) - 用于展示测试统计、覆盖率等数据图表。

### 开发环境配置
- **开发服务器**: 运行于 `http://localhost:5173`。
- **API 代理**: 通过 Vite 的 `server.proxy` 将 `/api` 请求转发至后端 `http://localhost:8000`，解决跨域问题。

---

## 3. 后端技术栈 (Backend)
后端服务位于 `ai_test_platform/` 目录，提供 RESTful API 服务及 AI 处理能力。

### 核心框架
- **Web 框架**: [FastAPI](https://fastapi.tiangolo.com/) - 高性能、易于使用的 Python Web 框架，自动生成 OpenAPI 文档。
- **ASGI 服务器**: [Uvicorn](https://www.uvicorn.org/) - 生产级 ASGI 服务器，支持异步处理。

### 数据库与 ORM
- **数据库**: MySQL 8.0 - 关系型数据库，存储项目、用例、日志等核心数据。
- **ORM**: [SQLAlchemy](https://www.sqlalchemy.org/) - Python SQL 工具包与对象关系映射器。
- **驱动**: `pymysql` - 纯 Python 实现的 MySQL 客户端。
- **连接池**: 配置了 `pool_size`, `max_overflow` 等参数以优化并发性能。

### AI 与大模型集成
- **SDK**: [DashScope](https://help.aliyun.com/zh/dashscope/) (阿里云百炼) - 用于调用通义千问系列模型。
- **模型**:
  - `qwen-plus`: 主模型，用于复杂逻辑处理。
  - `qwen3-vl-plus-2025-12-19`: 视觉模型，用于 UI 截图分析与 OCR。
  - `qwen-turbo`: 轻量模型，用于摘要生成与长文本压缩。

### 自动化测试引擎
- **UI 自动化**: [Playwright](https://playwright.dev/python/) - 现代 Web 自动化工具，支持无头模式 (`HEADLESS_MODE=True`)。
- **移动端自动化**: [Appium Python Client](https://github.com/appium/python-client) - 支持移动应用自动化测试。
- **测试运行器**: [Pytest](https://docs.pytest.org/) - 强大的 Python 测试框架，用于执行生成的测试脚本。

### 数据处理与工具
- **文档解析**: `pypdf` (PDF), `openpyxl` (Excel), `pandas` (数据分析)。
- **图像处理**: `Pillow` - 用于图像格式转换与预处理。
- **模板引擎**: `Jinja2` - (注：主要用于旧版兼容或邮件生成，新版前端已接管页面渲染)。

---

## 4. 基础设施与环境
- **操作系统**: Windows (当前环境), Linux (生产环境兼容)。
- **包管理**: `npm` (前端), `pip` (后端)。
- **服务端口**:
  - 前端: 5173 (Dev)
  - 后端: 8000 (API)
  - 数据库: 3306 (MySQL), 6379 (Redis - 可选/健康检查支持)

## 5. 架构图示
```mermaid
graph TD
    Client[浏览器 (React SPA)] -->|HTTP/JSON| Proxy[Vite Proxy / Nginx]
    Proxy -->|转发 /api| API[FastAPI 后端服务]
    
    subgraph "后端服务 (Python)"
        API --> Controller[路由控制器]
        Controller --> Service[业务逻辑层]
        Service --> AI[DashScope LLM 服务]
        Service --> Auto[Playwright/Appium 引擎]
        Service --> DB[(MySQL 数据库)]
    end
```
