# 阿里云服务器部署指南

## 1. 准备工作

在本地电脑上完成以下步骤：

1.  **打包前端代码**：
    进入 `frontend` 目录，运行：
    ```bash
    cd frontend
    npm run build
    ```
    这将生成 `dist` 目录。

2.  **准备上传**：
    确保你已经生成了 `ai_test_platform/deploy` 目录下的配置文件，以及 `ai_test_platform/docker-compose.prod.yml`。

## 2. 上传文件到服务器

使用 SCP、WinSCP 或其他工具将整个项目文件夹（包含 `ai_test_platform` 和 `frontend/dist`）上传到服务器。

建议上传路径：`/root/ai_test_platform`

上传内容应包含：
- `ai_test_platform/` (后端代码、Dockerfile、docker-compose.prod.yml、deploy目录)
- `frontend/dist/` (前端构建产物)

**注意**：不需要上传 `frontend/node_modules` 和 `ai_test_platform/__pycache__` 等临时文件。

## 3. 服务器端操作

通过 SSH 登录到阿里云服务器：

```bash
ssh root@<你的服务器公网IP>
```

进入项目目录：

```bash
cd /root/ai_test_platform/ai_test_platform
```

赋予脚本执行权限并运行：

```bash
chmod +x deploy/deploy_aliyun.sh
./deploy/deploy_aliyun.sh
```

脚本会自动：
1. 配置 4G Swap 虚拟内存（防止 2G 内存不足）。
2. 安装 Docker 环境。
3. 启动所有服务（MySQL, Redis, Backend, Worker, Nginx）。

## 4. 验证部署

部署完成后，在浏览器访问：
- 平台首页：`http://<你的服务器公网IP>`
- API 文档：`http://<你的服务器公网IP>/docs`

## 常见问题

- **端口无法访问**：请检查阿里云控制台 -> 实例 -> 安全组，确保 **入方向** 允许了 **80** 端口。
- **数据库连接失败**：检查 `docker-compose.prod.yml` 中的密码是否与代码中一致（默认已配置为 testpass）。
