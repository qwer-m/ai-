import yaml
import os
from typing import Dict, Any, Optional

class PromptLoader:
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
        支持基于 last_modified 的缓存机制
        :param prompt_name: 配置文件名（不含 .yaml 后缀）
        :return: 解析后的字典配置
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
        获取渲染后的 System Prompt
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
