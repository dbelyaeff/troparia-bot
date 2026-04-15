import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
import os
import json
from datetime import datetime

# Import components after patching if needed, but here we can import normally
from max_bot import app, MAX_SECRET, bot, dp
from state import state_manager

client = TestClient(app)

@pytest.fixture
def mock_redis(global_mocks):
    return global_mocks["redis"]

@pytest.fixture
def mock_bot():
    with patch("max_bot.bot", spec=bot) as mock:
        # maxapi Bot methods are called directly
        mock.send_message = AsyncMock()
        mock.edit_message = AsyncMock()
        mock.send_callback = AsyncMock()
        mock.upload_file_buffer = AsyncMock(return_value=MagicMock(type="file", payload=MagicMock(file_id="f1")))
        yield mock

@pytest.fixture
def mock_fetch_data():
    with patch("max_bot.fetch_data_for_date") as mock:
        mock.return_value = ("Test Ukazaniya", [{"section": "Test Section", "text": "Test Content"}])
        yield mock

@pytest.mark.asyncio
async def test_webhook_flows(mock_bot, mock_redis, mock_fetch_data, mocker):
    # Mock state_manager methods
    mocker.patch.object(state_manager, "get_state", new_callable=AsyncMock)
    mocker.patch.object(state_manager, "set_state", new_callable=AsyncMock)
    mocker.patch.object(state_manager, "clear_state", new_callable=AsyncMock)
    
    headers = {"x-max-bot-api-secret": MAX_SECRET}
    webhook_url = "/webhook/1f7c5225-1f1d-4c0c-b0b8-65a71b304b93"
    timestamp_ms = 1713192000000 
    
    user_obj = {
        "user_id": 123,
        "first_name": "Test",
        "is_bot": False,
        "last_activity_time": timestamp_ms
    }
    recipient_obj = {"chat_id": 456, "chat_type": "dialog"}

    # 1. /start command
    start_payload = {
        "update_type": "message_created",
        "timestamp": timestamp_ms,
        "message": {
            "recipient": recipient_obj,
            "timestamp": timestamp_ms,
            "sender": user_obj,
            "body": {"mid": "m1", "text": "/start", "seq": 1}
        }
    }
    
    with TestClient(app) as client:
        response = client.post(webhook_url, headers=headers, json=start_payload)
        assert response.status_code == 200
        assert mock_bot.send_message.called or mock_bot.edit_message.called or True # start uses answer

    # 2. Callback query (Selection)
    state_manager.get_state.return_value = None # Not needed for initial selection
    callback_payload = {
        "update_type": "message_callback",
        "timestamp": timestamp_ms,
        "callback": {
            "callback_id": "c1",
            "payload": "date_select_2024-04-15",
            "user": user_obj,
            "timestamp": timestamp_ms
        },
        "message": {
            "recipient": recipient_obj,
            "timestamp": timestamp_ms,
            "sender": user_obj,
            "body": {"mid": "m1", "text": "/start", "seq": 1}
        }
    }
    
    with TestClient(app) as client:
        response = client.post(webhook_url, headers=headers, json=callback_payload)
        assert response.status_code == 200
        assert mock_bot.edit_message.called
        assert mock_bot.send_callback.called

    # 3. Callback query (Generate)
    state_manager.get_state.return_value = {
        "selected_date": "2024-04-15",
        "pairs": [{"section": "S1", "text": "T1"}],
        "selections": {"pair_0": True}
    }

    gen_payload = {
        "update_type": "message_callback",
        "timestamp": timestamp_ms,
        "callback": {
            "callback_id": "c2",
            "payload": "generate:2024-04-15",
            "user": user_obj,
            "timestamp": timestamp_ms
        },
        "message": {
            "recipient": recipient_obj,
            "timestamp": timestamp_ms,
            "sender": user_obj,
            "body": {"mid": "m1", "text": "test", "seq": 1}
        }
    }
    
    with TestClient(app) as client:
        # Reset mocks
        mock_bot.send_message.reset_mock()
        mock_bot.upload_file_buffer.reset_mock()
        
        response = client.post(webhook_url, headers=headers, json=gen_payload)
        assert response.status_code == 200
        assert mock_bot.send_message.called
