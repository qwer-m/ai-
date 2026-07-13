from pydantic import BaseModel
from typing import Optional, List, Dict

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
    headers: Dict[str, str] = {}
    params: Dict[str, str] = {}
    cookies: Dict[str, str] = {}
    body: Optional[str] = None
    is_base64_body: bool = False
    timeout: int = 30
    verify_ssl: bool = True
    follow_redirects: bool = True
    max_redirects: int = 20
    http_version: str = "HTTP/1.1"
