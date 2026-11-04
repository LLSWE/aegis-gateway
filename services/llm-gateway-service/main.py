import os
import time
import asyncio
from typing import Optional, Any
from fastapi import FastAPI, Header, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import httpx

from database import check_and_record_rate_limit
from guardrails import StreamingPIIRedactor

app = FastAPI(title="Aegis LLM Security & Routing Gateway", version="0.1.0")

# Configuration for upstream model providers (can be overridden via environment variables)
PRIMARY_PROVIDER_URL = os.getenv("PRIMARY_PROVIDER_URL", "http://localhost:8000/mock-primary/v1/chat/completions")
SECONDARY_PROVIDER_URL = os.getenv("SECONDARY_PROVIDER_URL", "http://localhost:8000/mock-secondary/v1/chat/completions")

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: Optional[bool] = False
    max_tokens: Optional[int] = 500

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    # Extract API key
    api_key = x_api_key
    if not api_key and authorization:
        parts = authorization.split(" ")
        if len(parts) == 2 and parts[0].lower() == "bearer":
            api_key = parts[1]
    if not api_key:
        api_key = "default-tenant-key"

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_JSON", "message": "Malformed JSON body"}}
        )

    # Estimate tokens for rate limiting (prompt length + requested max_tokens)
    prompt_text = "".join([str(m.get("content", "")) for m in body.get("messages", [])])
    estimated_tokens = int(len(prompt_text) / 4) + body.get("max_tokens", 500)

    # Check sliding window rate limit (50,000 tokens / 60 seconds)
    allowed, current_usage = check_and_record_rate_limit(api_key, estimated_tokens, limit=50000, window_seconds=60)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Token rate limit exceeded (50,000 tokens/minute per tenant). Please slow down.",
                    "current_usage": current_usage,
                    "limit": 50000
                }
            }
        )

    # Model Failover Router logic with 3000ms timeout
    client = httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=3.0, read=3.0))
    upstream_url = PRIMARY_PROVIDER_URL
    is_secondary = False

    async def make_upstream_request(url: str):
        return await client.post(url, json=body, headers={"Authorization": authorization or ""})

    try:
        response = await make_upstream_request(PRIMARY_PROVIDER_URL)
        if response.status_code == 429 or response.status_code >= 500:
            # Trigger failover to secondary provider
            is_secondary = True
            upstream_url = SECONDARY_PROVIDER_URL
            response = await make_upstream_request(SECONDARY_PROVIDER_URL)
    except (httpx.TimeoutException, httpx.RequestError):
        # Timeout or network error on primary -> failover to secondary
        try:
            is_secondary = True
            upstream_url = SECONDARY_PROVIDER_URL
            response = await make_upstream_request(SECONDARY_PROVIDER_URL)
        except Exception as e:
            await client.aclose()
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "code": "UPSTREAM_UNAVAILABLE",
                        "message": "Both primary and secondary LLM providers failed or timed out."
                    }
                }
            )

    await client.aclose()

    if response.status_code != 200:
        return JSONResponse(
            status_code=response.status_code,
            content={
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": f"Upstream model provider returned error status {response.status_code}",
                    "provider": "secondary" if is_secondary else "primary"
                }
            }
        )

    # Handle streaming vs non-streaming responses with PII Guardrail
    is_streaming = body.get("stream", False)

    if is_streaming:
        async def event_stream():
            redactor = StreamingPIIRedactor()
            async with httpx.AsyncClient() as stream_client:
                async with stream_client.stream("POST", upstream_url, json=body, headers={"Authorization": authorization or ""}) as upstream_resp:
                    async for chunk in upstream_resp.aiter_text():
                        redacted_chunk = redactor.process_chunk(chunk)
                        if redacted_chunk:
                            yield redacted_chunk
                    final_flush = redactor.flush()
                    if final_flush:
                        yield final_flush

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    else:
        resp_data = response.json()
        # Redact PII in choices content
        if "choices" in resp_data:
            for choice in resp_data["choices"]:
                if "message" in choice and "content" in choice["message"]:
                    choice["message"]["content"] = redactor_text_full(choice["message"]["content"]) # helper or direct
        return JSONResponse(status_code=200, content=resp_data)

def redactor_text_full(text: str) -> str:
    from guardrails import redact_text
    return redact_text(text)

# Mock Upstream Providers for Testing and Local Verification
@app.post("/mock-primary/v1/chat/completions")
async def mock_primary(request: Request):
    body = await request.json()
    if body.get("model") == "trigger-timeout":
        await asyncio.sleep(4.0) # exceeds 3000ms timeout
    if body.get("model") == "trigger-429":
        return JSONResponse(status_code=429, content={"error": "Too Many Requests"})
    
    is_stream = body.get("stream", False)
    if is_stream:
        async def mock_stream():
            yield "data: {\"choices\": [{\"delta\": {\"content\": \"Hello, my email is alice@example.com\"}}]}\n\n"
            await asyncio.sleep(0.1)
            yield "data: {\"choices\": [{\"delta\": {\"content\": \" and my SSN is 123-45-6789.\"}}]}\n\n"
        return StreamingResponse(mock_stream(), media_type="text/event-stream")
    else:
        return {
            "id": "chatcmpl-primary-1",
            "choices": [{"message": {"role": "assistant", "content": "Hello! Contact me at bob@company.com or card 4532-1234-5678-9012."}}]
        }

@app.post("/mock-secondary/v1/chat/completions")
async def mock_secondary(request: Request):
    body = await request.json()
    is_stream = body.get("stream", False)
    if is_stream:
        async def mock_sec_stream():
            yield "data: {\"choices\": [{\"delta\": {\"content\": \"[Secondary] Hi! Secret contact: charlie@test.org\"}}]}\n\n"
        return StreamingResponse(mock_sec_stream(), media_type="text/event-stream")
    else:
        return {
            "id": "chatcmpl-secondary-1",
            "choices": [{"message": {"role": "assistant", "content": "[Secondary Backup] Hello from backup provider! SSN: 987-65-4321."}}]
        }

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "llm-gateway-service"}
