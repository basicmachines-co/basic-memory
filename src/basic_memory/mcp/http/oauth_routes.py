"""OAuth proxy routes for Basic Memory MCP server."""

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

app = FastAPI()

# Basic Memory Cloud OAuth server base URL
OAUTH_SERVER_BASE = "http://localhost:3000"


@app.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server():
    """Proxy OAuth authorization server metadata to basic-memory-cloud."""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Proxying OAuth metadata from {OAUTH_SERVER_BASE}")
            response = await client.get(
                f"{OAUTH_SERVER_BASE}/.well-known/oauth-authorization-server"
            )
            logger.info(f"OAuth metadata response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"OAuth metadata contains registration_endpoint: {'registration_endpoint' in data}")
                return JSONResponse(data, headers=headers)
            else:
                logger.error(f"OAuth metadata request failed: {response.status_code}")
                return JSONResponse(
                    {"error": f"OAuth server returned {response.status_code}"}, 
                    status_code=response.status_code,
                    headers=headers
                )
        except Exception as e:
            logger.error(f"Failed to fetch OAuth metadata: {str(e)}")
            return JSONResponse(
                {"error": f"Failed to fetch OAuth metadata: {str(e)}"}, 
                status_code=500,
                headers=headers
            )


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource():
    """OAuth 2.0 protected resource metadata (RFC 8707)."""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    return JSONResponse(
        {
            "resource": "http://localhost:8000",
            "authorization_servers": [OAUTH_SERVER_BASE],
            "scopes_supported": ["basic_memory:read", "basic_memory:write"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://github.com/basicmachines-co/basic-memory",
        },
        headers=headers,
    )


@app.post("/api/oauth/register")
async def oauth_client_registration(request: Request):
    """Proxy OAuth client registration to basic-memory-cloud."""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    async with httpx.AsyncClient() as client:
        try:
            # Forward the registration request to basic-memory-cloud
            body = await request.body()
            response = await client.post(
                f"{OAUTH_SERVER_BASE}/api/oauth/register",
                content=body,
                headers={"content-type": "application/json"},
            )
            
            if response.status_code == 200:
                return JSONResponse(response.json(), headers=headers)
            else:
                logger.error(f"Client registration failed: {response.status_code}")
                return JSONResponse(
                    {"error": f"Client registration failed: {response.status_code}"}, 
                    status_code=response.status_code,
                    headers=headers
                )
        except Exception as e:
            logger.error(f"Client registration error: {str(e)}")
            return JSONResponse(
                {"error": f"Client registration failed: {str(e)}"}, 
                status_code=500,
                headers=headers
            )


@app.get("/mcp")
async def mcp_discovery():
    """MCP discovery endpoint for OAuth integration."""
    return JSONResponse({
        "name": "Basic Memory",
        "version": "1.0.0",
        "description": "Personal knowledge management with AI - local-first, markdown-based knowledge graph",
        "protocol_version": "2025-06-18",
        # Point to OAuth endpoints (on this server)
        "authentication": {
            "type": "oauth2",
            "authorization_server": "http://localhost:8000/.well-known/oauth-authorization-server",
            "client_registration": "http://localhost:8000/api/oauth/register",
            "required_scopes": ["basic_memory:read"],
            "optional_scopes": ["basic_memory:write"],
        },
        # This server's MCP endpoints
        "endpoints": {
            "mcp_server": "http://localhost:8000/mcp",
            "health": "http://localhost:8000/health",
            "discovery": "http://localhost:8000/mcp",
        },
        "capabilities": ["resources", "tools", "prompts", "search"],
        # Additional metadata
        "metadata": {
            "repository": "https://github.com/basicmachines-co/basic-memory",
            "documentation": "https://github.com/basicmachines-co/basic-memory/blob/main/README.md",
            "local_first": True,
            "multi_project": True,
        },
    })


@app.options("/{path:path}")
async def options_handler(path: str):
    """Handle CORS preflight requests."""
    return JSONResponse(
        {},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )