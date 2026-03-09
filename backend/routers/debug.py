from fastapi import APIRouter, Depends
from core.models import User
from core.auth import get_current_user
from schemas.api_testing import ProxyRequest
import httpx
import asyncio
import base64

router = APIRouter(
    prefix="/debug",
    tags=["Debug"]
)

@router.post("/request")
async def debug_request(req: ProxyRequest, current_user: User = Depends(get_current_user)):
    try:
        content_body = None
        if req.body:
            if req.is_base64_body:
                try:
                    # Fix: Handle data URI scheme if present (e.g. data:image/png;base64,...)
                    if ',' in req.body:
                        # Check if it looks like a data URI
                        header, data = req.body.split(',', 1)
                        if ';base64' in header:
                            content_body = base64.b64decode(data)
                        else:
                            # Not a base64 data URI, treat whole as base64 or plain
                            content_body = base64.b64decode(req.body)
                    else:
                        content_body = base64.b64decode(req.body)
                except Exception:
                    # Fallback if decode fails
                    content_body = req.body.encode('utf-8') 
            else:
                content_body = req.body

        # Configure client based on settings
        http2 = req.http_version == "HTTP/2"
        
        async with httpx.AsyncClient(
            timeout=req.timeout, 
            verify=req.verify_ssl,
            follow_redirects=req.follow_redirects,
            max_redirects=req.max_redirects if req.follow_redirects else 20,
            http2=http2
        ) as client:
            response = await client.request(
                method=req.method,
                url=req.url,
                headers=req.headers,
                params=req.params,
                cookies=req.cookies,
                content=content_body
            )
            
            # Extract detailed cookies
            detailed_cookies = {}
            for cookie in response.cookies.jar:
                detailed_cookies[cookie.name] = {
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }

            # Handle binary content
            try:
                text_content = response.text
                is_binary = False
            except Exception:
                text_content = base64.b64encode(response.content).decode('utf-8')
                is_binary = True

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": text_content,
                "cookies": detailed_cookies,
                "elapsed_time": response.elapsed.total_seconds(),
                "is_binary": is_binary,
                "url": str(response.url)
            }
            
    except httpx.RequestError as e:
        return {
            "error": f"Request failed: {str(e)}",
            "type": "NetworkError"
        }
    except Exception as e:
        return {
            "error": f"Internal error: {str(e)}",
            "type": "InternalError"
        }
