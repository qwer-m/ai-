from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict

class APIRequest(BaseModel):
    requirement: str
    project_id: int
    base_url: Optional[str] = None
    api_path: Optional[str] = None
    test_types: Optional[List[str]] = None
    mode: str = "natural"  # "natural" | "structured"

class APITestEvalRequest(BaseModel):
    script: str
    execution_result: str
    project_id: int
    openapi_spec: Optional[str] = None

class ProxyRequest(BaseModel):
    method: str
    url: str
    headers: Dict[str, str] = Field(default_factory=dict)
    params: Dict[str, str] = Field(default_factory=dict)
    cookies: Dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    is_base64_body: bool = False
    timeout_ms: int = Field(default=0, ge=0, le=600_000)
    verify_ssl: bool = True
    follow_redirects: bool = True
    max_redirects: int = Field(default=20, ge=0, le=100)
    http_version: Literal["HTTP/1.x", "HTTP/2"] = "HTTP/1.x"
