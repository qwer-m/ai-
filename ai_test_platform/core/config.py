#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置模块

该模块定义了项目的所有配置参数，包括：
1. AI模型配置
2. 数据库配置
3. UI自动化配置
4. API测试配置

所有配置参数集中管理，便于维护和修改。
"""

import os
import urllib.parse


class Config:
    """配置类，包含项目所有配置参数"""
    
    # ===========================
    # AI模型配置
    # ===========================
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")  # DashScope API密钥
    MODEL_NAME = "qwen-plus"  # 主模型名称
    VL_MODEL_NAME = "qwen3-vl-plus-2025-12-19"  # 视觉语言模型，用于OCR等图像处理
    TURBO_MODEL_NAME = "qwen-turbo"  # 轻量模型，用于上下文压缩和摘要生成
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "10000"))  # 最大输出token数
    
    # ===========================
    # 数据库配置
    # ===========================
    DB_USER = "root"  # 数据库用户名
    DB_PASSWORD = urllib.parse.quote_plus("Wtk1314521!")  # 数据库密码（URL编码）
    DB_HOST = "localhost"  # 数据库主机
    DB_PORT = "3306"  # 数据库端口
    DB_NAME = "ai_test_platform"  # 数据库名称
    
    # 处理用户名中的特殊字符（如果有）
    DB_USER_ENCODED = urllib.parse.quote_plus(DB_USER)
    
    # 数据库连接URL
    DATABASE_URL = f"mysql+pymysql://{DB_USER_ENCODED}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

    # ===========================
    # UI自动化配置
    # ===========================
    HEADLESS_MODE = True  # 是否启用无头模式（无界面运行浏览器）
    
    # ===========================
    # API测试配置
    # ===========================
    DEFAULT_TIMEOUT = 10  # API测试默认超时时间（秒）

    # ===========================
    # Redis配置（用于健康检查）
    # ===========================
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

    # ===========================
    # 安全配置
    # ===========================
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days


# 创建配置实例
settings = Config()
