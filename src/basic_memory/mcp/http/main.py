import uvicorn
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from basic_memory.mcp.server import mcp  # noqa: E402

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: E402, F401

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: E402, F401


# Add our OAuth proxy routes to override FastMCP's built-in OAuth
@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_authorization_server(request):
    """Proxy OAuth authorization server metadata to basic-memory-cloud."""
    import httpx
    from fastapi.responses import JSONResponse
    from loguru import logger

    oauth_server_base = "http://localhost:3000"
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    async with httpx.AsyncClient() as client:
        try:
            logger.info(f"Proxying OAuth metadata from {oauth_server_base}")
            response = await client.get(f"{oauth_server_base}/.well-known/oauth-authorization-server")
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


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource(request):
    """OAuth 2.0 protected resource metadata (RFC 8707)."""
    from fastapi.responses import JSONResponse
    
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS", 
        "Access-Control-Allow-Headers": "*",
    }

    return JSONResponse(
        {
            "resource": "http://localhost:8000",
            "authorization_servers": ["http://localhost:3000"],
            "scopes_supported": ["basic_memory:read", "basic_memory:write"],
            "bearer_methods_supported": ["header"],
            "resource_documentation": "https://github.com/basicmachines-co/basic-memory",
        },
        headers=headers,
    )


@mcp.custom_route("/api/oauth/register", methods=["POST"])
async def oauth_client_registration(request):
    """Proxy OAuth client registration to basic-memory-cloud."""
    import httpx
    from fastapi.responses import JSONResponse
    from loguru import logger
    
    oauth_server_base = "http://localhost:3000"
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }

    async with httpx.AsyncClient() as client:
        try:
            # Forward the registration request to basic-memory-cloud
            body = await request.body()
            logger.info(f"Proxying client registration to {oauth_server_base}")
            response = await client.post(
                f"{oauth_server_base}/api/oauth/register",
                content=body,
                headers={"content-type": "application/json"},
            )
            logger.info(f"Client registration response status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Client registration successful, client_id: {data.get('client_id', 'unknown')}")
                return JSONResponse(data, headers=headers)
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


# Create the FastMCP app with our custom OAuth routes
app = mcp.http_app()

# Add CORS middleware to the FastMCP app  
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print(f"MCP app type: {type(app)}")
print(f"MCP app routes: {getattr(app, 'routes', 'No routes attribute')}")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)