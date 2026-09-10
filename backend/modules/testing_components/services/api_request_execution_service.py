from __future__ import annotations

import base64
from typing import Any

import httpx

from core.authn.diagnostic_access import validate_outbound_http_url
from schemas.automation.api_testing import ProxyRequest


class ApiRequestExecutionService:
    """执行标准接口测试请求并返回可直接展示的结构化响应。"""

    async def execute(self, request: ProxyRequest) -> dict[str, Any]:
        url = validate_outbound_http_url(request.url)
        timeout = None if request.timeout_ms == 0 else request.timeout_ms / 1000
        body = self._decode_body(request)

        async with httpx.AsyncClient(
            timeout=timeout,
            verify=request.verify_ssl,
            follow_redirects=request.follow_redirects,
            max_redirects=request.max_redirects,
            http2=request.http_version == "HTTP/2",
        ) as client:
            response = await client.request(
                method=request.method,
                url=url,
                headers=request.headers,
                params=request.params,
                cookies=request.cookies,
                content=body,
            )

        response_body, is_binary = self._response_body(response)
        return {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": response_body,
            "cookies": {
                cookie.name: {
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }
                for cookie in response.cookies.jar
            },
            "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 2),
            "is_binary": is_binary,
            "url": str(response.url),
        }

    @staticmethod
    def _decode_body(request: ProxyRequest) -> str | bytes | None:
        if request.body is None:
            return None
        if not request.is_base64_body:
            return request.body

        encoded = request.body
        if encoded.startswith("data:") and ";base64," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("请求体不是有效的 Base64 数据") from exc

    @staticmethod
    def _response_body(response: httpx.Response) -> tuple[str, bool]:
        content_type = response.headers.get("content-type", "").lower()
        textual = (
            content_type.startswith("text/")
            or "json" in content_type
            or "xml" in content_type
            or "javascript" in content_type
            or "x-www-form-urlencoded" in content_type
        )
        if textual:
            return response.text, False
        return base64.b64encode(response.content).decode("ascii"), True
