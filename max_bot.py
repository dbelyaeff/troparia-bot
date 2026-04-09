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

import logging.handlers

LOG_DIR = os.environ.get("LOG_DIR", "logs")
if not os.path.isabs(LOG_DIR):
    LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_DIR)
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("max_bot")
logger.setLevel(logging.INFO)

# File handler with rotation
file_handler = logging.handlers.TimedRotatingFileHandler(
    os.path.join(LOG_DIR, "max.log"), when="D", interval=1, backupCount=7
)
file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
logger.addHandler(console_handler)

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
            if resp.status_code >= 400:
                logger.error(f"MAX send_message error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Sent message to {user_id}: {data.get('body', {}).get('mid')}")
            return data

    async def edit_message(self, mid: str, text: str, keyboard: Optional[List] = None):
        body = {"text": text, "format": "html"}
        if keyboard:
            body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard}}]
        
        async with httpx.AsyncClient() as client:
            resp = await client.put(f"{BASE_URL}/messages?message_id={mid}", json=body, headers=self.headers)
            if resp.status_code >= 400:
                logger.error(f"MAX edit_message error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            logger.info(f"Edited message {mid}")
            return resp.json()

    async def answer_callback(self, callback_id: str, text: str):
        # Use notification for toast message, message for editing/replying
        body = {
            "callback_id": callback_id, 
            "notification": text
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/answers", json=body, headers=self.headers)
            # Don't raise for status if it's 400 (likely expired)
            if resp.status_code >= 400 and resp.status_code != 400:
                logger.error(f"MAX answer_callback error {resp.status_code}: {resp.text}")
                resp.raise_for_status()
            elif resp.status_code == 400:
                logger.warning(f"MAX answer_callback expired (400): {resp.text}")

    async def upload_file(self, file_bytes: bytes, filename: str) -> str:
        async with httpx.AsyncClient() as client:
            # Step 1: Get upload URL
            resp = await client.post(f"{BASE_URL}/uploads?type=file", headers=self.headers)
            if resp.status_code >= 400:
                logger.error(f"MAX upload_file step 1 error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            upload_url = resp.json()["url"]
            
            # Step 2: Upload actual data
            files = {"file": (filename, file_bytes, "application/pdf")}
            resp = await client.post(upload_url, files=files)
            if resp.status_code >= 400:
                logger.error(f"MAX upload_file step 2 error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            # This step finally returns the token
            return resp.json()["token"]

    async def send_file(self, user_id: int, token: str, text: str):
        body = {
            "text": text,
            "format": "html",
            "attachments": [{"type": "file", "payload": {"token": token}}]
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/messages?user_id={user_id}", json=body, headers=self.headers)
            if resp.status_code >= 400:
                logger.error(f"MAX send_file error {resp.status_code}: {resp.text}")
            resp.raise_for_status()

    async def register_webhook(self, url: str, secret: Optional[str] = None):
        body = {
            "url": url,
            "update_types": ["message_created", "bot_started", "message_callback"]
        }
        if secret:
            body["secret"] = secret
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{BASE_URL}/subscriptions", json=body, headers=self.headers)
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
        logger.warning(f"Invalid secret from {request.client.host}")
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    update = await request.json()
    logger.info(f"Received MAX update: {update.get('update_type')}")
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
        msg = update.get("message", {})
        user_id = cb.get("user", {}).get("user_id") or msg.get("sender", {}).get("user_id")
        data = cb.get("payload")
        callback_id = cb.get("callback_id")
        mid = msg.get("body", {}).get("mid")
        
        logger.info(f"MAX Callback: user={user_id}, mid={mid}, data={data}")
        if not mid:
            # Fallback for debugging if sibling structure is still not right
            mid = cb.get("message", {}).get("mid")
            if mid: logger.info(f"Found mid in callback.message: {mid}")
            
        user_state = await state_manager.get_state(str(user_id))

        if data.startswith("week:"):
            offset = int(data.split(":")[1])
            mid = cb.get("message", {}).get("mid")
            await max_client.edit_message(mid, "Выберите дату:", build_max_date_keyboard(offset))
            await max_client.answer_callback(callback_id, "Пагинация")
        
        elif data.startswith("date:"):
            date_str = data.split(":")[1]
            date_human = get_date_human(date_str)
            mid = cb.get("message", {}).get("mid")
            
            # Show "loading" by editing the message
            await max_client.edit_message(mid, f"⏳ Загружаю данные на {date_human}...")
            
            try:
                ukazaniya_text, pairs = await fetch_data_for_date(date_str)
                user_state.update({
                    "selected_date": date_str,
                    "pairs": pairs,
                    "selections": {f"pair_{i}": True for i in range(len(pairs))}
                })
                await state_manager.set_state(str(user_id), user_state)
                
                # After fetching, we edit again with the tropari selection
                text = f"📖 <b>Указания</b>: {ukazaniya_text}\n\n🔹 <b>Выберите тропари:</b>"
                await max_client.edit_message(mid, text, build_max_selection_keyboard(pairs, user_state["selections"]))
            except Exception as e:
                logger.exception("MAX data fetch error")
                await max_client.edit_message(mid, f"❌ Ошибка: {e}")
            await max_client.answer_callback(callback_id, "Дата выбрана")

        elif data.startswith("toggle:"):
            pair_id = data.split(":")[1]
            mid = cb.get("message", {}).get("mid")
            selections = user_state.get("selections", {})
            selections[pair_id] = not selections.get(pair_id, True)
            user_state["selections"] = selections
            await state_manager.set_state(str(user_id), user_state)
            await max_client.edit_message(mid, "Обновлено (выбор тропарей):", build_max_selection_keyboard(user_state["pairs"], selections))
            await max_client.answer_callback(callback_id, "Переключено")

        elif data == "toggle_all":
            pairs = user_state.get("pairs", [])
            mid = cb.get("message", {}).get("mid")
            selections = user_state.get("selections", {})
            all_selected = all(selections.get(f"pair_{i}", True) for i in range(len(pairs)))
            for i in range(len(pairs)): selections[f"pair_{i}"] = not all_selected
            user_state["selections"] = selections
            await state_manager.set_state(str(user_id), user_state)
            await max_client.edit_message(mid, "Обновлено (все):", build_max_selection_keyboard(pairs, selections))
            await max_client.answer_callback(callback_id, "Все переключено")

        elif data == "generate_pdf":
            mid = cb.get("message", {}).get("mid")
            date_str = user_state.get("selected_date")
            pairs = user_state.get("pairs")
            selections = user_state.get("selections")
            
            if not date_str or not pairs or not selections:
                logger.warning(f"MAX generate_pdf state missing: {user_state}")
                await max_client.edit_message(mid, "⚠️ Сессия истекла или данные не выбраны. Начните сначала:", build_max_date_keyboard(0))
                await max_client.answer_callback(callback_id, "Ошибка сессии")
                return

            selected_pairs = [pairs[i] for i in range(len(pairs)) if selections.get(f"pair_{i}", True)]
            
            if not selected_pairs:
                await max_client.edit_message(mid, "❌ Выберите хотя бы одну пару!", build_max_selection_keyboard(pairs, selections))
                return
            
            # Show "generating"
            await max_client.edit_message(mid, "⏳ Генерирую PDF...")
            
            try:
                pdf_bytes = generate_pdf_bytes(date_str, FONT_PATH, sections=get_pdf_sections(selected_pairs))
                token = await max_client.upload_file(pdf_bytes, f"Troparia_{date_str}.pdf")
                await max_client.send_file(user_id, token, f"☦️ PDF на {get_date_human(date_str)}")
                # We can either edit the selection message to "Done" or just keep it. 
                # Let's return to calendar start in the selection message.
                await max_client.edit_message(mid, "✅ PDF отправлен! Выберите следующую дату:", build_max_date_keyboard(0))
            except Exception as e:
                logger.exception("MAX PDF error")
                await max_client.edit_message(mid, f"❌ Ошибка генерации: {e}", build_max_selection_keyboard(pairs, selections))
            await max_client.answer_callback(callback_id, "PDF сгенерирован")

        elif data == "back_to_calendar":
            mid = cb.get("message", {}).get("mid")
            await state_manager.clear_state(str(user_id))
            await max_client.edit_message(mid, "📅 Выберите дату:", build_max_date_keyboard(0))
            await max_client.answer_callback(callback_id, "Назад")

    return {"status": "ok"}
