from pydantic import BaseModel
from typing import Any, Optional

# 中文注释：错误管理请求体，接收任意错误对象并返回中文提示
class ErrorTranslateRequest(BaseModel):
    error: Any
    context: Optional[str] = None
