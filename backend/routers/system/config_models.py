from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigValidateRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str
    vl_model_name: Optional[str] = None
    turbo_model_name: Optional[str] = None
    turbo_provider: Optional[str] = None
    turbo_api_key: Optional[str] = None
    turbo_base_url: Optional[str] = None
    turbo_follow_main: Optional[bool] = True
    review_model_name: Optional[str] = None
    review_provider: Optional[str] = None
    review_api_key: Optional[str] = None
    review_base_url: Optional[str] = None
    review_follow_main: Optional[bool] = True
    vl_provider: Optional[str] = None
    vl_api_key: Optional[str] = None
    vl_base_url: Optional[str] = None
    vl_follow_main: Optional[bool] = True
    tesseract_path: Optional[str] = None
    tesseract_manual_override: Optional[bool] = None


class ConfigSaveRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str
    vl_model_name: Optional[str] = None
    turbo_model_name: Optional[str] = None
    turbo_provider: Optional[str] = None
    turbo_api_key: Optional[str] = None
    turbo_base_url: Optional[str] = None
    turbo_follow_main: Optional[bool] = True
    review_model_name: Optional[str] = None
    review_provider: Optional[str] = None
    review_api_key: Optional[str] = None
    review_base_url: Optional[str] = None
    review_follow_main: Optional[bool] = True
    vl_provider: Optional[str] = None
    vl_api_key: Optional[str] = None
    vl_base_url: Optional[str] = None
    vl_follow_main: Optional[bool] = True
    tesseract_path: Optional[str] = None
    tesseract_manual_override: Optional[bool] = None


class ConfigDetectRequest(BaseModel):
    candidates: List[str]


class ConfigQuotaRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None


class ConfigTestStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=256)
    api_key: Optional[str] = Field(default=None, max_length=8192)
    base_url: Optional[str] = Field(default=None, max_length=2048)
    prompt: str = Field(default="你好，请简短回复连接成功。", min_length=1, max_length=2000)

    @field_validator("provider", "model_name", "prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("api_key", "base_url")
    @classmethod
    def strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
