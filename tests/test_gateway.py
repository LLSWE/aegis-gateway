import pytest
import sys
import os
import importlib.util
from fastapi.testclient import TestClient

def load_module_from_path(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {module_name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../services"))

mcp_gw_mod = load_module_from_path(
    "mcp_gateway_main",
    os.path.join(base_dir, "mcp-gateway-service/main.py")
)
llm_db_mod = load_module_from_path(
    "llm_database",
    os.path.join(base_dir, "llm-gateway-service/database.py")
)
llm_gr_mod = load_module_from_path(
    "llm_guardrails",
    os.path.join(base_dir, "llm-gateway-service/guardrails.py")
)

mcp_gateway_app = mcp_gw_mod.app
get_user_role = mcp_gw_mod.get_user_role
check_and_record_rate_limit = llm_db_mod.check_and_record_rate_limit
StreamingPIIRedactor = llm_gr_mod.StreamingPIIRedactor
redact_text = llm_gr_mod.redact_text

def test_user_role_extraction():
    assert get_user_role("Bearer admin-token-123") == "admin"
    assert get_user_role("Bearer admin_custom") == "admin"
    assert get_user_role("Bearer viewer-token-456") == "viewer"
    assert get_user_role(None) == "viewer"
    assert get_user_role("InvalidHeader") == "viewer"

def test_mcp_gateway_unauthorized_tool_call():
    client = TestClient(mcp_gateway_app)
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "trigger_refund",
            "arguments": {
                "customer_id": "CUST-12345",
                "amount": 100.0,
                "reason": "Customer unhappy with service"
            }
        },
        "id": 1
    }
    
    response = client.post("/mcp", json=payload, headers={"Authorization": "Bearer viewer-token-456"})
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == -32001
    assert data["error"]["message"] == "Unauthorized Tool Call"

def test_pii_redaction_patterns():
    text = "Contact alice.smith@test.com or call SSN 123-45-6789 or card 4532-1234-5678-9012."
    redacted = redact_text(text)
    assert "alice.smith@test.com" not in redacted
    assert "123-45-6789" not in redacted
    assert "4532-1234-5678-9012" not in redacted
    assert redacted.count("[REDACTED]") == 3

def test_streaming_pii_redactor_buffer():
    redactor = StreamingPIIRedactor(buffer_size=10)
    
    c1 = redactor.process_chunk("Hello my email is test")
    c2 = redactor.process_chunk("@example.com and SSN is 111-22-3333.")
    c3 = redactor.flush()
    
    full_output = c1 + c2 + c3
    assert "test@example.com" not in full_output
    assert "111-22-3333" not in full_output
    assert "[REDACTED]" in full_output

def test_rate_limiter_sliding_window():
    api_key = "test-tenant-key-999"
    
    allowed, usage = check_and_record_rate_limit(api_key, estimated_tokens=1000, limit=5000, window_seconds=60)
    assert allowed is True
    assert usage == 1000
    
    allowed_2, usage_2 = check_and_record_rate_limit(api_key, estimated_tokens=5000, limit=5000, window_seconds=60)
    assert allowed_2 is False
    assert usage_2 == 1000
