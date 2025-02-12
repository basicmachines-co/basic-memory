from httpx import ASGITransport, AsyncClient

from basic_memory.api.app import app as fastapi_app

BASE_URL = "http://test"

# Create shared async client
client = AsyncClient(transport=ASGITransport(app=fastapi_app), base_url=BASE_URL)
