import sys
import re
import asyncio
from typing import Any
from pydantic import BaseModel, Field, field_validator, ValidationError
from mcp.server import Server, InitializationOptions
from mcp.server.stdio import stdio_server
from mcp import types
from mcp.shared.exceptions import MCPError

class GetCustomerRecordParams(BaseModel):
    customer_id: str = Field(..., description="Customer ID formatted as CUST-XXXXX")

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id(cls, v: str) -> str:
        if not re.match(r"^CUST-\d{5}$", v):
            raise ValueError("customer_id must be formatted as CUST-XXXXX (e.g., CUST-12345)")
        return v

class TriggerRefundParams(BaseModel):
    customer_id: str = Field(..., description="Customer ID formatted as CUST-XXXXX")
    amount: float = Field(..., gt=0, description="Positive refund amount")
    reason: str = Field(..., min_length=10, description="Reason for refund (minimum 10 characters)")

    @field_validator("customer_id")
    @classmethod
    def validate_customer_id(cls, v: str) -> str:
        if not re.match(r"^CUST-\d{5}$", v):
            raise ValueError("customer_id must be formatted as CUST-XXXXX (e.g., CUST-12345)")
        return v

server = Server("aegis-mcp-service", version="0.1.0")

@server.list_tools()
async def handle_list_tools(context, params) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name="get_customer_record",
                description="Retrieve a customer record by ID (format CUST-XXXXX)",
                inputSchema=GetCustomerRecordParams.model_json_schema()
            ),
            types.Tool(
                name="trigger_refund",
                description="Trigger a customer refund with validated amount and reason (>=10 chars)",
                inputSchema=TriggerRefundParams.model_json_schema()
            )
        ]
    )

@server.call_tool()
async def handle_call_tool(context, params: types.CallToolRequestParams) -> types.CallToolResult:
    tool_name = params.name
    arguments = params.arguments or {}

    print(f"[mcp-service] Calling tool {tool_name} with args {arguments}", file=sys.stderr)

    try:
        if tool_name == "get_customer_record":
            validated = GetCustomerRecordParams(**arguments)
            record = {
                "customer_id": validated.customer_id,
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "status": "active",
                "account_tier": "Enterprise"
            }
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(record))]
            )

        elif tool_name == "trigger_refund":
            validated = TriggerRefundParams(**arguments)
            refund_result = {
                "status": "success",
                "refund_id": f"REF-{abs(hash(validated.customer_id)) % 100000:05d}",
                "customer_id": validated.customer_id,
                "amount": validated.amount,
                "reason": validated.reason
            }
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(refund_result))]
            )
        else:
            raise MCPError(code=-32601, message=f"Tool not found: {tool_name}")

    except (ValueError, ValidationError) as e:
        print(f"[mcp-service] Validation error in {tool_name}: {e}", file=sys.stderr)
        raise MCPError(code=-32602, message=f"Invalid arguments for {tool_name}: {str(e)}")
    except MCPError:
        raise
    except Exception as e:
        print(f"[mcp-service] Internal error in {tool_name}: {e}", file=sys.stderr)
        raise MCPError(code=-32603, message=f"Internal error: {str(e)}")

async def main():
    print("[mcp-service] Starting MCP Server on stdio...", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="aegis-mcp-service",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={}
                )
            )
        )

if __name__ == "__main__":
    asyncio.run(main())
