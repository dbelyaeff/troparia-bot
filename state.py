import json
import logging
import os
from typing import Any, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis = None

    @property
    def redis_client(self):
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def get_state(self, user_id: str) -> dict:
        """Получает состояние пользователя из Redis."""
        key = f"state:{user_id}"
        data = await self.redis_client.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return {}
        return {}

    async def set_state(self, user_id: str, data: dict):
        """Сохраняет состояние пользователя в Redis."""
        key = f"state:{user_id}"
        await self.redis_client.set(key, json.dumps(data), ex=86400)  # TTL 24 часа

    async def clear_state(self, user_id: str):
        """Очищает состояние пользователя."""
        key = f"state:{user_id}"
        await self.redis_client.delete(key)

# Singleton instance
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
state_manager = StateManager(REDIS_URL)
