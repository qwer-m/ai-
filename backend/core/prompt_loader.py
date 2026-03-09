"""
Prompt 加载模块 (Prompt Loader Module)

该模块负责加载和管理 System Prompt 配置文件 (YAML)。
主要功能：
1. 加载 YAML 配置文件 (load_prompt): 支持基于文件修改时间的缓存机制，减少磁盘 IO。
2. 渲染 Prompt (get_rendered_prompt): 使用 Python 字符串格式化填充动态参数。

设计模式：
- 单例模式 (Singleton): 确保全局只有一个加载器实例，共享缓存。
"""

import yaml
import os
from typing import Dict, Any, Optional

class PromptLoader:
    """
    Prompt 加载器单例类
    """
    _instance = None
    _prompts: Dict[str, Any] = {}
    _prompt_timestamps: Dict[str, float] = {}
    _base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PromptLoader, cls).__new__(cls)
        return cls._instance

    def load_prompt(self, prompt_name: str) -> Optional[Dict[str, Any]]:
        """
        加载指定名称的 Prompt 配置文件 (YAML)
        
        支持基于 last_modified 的缓存机制：
        如果文件未修改，直接返回内存中的缓存数据。
        
        Args:
            prompt_name: 配置文件名（不含 .yaml 后缀）。
            
        Returns:
            Optional[Dict[str, Any]]: 解析后的字典配置，加载失败返回 None。
        """
        file_path = os.path.join(self._base_path, f"{prompt_name}.yaml")
        if not os.path.exists(file_path):
            # Fallback for reliability: Return a minimal default if file is missing
            return None

        try:
            mtime = os.path.getmtime(file_path)

            # Check cache validity
            if prompt_name in self._prompts and prompt_name in self._prompt_timestamps:
                if self._prompt_timestamps[prompt_name] == mtime:
                    return self._prompts[prompt_name]

            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                self._prompts[prompt_name] = data
                self._prompt_timestamps[prompt_name] = mtime
                return data
        except Exception as e:
            print(f"Failed to load prompt {prompt_name}: {e}")
            return None

    def get_rendered_prompt(self, prompt_name: str, **kwargs) -> str:
        """
        获取渲染后的 System Prompt (Get Rendered Prompt)
        
        加载 Prompt 配置并使用 kwargs 填充占位符。
        
        Args:
            prompt_name: Prompt 名称。
            **kwargs: 用于格式化 Prompt 的参数。
            
        Returns:
            str: 渲染后的完整 System Prompt 字符串。
        """
        config = self.load_prompt(prompt_name)
        if not config:
            return ""
        
        system_prompt = config.get("system_prompt", "")
        # Simple string formatting
        try:
            return system_prompt.format(**kwargs)
        except KeyError:
            # If kwargs are missing, return raw prompt or handle gracefully
            return system_prompt

prompt_loader = PromptLoader()
