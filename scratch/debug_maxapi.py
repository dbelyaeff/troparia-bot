from maxapi import Bot
from maxapi.methods.types.getted_updates import process_update_webhook
import asyncio
import json

async def main():
    bot = Bot(token="T")
    timestamp_ms = 1713192000000
    user_obj = {
        "user_id": 123,
        "first_name": "T",
        "is_bot": False,
        "last_activity_time": timestamp_ms
    }
    recipient_obj = {"chat_id": 456, "chat_type": "dialog"}

    data_msg = {
        "update_type": "message_created",
        "timestamp": timestamp_ms,
        "message": {
            "recipient": recipient_obj,
            "timestamp": timestamp_ms,
            "sender": user_obj,
            "body": {"mid": "m1", "text": "/start", "seq": 1}
        }
    }

    print("--- Testing process_update_webhook ---")
    try:
        event = await process_update_webhook(event_json=data_msg, bot=bot)
        print(f"RESULT: {event}")
        if event:
            print(f"TYPE: {type(event)}")
    except Exception as e:
        print(f"FAILURE: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
