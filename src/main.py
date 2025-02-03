import os
import re
import json
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
from typing import Dict, List
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER', '+10000000000')
PORT = int(os.getenv('PORT', 8010))

if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY in environment.")

# In-memory data stores
CONVERSATIONS: Dict[str, List[dict]] = {}
ORDERS: Dict[str, List[str]] = {}

RESERVATION_SCHEDULE = {
    "2025-02-01": {"19:00": 5, "20:00": 4},
    "2025-02-02": {"19:00": 0, "20:00": 5},
}

MENU_DATA = {
    "appetizers": ["Bruschetta", "Caesar Salad", "Mozzarella Sticks"],
    "main_courses": ["Grilled Salmon", "Margherita Pizza", "Steak Frites"],
    "desserts": ["Tiramisu", "Cheesecake", "Chocolate Mousse"],
    "drinks": ["Red Wine", "White Wine", "Sparkling Water"]
}

SYSTEM_MESSAGE = f"""
I am Sofia, an AI restaurant hostess. I handle reservations and takeout orders.

Real Menu:
Appetizers: {', '.join(MENU_DATA['appetizers'])}
Mains: {', '.join(MENU_DATA['main_courses'])}
Desserts: {', '.join(MENU_DATA['desserts'])}
Drinks: {', '.join(MENU_DATA['drinks'])}

If a requested reservation time is '0 seats', we're fully booked.
If seats > 0, we can book. For orders, add items until the user says 'done'.
"""

app = FastAPI()


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Restaurant AI is running."}


@app.post("/incoming-call")
async def incoming_call(request: Request):
    body = await request.form()
    from_number = body.get("From", "")
    if from_number not in CONVERSATIONS:
        CONVERSATIONS[from_number] = [{"role": "system", "content": SYSTEM_MESSAGE}]

    resp = VoiceResponse()
    resp.say("Welcome to Fine Dining Restaurant. This is Sofia, your virtual hostess.")

    gather = Gather(
        input="speech",
        speech_timeout="auto",
        action="/process-speech",
        method="POST",
        language="en-US"
    )
    gather.say("How may I assist you today? You can reserve a table or place a takeout order.")
    resp.append(gather)

    resp.say("I did not receive any input. Goodbye.")
    resp.hangup()
    return HTMLResponse(str(resp), media_type="application/xml")


@app.post("/process-speech")
async def process_speech(request: Request):
    form_data = await request.form()
    transcription = form_data.get("SpeechResult", "").strip()
    from_number = form_data.get("From", "")

    if not transcription:
        return _simple_response("Sorry, I didn't catch that. Goodbye.", end_call=True)

    _log_message(from_number, "user", transcription)

    conversation = CONVERSATIONS.setdefault(from_number, [{"role": "system", "content": SYSTEM_MESSAGE}])
    conversation.append({"role": "user", "content": transcription})

    user_text_lower = transcription.lower()
    is_order_flow = any(k in user_text_lower for k in ["order", "takeout", "pickup", "pick up"])
    is_reservation_flow = any(k in user_text_lower for k in ["reserve", "book", "table"])

    ai_reply = _ask_chatgpt(conversation)

    if is_order_flow:
        ai_reply = _handle_order(from_number, transcription, ai_reply)
    elif is_reservation_flow:
        ai_reply = _handle_reservation(from_number, transcription, ai_reply)

    conversation.append({"role": "assistant", "content": ai_reply})
    _log_message(from_number, "assistant", ai_reply)

    if _should_end_call(transcription, ai_reply):
        return _simple_response(ai_reply + "\nThank you for calling. Goodbye!", end_call=True)
    else:
        return _gather_again(ai_reply)


def _ask_chatgpt(messages: List[dict]) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"}
    data = {"model": "gpt-3.5-turbo", "messages": messages, "temperature": 0.7}
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"].strip()
    return "I'm sorry, I'm having trouble understanding."


def _handle_order(caller: str, user_text: str, ai_reply: str) -> str:
    if caller not in ORDERS:
        ORDERS[caller] = []

    text_lower = user_text.lower()
    done_triggers = ["done", "that's all", "finished"]

    if any(k in text_lower for k in done_triggers):
        items = ORDERS[caller]
        if items:
            print(f"OWNER NOTIFICATION: New order from {caller}: {items}")
            ORDERS[caller] = []
            return ai_reply + "\nYour order is confirmed! We will have it ready soon."
        return ai_reply + "\nIt seems you haven't added any items yet."

    found_items = []
    for category, dishes in MENU_DATA.items():
        for dish in dishes:
            if dish.lower() in text_lower:
                found_items.append(dish)

    if found_items:
        ORDERS[caller].extend(found_items)
        return ai_reply + f"\nI've added {', '.join(found_items)} to your order. Say 'done' when finished."

    return ai_reply


def _handle_reservation(caller: str, user_text: str, ai_reply: str) -> str:
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", user_text)
    time_match = re.search(r"\b(\d{2}:\d{2})\b", user_text)
    party_match = re.search(r"\bfor\s+(\d+)\s*(people|guests|seat|seats)\b", user_text.lower())
    if not party_match:
        party_match = re.search(r"\b(\d+)\s*(people|guests|seat|seats)\b", user_text.lower())
    party_size = int(party_match.group(1)) if party_match else 2

    if date_match and time_match:
        date_str, time_str = date_match.group(1), time_match.group(1)
        if date_str in RESERVATION_SCHEDULE and time_str in RESERVATION_SCHEDULE[date_str]:
            seats_available = RESERVATION_SCHEDULE[date_str][time_str]
            if seats_available <= 0:
                return ai_reply + f"\nSorry, we're fully booked on {date_str} at {time_str}."
            if seats_available < party_size:
                return ai_reply + f"\nWe only have {seats_available} seats left for that time."
            RESERVATION_SCHEDULE[date_str][time_str] = seats_available - party_size
            _send_reservation_sms(caller, date_str, time_str, party_size)
            return ai_reply + f"\nI've reserved a table for {party_size} on {date_str} at {time_str}."
    return ai_reply


def _send_reservation_sms(to_number: str, date_str: str, time_str: str, party_size: int):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    body = (f"Your reservation is confirmed!\nDate: {date_str}\n"
            f"Time: {time_str}\nParty Size: {party_size}")
    try:
        msg = client.messages.create(body=body, from_=TWILIO_PHONE_NUMBER, to=to_number)
        print("SMS for reservation sent, SID:", msg.sid)
    except Exception as e:
        print("SMS Error:", e)


def _should_end_call(user_text: str, ai_reply: str) -> bool:
    user_lower = user_text.lower()
    ai_lower = ai_reply.lower()
    if any(k in user_lower for k in ["bye", "exit", "quit"]):
        return True
    if "thank you for calling" in ai_lower or "goodbye" in ai_lower:
        return True
    return False


def _simple_response(message: str, end_call=False):
    resp = VoiceResponse()
    resp.say(message)
    if end_call:
        resp.hangup()
    return HTMLResponse(str(resp), media_type="application/xml")


def _gather_again(ai_reply: str):
    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        speech_timeout="auto",
        action="/process-speech",
        method="POST",
        language="en-US"
    )
    gather.say(ai_reply)
    resp.append(gather)
    resp.say("I did not receive any input. Goodbye.")
    resp.hangup()
    return HTMLResponse(str(resp), media_type="application/xml")


def _log_message(caller: str, speaker: str, msg: str):
    with open("conversation_logs.txt", "a", encoding="utf-8") as f:
        f.write(f"[{caller} - {speaker.upper()}]: {msg}\n")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
