import io
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from pydantic import BaseModel

from generator import generate_pdf_bytes
from shared_logic import (
    get_liturgical_date, get_date_human, fetch_data_for_date,
    get_pdf_sections, DAYS_RU, MONTHS_RU
)
from state import state_manager

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: register webhook
    webhook_url = os.environ.get("MAX_WEBHOOK_URL")
    webhook_secret = os.environ.get("MAX_WEBHOOK_SECRET")
    if webhook_url and MAX_TOKEN:
        try:
            logger.info(f"Registering MAX webhook: {webhook_url}")
            await max_client.register_webhook(webhook_url, webhook_secret)
            logger.info("MAX webhook registered successfully")
        except Exception as e:
            logger.error(f"Failed to register MAX webhook: {e}")
    yield
    # Shutdown: cleanup if needed

app = FastAPI(lifespan=lifespan)

MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN")
MAX_SECRET = os.environ.get("MAX_WEBHOOK_SECRET")
FONT_PATH = os.environ.get("FONT_PATH", "/app/fonts/PonomarUnicode.otf")
BASE_URL = "https://platform-api.max.ru"

class MAXClient:
    def __init__(self, token: str):
        self.headers = {"Authorization": token, "Content-Type": "application/json"}

    async def send_message(self, user_id: int, text: str, keyboard: Optional[List] = None):
        body = {"text": text, "format": "html"}
        if keyboard:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/messages?user_id={user_id}", json=body, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def answer_callback(self, callback_id: str, text: str):
        body = {"callback_id": callback_id, "message": {"text": text}}
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/answers", json=body, headers=self.headers)
            resp.raise_for_status()

    async def upload_file(self, file_bytes: bytes, filename: str) -> str:
        async with httpx.AsyncClient() as client:
            files = {"file": (filename, file_bytes, "application/pdf")}
            resp = await client.post(f"{BASE_URL}/uploads?type=file", files=files, headers={"Authorization": MAX_TOKEN})
            resp.raise_for_status()
            return resp.json()["token"]

    async def send_file(self, user_id: int, token: str, text: str):
        body = {
            "text": text,
            "attachments": [{"type": "file", "payload": {"token": token}}]
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/messages?user_id={user_id}", json=body, headers=self.headers)
            resp.raise_for_status()

    async def register_webhook(self, url: str, secret: Optional[str] = None):
        body = {"url": url}
        if secret:
            body["secret"] = secret
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/webhooks", json=body, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

max_client = MAXClient(MAX_TOKEN)

# ─── Клавиатуры ───

def build_max_date_keyboard(week_offset: int = 0) -> List:
    liturgical_today = get_liturgical_date().date()
    start = liturgical_today + timedelta(weeks=week_offset)
    buttons = []
    
    # Сегодня
    if week_offset == 0:
        d = start
        weekday = DAYS_RU[d.weekday()]
        buttons.append([{ "type": "callback", "text": f"📅 Сегодня {d.day} {MONTHS_RU[d.month]} ({weekday})", "payload": f"date:{d.isoformat()}" }])
        # Остальные дни
        row = []
        for i in range(1, 7):
            d = start + timedelta(days=i)
            row.append({ "type": "callback", "text": f"{d.day} {MONTHS_RU[d.month]} ({DAYS_RU[d.weekday()]})", "payload": f"date:{d.isoformat()}" })
            if len(row) == 2:
                buttons.append(row); row = []
        if row: buttons.append(row)
    else:
        row = []
        for i in range(7):
            d = start + timedelta(days=i)
            row.append({ "type": "callback", "text": f"{d.day} {MONTHS_RU[d.month]} ({DAYS_RU[d.weekday()]})", "payload": f"date:{d.isoformat()}" })
            if len(row) == 2:
                buttons.append(row); row = []
        if row: buttons.append(row)

    nav = []
    if week_offset > 0:
        nav.append({ "type": "callback", "text": "⬅️ Назад", "payload": f"week:{week_offset - 1}" })
    nav.append({ "type": "callback", "text": "Вперёд ▶️", "payload": f"week:{week_offset + 1}" })
    buttons.append(nav)
    return buttons

def build_max_selection_keyboard(pairs: list, selections: dict) -> List:
    buttons = []
    for pair_idx, pair in enumerate(pairs):
        pair_id = f"pair_{pair_idx}"
        is_selected = selections.get(pair_id, True)
        check = "✅" if is_selected else "❌"
        label = f"{check} {pair.get('section', '')}"
        buttons.append([{ "type": "callback", "text": label, "payload": f"toggle:{pair_id}" }])
    
    if pairs:
        all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
        toggle_all_text = "❌ Снять все" if all_selected else "✅ Выбрать все"
        buttons.append([{ "type": "callback", "text": toggle_all_text, "payload": "toggle_all" }])
    
    # Кнопки действия (заменяют ReplyKeyboard)
    buttons.append([{ "type": "callback", "text": "📄 Сгенерировать PDF", "payload": "generate_pdf" }])
    buttons.append([{ "type": "callback", "text": "⬅️ К календарю", "payload": "back_to_calendar" }])
    
    return buttons

# ─── Обработчики ───

@app.post("/webhook/1f7c5225-1f1d-4c0c-b0b8-65a71b304b93")
async def max_webhook(request: Request, x_max_bot_api_secret: str = Header(None)):
    if x_max_bot_api_secret != MAX_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    update = await request.json()
    update_type = update.get("update_type")
    
    if update_type == "message_created":
        msg = update.get("message", {})
        sender = msg.get("sender", {})
        user_id = sender.get("user_id")
        text = msg.get("body", {}).get("text", "")
        
        if text == "/start":
            await state_manager.clear_state(str(user_id))
            await max_client.send_message(user_id, "☦️ <b>Тропари и Кондаки</b>\n\nВыберите дату:", build_max_date_keyboard(0))
            
    elif update_type == "message_callback":
        cb = update.get("callback", {})
        user_id = cb.get("user", {}).get("user_id")
        data = cb.get("payload")
        callback_id = cb.get("callback_id")
        user_state = await state_manager.get_state(str(user_id))

        if data.startswith("week:"):
            offset = int(data.split(":")[1])
            await max_client.send_message(user_id, "Выберите дату:", build_max_date_keyboard(offset))
            await max_client.answer_callback(callback_id, "Пагинация")
        
        elif data.startswith("date:"):
            date_str = data.split(":")[1]
            date_human = get_date_human(date_str)
            await max_client.send_message(user_id, f"⏳ Загружаю данные на {date_human}...")
            
            try:
                ukazaniya_text, pairs = await fetch_data_for_date(date_str)
                user_state.update({
                    "selected_date": date_str,
                    "pairs": pairs,
                    "selections": {f"pair_{i}": True for i in range(len(pairs))}
                })
                await state_manager.set_state(str(user_id), user_state)
                
                await max_client.send_message(user_id, f"📖 <b>Указания</b>: {ukazaniya_text}")
                await max_client.send_message(user_id, "🔹 <b>Выберите тропари:</b>", build_max_selection_keyboard(pairs, user_state["selections"]))
            except Exception as e:
                logger.exception("MAX data fetch error")
                await max_client.send_message(user_id, f"❌ Ошибка: {e}")
            await max_client.answer_callback(callback_id, "Дата выбрана")

        elif data.startswith("toggle:"):
            pair_id = data.split(":")[1]
            selections = user_state.get("selections", {})
            selections[pair_id] = not selections.get(pair_id, True)
            user_state["selections"] = selections
            await state_manager.set_state(str(user_id), user_state)
            await max_client.send_message(user_id, "Обновлено:", build_max_selection_keyboard(user_state["pairs"], selections))
            await max_client.answer_callback(callback_id, "Переключено")

        elif data == "toggle_all":
            pairs = user_state.get("pairs", [])
            selections = user_state.get("selections", {})
            all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
            for i in range(len(pairs)): selections[f"pair_{i}"] = not all_selected
            user_state["selections"] = selections
            await state_manager.set_state(str(user_id), user_state)
            await max_client.send_message(user_id, "Обновлено все:", build_max_selection_keyboard(pairs, selections))
            await max_client.answer_callback(callback_id, "Все переключено")

        elif data == "generate_pdf":
            date_str = user_state.get("selected_date")
            selected_pairs = [user_state["pairs"][i] for i in range(len(user_state["pairs"])) if user_state["selections"].get(f"pair_{i}", True)]
            
            if not selected_pairs:
                await max_client.send_message(user_id, "❌ Выберите хотя бы одну пару!")
                return
            
            try:
                pdf_bytes = generate_pdf_bytes(date_str, FONT_PATH, sections=get_pdf_sections(selected_pairs))
                token = await max_client.upload_file(pdf_bytes, f"Troparia_{date_str}.pdf")
                await max_client.send_file(user_id, token, f"☦️ PDF на {get_date_human(date_str)}")
                await max_client.send_message(user_id, "📅 Выберите следующую дату:", build_max_date_keyboard(0))
            except Exception as e:
                logger.exception("MAX PDF error")
                await max_client.send_message(user_id, f"❌ Ошибка генерации: {e}")
            await max_client.answer_callback(callback_id, "PDF сгенерирован")

        elif data == "back_to_calendar":
            await state_manager.clear_state(str(user_id))
            await max_client.send_message(user_id, "📅 Выберите дату:", build_max_date_keyboard(0))
            await max_client.answer_callback(callback_id, "Назад")

    return {"status": "ok"}
