import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from basic_memory.mcp.server import mcp  # noqa: E402
from basic_memory.mcp.http.oauth_routes import (  # noqa: E402
    oauth_authorization_server,
    oauth_protected_resource, 
    oauth_client_registration,
    mcp_discovery
)

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: E402, F401

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: E402, F401


# Create the main FastAPI app
app = FastAPI()

origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:6274",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add OAuth routes directly to main app
app.get("/.well-known/oauth-authorization-server")(oauth_authorization_server)
app.get("/.well-known/oauth-protected-resource")(oauth_protected_resource)
app.post("/api/oauth/register")(oauth_client_registration)
app.get("/")(mcp_discovery)

# Create and mount MCP app at /mcp
mcp_app = mcp.http_app()
app.mount("/mcp", mcp_app)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)