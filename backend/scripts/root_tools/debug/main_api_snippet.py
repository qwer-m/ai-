
class ConfigValidateRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str

class ConfigSaveRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str

class ConfigDetectRequest(BaseModel):
    candidates: List[str]

@app.post("/api/config/validate")
async def validate_config(req: ConfigValidateRequest):
    try:
        # Create a temporary config object to use with AIClient factory
        temp_config = SystemConfig(
            provider=req.provider,
            model_name=req.model_name,
            # We need to manually handle key here because AIClient expects decrypted or handles it
            # But AIClient.from_config expects a SystemConfig with encrypted key (which it decrypts)
            # OR we can manually instantiate provider.
            base_url=req.base_url
        )
        
        # Manually create provider to avoid DB dependency/encryption complexity for validation
        provider = None
        if req.provider == "dashscope":
            provider = DashScopeProvider(req.api_key or "")
        elif req.provider in ["openai", "ollama", "local"]:
            provider = OpenAICompatibleProvider(
                base_url=req.base_url or "",
                api_key=req.api_key or "",
                model=req.model_name
            )
        
        if not provider:
            return {"valid": False, "error": f"Unknown provider: {req.provider}"}
            
        result = provider.test_connection()
        return {"valid": result["success"], "details": result}
        
    except Exception as e:
        logger.error(f"Config validation error: {str(e)}")
        return {"valid": False, "error": f"Validation exception: {str(e)}"}

@app.post("/api/config/save")
async def save_config(req: ConfigSaveRequest, db: Session = Depends(get_db)):
    try:
        # Create and activate config
        new_config = config_manager.create_config(
            db, 
            provider=req.provider,
            model_name=req.model_name,
            api_key=req.api_key,
            base_url=req.base_url,
            activate=True
        )
        
        # Update global AI Client
        new_client = ai_client.from_config(new_config)
        ai_client.update_provider(new_client.provider, new_client.model)
        
        return {"status": "success", "id": new_config.id}
    except Exception as e:
        logger.error(f"Config save error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/config/detect")
async def detect_local_services(req: ConfigDetectRequest):
    """
    Parallel detection of local services.
    """
    results = []
    
    async def check_url(url):
        try:
            # Try /v1/models or just /v1
            target = url.rstrip('/')
            if not target.endswith('/v1'):
                target += '/v1'
            
            # Use httpx to probe
            async with httpx.AsyncClient(timeout=2.0) as client:
                # Some services might not respond to GET /v1, try /v1/models
                try:
                    resp = await client.get(f"{target}/models")
                    if resp.status_code == 200:
                        data = resp.json()
                        models = []
                        if "data" in data:
                            models = data["data"]
                        return {
                            "url": url,
                            "success": True, 
                            "latency": 0, # Placeholder
                            "models": models
                        }
                except:
                    pass
                
                # Fallback to simple health check
                return {"url": url, "success": False, "error": "Not reachable"}
                
        except Exception as e:
            return {"url": url, "success": False, "error": str(e)}

    # Run checks in parallel
    tasks = [check_url(url) for url in req.candidates]
    scan_results = await asyncio.gather(*tasks)
    
    # Filter successful ones
    valid_services = [r for r in scan_results if r.get("success")]
    
    return {"services": valid_services}

@app.get("/api/config/test-stream")
async def test_stream(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    prompt: str = "Hi"
):
    """
    SSE endpoint for testing streaming response
    """
    async def event_generator():
        try:
            # Instantiate provider
            prov = None
            if provider == "dashscope":
                prov = DashScopeProvider(api_key or "")
            elif provider in ["openai", "ollama", "local"]:
                prov = OpenAICompatibleProvider(
                    base_url=base_url or "",
                    api_key=api_key or "",
                    model=model
                )
            
            if not prov:
                yield f"data: {json.dumps({'error': 'Unknown provider'})}\n\n"
                return

            # Generate stream
            # Note: Provider generate_stream might be sync generator, wrap it
            # But OpenAICompatibleProvider.generate_stream is sync generator yielding strings
            # We need to make it async compatible for StreamingResponse
            
            # Actually, FastAPI StreamingResponse accepts sync generators too, but it runs them in threadpool.
            # For better performance, we should iterate.
            
            iterator = prov.generate_stream([{"role": "user", "content": prompt}], model, max_tokens=50)
            
            for chunk in iterator:
                if chunk.startswith("Error:"):
                     yield f"data: {json.dumps({'error': chunk})}\n\n"
                else:
                     yield f"data: {json.dumps({'token': chunk})}\n\n"
                # Add small delay to simulate typing effect if it's too fast (local models)
                # await asyncio.sleep(0.02) 
            
            yield f"data: {json.dumps({'done': True})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/config/current")
async def get_current_config(db: Session = Depends(get_db)):
    config = config_manager.get_active_config(db)
    if not config:
        return {"active": False}
    
    return {
        "active": True,
        "provider": config.provider,
        "model_name": config.model_name,
        "base_url": config.base_url,
        "has_api_key": bool(config.api_key)
    }
