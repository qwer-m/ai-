#!/bin/bash
set -e

echo "=========================================="
echo "   AI Test Platform - Aliyun Deploy"
echo "=========================================="

# 0. 检查配置文件
echo "[0/5] Checking Configuration..."
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating .env file..."
    touch "$ENV_FILE"
fi

# 检查 DASHSCOPE_API_KEY (可选)
if ! grep -q "DASHSCOPE_API_KEY" "$ENV_FILE" || [ -z "$(grep "DASHSCOPE_API_KEY" "$ENV_FILE" | cut -d '=' -f2)" ]; then
    echo "提示: 未检测到 DASHSCOPE_API_KEY，您可以在部署完成后通过网页端[设置]进行配置。"
else
    echo "API Key 已配置。"
fi

# 1. 配置 Swap (针对 2G 内存服务器)
echo ""
echo "[1/5] Configuring Swap..."
if [ ! -f /swapfile ]; then
    echo "检测到未配置 Swap，正在创建 4G 虚拟内存..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap 配置完成。"
else
    echo "Swap 已配置，跳过。"
fi

# 2. 安装 Docker (如果未安装)
echo ""
echo "[2/5] Checking Docker..."
if ! command -v docker &> /dev/null; then
    echo "正在安装 Docker..."
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
    sudo systemctl start docker
    sudo systemctl enable docker
else
    echo "Docker 已安装，跳过。"
fi

# 3. 配置 Docker 镜像加速器 (修复 Docker Hub 拉取失败)
echo ""
echo "[3/5] Configuring Docker Registry Mirror..."
if [ ! -f /etc/docker/daemon.json ]; then
    sudo mkdir -p /etc/docker
    sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://mirror.baidubce.com",
    "https://docker.nju.edu.cn"
  ]
}
EOF
    sudo systemctl daemon-reload
    sudo systemctl restart docker
    echo "Docker 镜像加速器已配置。"
else
    echo "Docker 配置已存在，跳过镜像加速配置。"
fi

# 4. 检查端口占用
echo ""
echo "[4/5] Checking Ports..."
if lsof -Pi :80 -sTCP:LISTEN -t >/dev/null ; then
    echo "警告: 80 端口已被占用。Nginx 容器可能无法启动。"
    echo "您可以尝试运行 'systemctl stop nginx' 或 'systemctl stop apache2' 来释放端口。"
fi

# 5. 构建并启动容器
echo ""
echo "[5/5] Starting Services..."
# 尝试使用 docker compose (新版) 或 docker-compose (旧版)
if docker compose version &> /dev/null; then
    docker compose -f docker-compose.prod.yml up -d --build
elif command -v docker-compose &> /dev/null; then
    docker-compose -f docker-compose.prod.yml up -d --build
else
    echo "错误: 未找到 docker compose 或 docker-compose 命令。"
    exit 1
fi

echo ""
echo "=========================================="
echo "   Deployment Complete!"
echo "=========================================="
echo "请访问服务器公网 IP 查看效果。"
echo "API 文档地址: http://<服务器IP>/docs"
