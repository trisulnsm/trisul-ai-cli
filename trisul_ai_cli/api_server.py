"""
Trisul AI REST API Server
=========================
Exposes the TrisulAIClient as a FastAPI REST API so that web applications
can send natural-language queries and receive structured JSON responses.

Endpoints
---------
GET  /api/health   – liveness check
GET  /api/tools    – list available MCP tools
POST /api/query    – submit a query; returns raw data + optional AI answer
"""

import asyncio
import logging
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from trisul_ai_cli.client import TrisulAIClient
from importlib.metadata import version as pkg_version
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging configuration: ensure we capture logs in the same file as CLI/Server
# ---------------------------------------------------------------------------
logging.basicConfig(
    filename=Path(os.getcwd()) / "trisul_ai_cli.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True
)


# ---------------------------------------------------------------------------
# Shared state: one long-lived client instance (MCP server started once)
# ---------------------------------------------------------------------------

_client: TrisulAIClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to MCP server. Shutdown: clean up."""
    global _client
    logging.info("[API] Starting up — connecting to MCP server...")
    _client = TrisulAIClient()
    await _client.connect_to_server("trisul_ai_cli.server")
    logging.info("[API] MCP server connected.")
    yield
    # Shutdown
    if _client:
        await _client.cleanup()
    logging.info("[API] Shutdown complete.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Trisul AI REST API",
    description=(
        "Natural-language query interface for Trisul network intelligence. "
        "Returns structured JSON data suitable for programmatic consumption "
        "(charts, tables, dashboards) rather than human-readable summaries."
    ),
    version=pkg_version("trisul_ai_cli"),
    lifespan=lifespan,
)

# Allow cross-origin requests so browser-based web apps can call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    system_prompt: Optional[str] = None
    session_id: Optional[str] = None  # reserved for future multi-turn support


class ToolCallRecord(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Any


class ChartData(BaseModel):
    type: str          # "line" | "pie"
    data: Any


class TableData(BaseModel):
    title: Optional[str] = "Data Table"
    headers: List[str]
    rows: List[List[Any]]


class QueryResponse(BaseModel):
    status: str        # "success" | "error"
    answer: Optional[str] = None   # AI text (Q&A or summary fallback)
    tool_calls: List[ToolCallRecord] = []
    chart_data: Optional[ChartData] = None
    table_data: Optional[TableData] = None
    message: Optional[str] = None  # error message when status == "error"
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["Meta"])
async def health():
    """Liveness check. Returns server version and MCP connection status."""
    connected = _client is not None and _client.session is not None
    return {
        "status": "ok",
        "version": pkg_version("trisul_ai_cli"),
        "mcp_connected": connected,
    }


@app.get("/api/tools", tags=["Meta"])
async def list_tools():
    """Return all available MCP tool names and their descriptions."""
    logging.info("[API] GET /api/tools")
    if not _client or not _client.session:
        logging.error("[API] MCP server not connected")
        raise HTTPException(status_code=503, detail="MCP server not connected")
    try:
        tools_result = await _client.session.list_tools()
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.inputSchema,
            }
            for t in tools_result.tools
        ]
        logging.info(f"[API] Successfully retrieved {len(tools)} tools.")
        return {"status": "ok", "tools": tools}
    except Exception as e:
        logging.error(f"[API] Error listing tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/query", response_model=QueryResponse, tags=["Query"])
async def query(req: QueryRequest):
    """
    Submit a natural-language query.

    - If the query requires network data, the LLM will call MCP tools and
      the raw tool results are returned in `tool_calls[].result`.
    - If the query is a general Trisul question (no tool calls needed), the
      answer is returned in the `answer` field.
    - `chart_data` is populated when a chart tool was invoked so the caller
      can render the chart directly.
    """
    if not _client or not _client.session:
        logging.error("[API] Rejecting query: MCP server not connected")
        raise HTTPException(status_code=503, detail="MCP server not connected")

    logging.info(f"[API] /api/query  session={req.session_id}  query={req.query!r}")

    try:
        result = await _client.process_query_api(
            query=req.query,
            system_prompt=req.system_prompt,
            session_id=req.session_id,
        )
        
        # Log summary of the result
        status = result.get("status", "error")
        ans_len = len(result.get("answer") or "")
        num_tools = len(result.get("tool_calls", []))
        has_chart = "yes" if result.get("chart_data") else "no"
        has_table = "yes" if result.get("table_data") else "no"
        logging.info(f"[API] Query processed: status={status}, ans_len={ans_len}, tool_calls={num_tools}, chart={has_chart}, table={has_table}")

    except Exception as e:
        logging.error(f"[API] Unhandled error in process_query_api: {e}")
        return QueryResponse(
            status="error",
            message=str(e),
            session_id=req.session_id,
        )

    return QueryResponse(
        status=result.get("status", "error"),
        answer=result.get("answer"),
        tool_calls=[
            ToolCallRecord(tool=tc["tool"], args=tc["args"], result=tc["result"])
            for tc in result.get("tool_calls", [])
        ],
        chart_data=(
            ChartData(type=result["chart_data"]["type"], data=result["chart_data"]["data"])
            if result.get("chart_data")
            else None
        ),
        table_data=(
            TableData(
                title=result["table_data"].get("title", "Data Table") if isinstance(result["table_data"], dict) else "Data Table",
                headers=result["table_data"].get("headers", []) if isinstance(result["table_data"], dict) else [],
                rows=result["table_data"].get("rows", []) if isinstance(result["table_data"], dict) else []
            )
            if result.get("table_data")
            else None
        ),
        message=result.get("message"),
        session_id=req.session_id,
    )


# ---------------------------------------------------------------------------
# Entry-point helper (called from cli.py)
# ---------------------------------------------------------------------------

def start_server(
    host: str = "0.0.0.0", 
    port: int = 8200, 
    log_level: str = "info",
    ssl_keyfile: str = None,
    ssl_certfile: str = None
):
    """Start the Uvicorn server. Called from the CLI `api` subcommand."""
    protocol = "https" if ssl_keyfile and ssl_certfile else "http"
    mode = "HTTPS" if protocol == "https" else "HTTP"

    print(f"\n🚀 Trisul AI REST API starting in {mode} mode on {protocol}://{host}:{port}")
    print(f"   Interactive docs: {protocol}://{host}:{port}/docs")
    print(f"   Health check:     {protocol}://{host}:{port}/api/health\n")
    
    uvicorn.run(
        "trisul_ai_cli.api_server:app",
        host=host,
        port=port,
        log_level=log_level,
        loop="asyncio",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
