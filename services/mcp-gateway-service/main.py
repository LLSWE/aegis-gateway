import sys
import os
import json
import asyncio
from typing import Optional, Any
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Aegis MCP Security Gateway Proxy", version="0.1.0")

DOWNSTREAM_SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../mcp-service/server.py")
)
PYTHON_EXEC = sys.executable

class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: Optional[dict[str, Any]] = None
    id: Optional[Any] = None

def get_user_role(authorization: Optional[str]) -> str:
    if not authorization:
        return "viewer"
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return "viewer"
    token = parts[1]
    if "admin" in token.lower() or token == "admin-token-123":
        return "admin"
    return "viewer"

async def call_downstream_mcp(request_data: dict) -> dict:
    env = os.environ.copy()
    try:
        proc = await asyncio.create_subprocess_exec(
            PYTHON_EXEC,
            DOWNSTREAM_SERVER_PATH,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": request_data.get("id"),
            "error": {"code": -32603, "message": f"Failed to spawn downstream MCP server: {str(e)}"}
        }
    
    payload_str = json.dumps(request_data) + "\n"
    try:
        stdout_data, stderr_data = await asyncio.wait_for(
            proc.communicate(input=payload_str.encode()),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "jsonrpc": "2.0",
            "id": request_data.get("id"),
            "error": {"code": -32603, "message": "Downstream MCP server timeout"}
        }
    
    for line in stdout_data.decode().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if "jsonrpc" in parsed and ("result" in parsed or "error" in parsed):
                if request_data.get("id") is None or parsed.get("id") == request_data.get("id"):
                    return parsed
        except json.JSONDecodeError:
            continue
            
    return {
        "jsonrpc": "2.0",
        "id": request_data.get("id"),
        "error": {"code": -32603, "message": "No valid JSON-RPC response from downstream MCP"}
    }

@app.post("/mcp")
async def mcp_proxy(request: Request, authorization: Optional[str] = Header(None)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON"}}
        )
    
    role = get_user_role(authorization)
    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params", {}) or {}

    if method == "tools/call":
        tool_name = params.get("name", "")
        is_admin_tool = tool_name.startswith("admin_") or tool_name == "trigger_refund"
        
        if is_admin_tool and role != "admin":
            return JSONResponse(
                status_code=200,
                content={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32001,
                        "message": "Unauthorized Tool Call"
                    }
                }
            )

    response_data = await call_downstream_mcp(body)
    return JSONResponse(status_code=200, content=response_data)

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "mcp-gateway-service"}
