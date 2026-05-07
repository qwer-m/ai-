from __future__ import annotations

from typing import Optional, List

from pydantic import BaseModel


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
