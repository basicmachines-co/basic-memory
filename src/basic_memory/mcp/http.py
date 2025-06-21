import uvicorn
from fastmcp import FastMCP
from fastapi import FastAPI

from basic_memory.mcp.server import mcp

# Create the ASGI app
mcp_app = mcp.http_app(path='/mcp')

# Create a FastAPI app and mount the MCP server
app = FastAPI(lifespan=mcp_app.lifespan)
app.mount("/mcp-server", mcp_app)

if __name__ == "__main__":
    uvicorn.run(mcp_app, host="0.0.0.0", port=8000)