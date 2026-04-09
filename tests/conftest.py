import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import json
import os

# Set dummy env vars for tests
os.environ["BOT_TOKEN"] = "12345:dummy"
os.environ["MAX_BOT_TOKEN"] = "dummy_max_token"
os.environ["MAX_WEBHOOK_SECRET"] = "dummy_secret"
os.environ["WEBHOOK_URL"] = "https://example.com/webhook/tg"
os.environ["MAX_WEBHOOK_URL"] = "https://example.com/webhook/max"
os.environ["REDIS_URL"] = "redis://redis:6379/0"
os.environ["FONT_PATH"] = "fonts/PonomarUnicode.otf"

@pytest.fixture(autouse=True)
def global_mocks():
    """Mock external dependencies globally."""
    # Mock redis
    with patch("state.redis.from_url") as m_redis, \
         patch("httpx.AsyncClient") as m_httpx, \
         patch("requests.get") as m_requests, \
         patch("reportlab.pdfbase.pdfmetrics.registerFont"), \
         patch("reportlab.pdfbase.pdfmetrics.stringWidth", return_value=10.0), \
         patch("reportlab.pdfgen.canvas.Canvas") as m_canvas:
        
        # Redis setup
        client = AsyncMock()
        m_redis.return_value = client
        client.get.return_value = None
        
        # httpx setup
        h_client = AsyncMock()
        m_httpx.return_value.__aenter__.return_value = h_client
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"token": "t"}
        h_client.post.return_value = resp
        h_client.put.return_value = resp
        h_client.delete.return_value = resp
        
        # requests setup
        r_resp = MagicMock()
        r_resp.status_code = 200
        r_resp.text = "<html></html>"
        m_requests.return_value = r_resp
        
        # Canvas setup
        m_inst = m_canvas.return_value
        m_inst.stringWidth.return_value = 10.0
        
        yield {
            "redis": client,
            "httpx": h_client,
            "requests": m_requests,
            "canvas": m_inst
        }

@pytest.fixture
def mock_redis(global_mocks):
    return global_mocks["redis"]

@pytest.fixture
def mock_httpx_client(global_mocks):
    return global_mocks["httpx"]

@pytest.fixture
def mock_requests(global_mocks):
    return global_mocks["requests"]

@pytest.fixture
def sample_ukazaniya_html():
    return """
    <html><body>
        <h3>Раздел 1</h3>
        <p>на 1-м часе тропарь воскресный, глас 1.</p>
    </body></html>
    """

@pytest.fixture
def sample_days_html():
    return """
    <html><body>
        <h2 class="block_title">Воскресные</h2>
        <div class="frame">
            <h3>Тропарь, глас 1</h3>
            <div class="taks_content"><div class="inner"><p><p>Текст</p></p></div></div>
        </div>
    </body></html>
    """
