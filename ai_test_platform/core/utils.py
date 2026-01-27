"""
通用工具模块 (Core Utils Module)

提供系统级的通用辅助函数，包括：
1. 代码块提取 (Markdown解析)
2. 临时脚本执行 (安全沙箱/子进程管理)
"""

import os
import subprocess
import tempfile
from typing import Optional, Tuple
import logging

# Configure logger
# 全局日志配置 (Global Logger Configuration)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_test_platform")

def extract_code_block(text: str, language: Optional[str] = None) -> str:
    """
    从Markdown文本中提取代码块 (Extract Code Block)
    
    解析Markdown格式的响应，提取被 ``` 包裹的代码内容。
    优先匹配指定语言，如果未指定或未找到，则尝试提取通用代码块。
    
    Args:
        text: 包含Markdown的原始文本。
        language: 目标编程语言 (如 "python", "json")。
        
    Returns:
        str: 提取出的纯代码内容 (去除Markdown标记)。
    """
    if not text:
        return ""
        
    # Try with specific language first if provided
    if language and f"```{language}" in text:
        return text.split(f"```{language}")[1].split("```")[0].strip()
    
    # Try generic markdown
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3: # ```code```
            return parts[1].strip()
        elif len(parts) == 2: # Unclosed or malformed
            # Check if the part starts with language name
            content = parts[1]
            if language and content.startswith(language):
                content = content[len(language):]
            return content.strip()
            
    return text.strip()

def run_temp_script(
    script_content: str, 
    suffix: str = ".py", 
    command: list[str] = None, 
    timeout: int = 30
) -> Tuple[str, str, int]:
    """
    执行临时脚本 (Run Temp Script)
    
    将字符串内容写入系统临时文件，并使用指定命令在子进程中执行。
    用于执行动态生成的测试脚本或辅助脚本。
    
    Args:
        script_content: 脚本代码内容。
        suffix: 临时文件后缀 (如 .py, .sh)，决定了文件类型。
        command: 执行命令前缀 (默认 ["python"])。
        timeout: 执行超时时间 (秒)，防止脚本死循环。
        
    Returns:
        Tuple[str, str, int]: (标准输出 stdout, 标准错误 stderr, 返回码 returncode)。
    """
    if command is None:
        command = ["python"]
        
    with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False, encoding='utf-8') as tmp:
        tmp.write(script_content)
        tmp_path = tmp.name
        
    try:
        # Prepare command
        full_command = command + [tmp_path]
        
        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Execution timed out after {timeout} seconds", -1
    except Exception as e:
        return "", f"Execution failed: {str(e)}", -1
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

