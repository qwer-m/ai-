"""
文件处理模块 (File Processing Module)

该模块负责解析上传的文件内容，支持多种格式。
主要功能：
1. PDF 解析: 使用 pypdf 提取文本。
2. CSV 解析: 使用 pandas 转换为 CSV 字符串。
3. 图片处理: 转换为 Base64 编码 (OCR 预处理)。
4. 文本文件: 直接解码 UTF-8 内容。

被调用方：
- modules.knowledge_base (上传文档时解析内容)
"""

import io
import pypdf
import pandas as pd
from fastapi import UploadFile
import base64
from core.ai_client import ai_client
from typing import Optional

async def parse_file_content(file: UploadFile, image_prompt: str = "OCR: Extract all text from this image.") -> str:
    """
    解析文件内容 (Parse File Content)
    
    根据文件扩展名自动选择解析策略。
    
    Args:
        file: FastAPI UploadFile 对象。
        image_prompt: 图片 OCR 的提示词 (当前暂未完全实现 OCR 逻辑，仅占位)。
        
    Returns:
        str: 解析后的文本内容。
    """
    filename = file.filename.lower()
    content_bytes = await file.read()
    text_content = ""

    try:
        if filename.endswith(".pdf"):
            # Handle PDF
            pdf_file = io.BytesIO(content_bytes)
            reader = pypdf.PdfReader(pdf_file)
            for page in reader.pages:
                text_content += page.extract_text() + "\n"
        
        elif filename.endswith(".csv"):
            # Handle CSV
            csv_file = io.BytesIO(content_bytes)
            try:
                df = pd.read_csv(csv_file)
                text_content = df.to_csv(index=False)
            except Exception as e:
                 # Fallback
                 try:
                    text_content = content_bytes.decode("utf-8")
                 except:
                    text_content = f"[Error reading CSV: {str(e)}]"
        
        elif filename.endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp')):
             # Handle Image (OCR)
             try:
                 encoded_image = base64.b64encode(content_bytes).decode('utf-8')
                 # Placeholder for OCR
                 text_content = f"[Image Content: {filename}]"
             except Exception as e:
                 text_content = f"[Error processing image: {str(e)}]"
        
        else:
            # Try text
            try:
                text_content = content_bytes.decode("utf-8")
            except:
                text_content = f"[Unsupported file type: {filename}]"

    except Exception as e:
        text_content = f"[Error parsing file: {str(e)}]"

    return text_content