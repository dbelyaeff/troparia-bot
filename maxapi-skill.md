# Integration Guide: MAX Messenger API (maxapi)

This guide provides technical instructions for AI agents on how to correctly integrate with the MAX Messenger platform using the `max-botapi-python` (imported as `maxapi`) library.

## 1. Authentication (CRITICAL)

The current version of the MAX platform requires an `Authorization: <token>` header. The `maxapi` library (v1.x) incorrectly attempts to pass the token as a query parameter, which results in `InvalidToken` or 401 errors.

**You MUST apply this monkeypatch before initializing the Bot:**

```python
from maxapi.connection.base import BaseConnection

# Monkeypatch to move token from params to headers
_original_request = BaseConnection.request
async def _patched_request(self, method, params=None, data=None, headers=None, **kwargs):
    if headers is None:
        headers = {}
    if self.token:
        headers["Authorization"] = self.token
    return await _original_request(self, method, params, data, headers, **kwargs)

BaseConnection.request = _patched_request
```

## 2. Initialization

Initialize the bot with the correct platform URL.

```python
from maxapi.bot import Bot
from maxapi.enums.parse_mode import ParseMode

bot = Bot(token="YOUR_MAX_TOKEN")
# MUST set API_URL on the instance
bot.API_URL = "https://platform-api.max.ru"
# Enable HTML parsing if needed
bot.parse_mode = ParseMode.HTML
```

## 3. Webhook Security & Registration

All webhooks MUST validate a secret token provided in the `X-Max-Bot-Api-Secret` header.

```python
# During startup/lifespan
await bot.subscribe_webhook(
    url="https://your-domain.com/webhook/path",
    secret_token="YOUR_SECRET"
)
```

## 4. Message Handling

### Method Signatures
The `maxapi` signature for `edit_message` is different from Telegram/Aiogram. It **does NOT** accept `chat_id`. It only uses `message_id` (often referred to as `mid`).

```python
# CORRECT
await bot.edit_message(
    message_id=event.message.body.mid,
    text="New Text",
    attachments=[...]
)

# INCORRECT (Will raise TypeError)
await bot.edit_message(chat_id=..., message_id=..., text=...) 
```

### Callback Responsibility
When handling a button click (`message_callback`), you **MUST** call `bot.send_callback()` to acknowledge the event. Failure to do so will leave the button in a "loading" state in the user's client.

```python
@dp.message_callback(F.callback.payload == "my_action")
async def handle_action(event: MessageCallback):
    # Process logic...
    await bot.send_message(...)
    
    # ALWAYS answer the callback
    await bot.send_callback(callback_id=event.callback.callback_id)
```

## 5. Media Uploads

Do not use low-level `upload_file` methods manually unless you want to handle multipart boundaries and token parsing. The library provides `InputMediaBuffer` for high-level automated uploads.

```python
from maxapi.types.input_media import InputMediaBuffer

async def send_pdf(chat_id, pdf_bytes, filename):
    attachment = InputMediaBuffer(buffer=pdf_bytes, filename=filename)
    await bot.send_message(
        chat_id=chat_id,
        text="Here is your file",
        attachments=[attachment]
    )
```

## 6. Common Pitfalls

| Issue | Cause | Solution |
|-------|-------|----------|
| `InvalidToken` / 401 | Missing `Authorization` header | Apply the monkeypatch in Section 1. |
| Button stays "loading" | Missing `send_callback` | Call `bot.send_callback()` at the end of the handler. |
| `TypeError` in `edit_message` | Passing `chat_id` | Remove `chat_id` from the arguments. |
| HTML tags visible | `parse_mode` not set | Set `bot.parse_mode = ParseMode.HTML`. |
| 400 Bad Request on files | Incorrect upload flow | Use `InputMediaBuffer` inside `send_message`. |

## 7. Reference
Source code for the library is often located in `./tmp/max-botapi-python/` or similar local clones for deep inspection of types and methods.
