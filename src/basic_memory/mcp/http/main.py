import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from basic_memory.mcp.server import mcp  # noqa: E402

# Import mcp tools to register them
import basic_memory.mcp.tools  # noqa: E402, F401

# Import prompts to register them
import basic_memory.mcp.prompts  # noqa: E402, F401


# Create the ASGI app
mcp_app = mcp.http_app(path="/mcp")

# Create a FastAPI app and mount the MCP server
app = FastAPI(lifespan=mcp_app.lifespan)

origins = [
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# mcp path is /mcp
app.mount("/", mcp_app)

@app.get("/")
async def root():
    return {"message": "Hello World"}

if __name__ == "__main__":
    uvicorn.run(mcp_app, host=mcp.settings.host, port=mcp.settings.port)
