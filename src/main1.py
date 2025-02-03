import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
import requests
from datetime import datetime

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SMS_KEY = os.getenv('SMS_KEY')
PORT = int(os.getenv('PORT', 8010))

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

current_datetime = datetime.now()
current_date = current_datetime.strftime("%d-%m-%Y")
current_time = current_datetime.strftime("%I:%M %p")

SYSTEM_MESSAGE = """
I am Sofia, an AI restaurant hostess at Fine Dining Restaurant. I handle reservations and answer questions about our establishment.

Reservation Requirements:
- Name
- Phone Number (must be valid)
- Number of Guests
- Date
- Time
- Special Requests (optional)

Key Behaviors:
1. Ask only one follow-up question at a time to gather required information
2. After receiving reservation request:
   - Check if the requested time is within operating hours (11 AM to 10 PM)
   - Suggest alternative times if requested time is unavailable
   - Inform about our 15-minute grace period policy
3. Language Protocol:
   - Professional and warm tone
   - Clear pronunciation of booking details
4. Persona: Friendly and efficient restaurant hostess

Menu Information:
- Provide details about daily specials
- Answer questions about dietary accommodations
- Explain signature dishes
- Share information about wine pairings

Sample Interaction Flow:
Guest: "I'd like to make a reservation"
Sofia: "I'd be happy to help you with that. How many guests will be dining with us?"
[Collect guest count]
Sofia: "And what date would you prefer?"
[Collect date]
Sofia: "What time would you like to dine with us?"
[Collect time]
Sofia: "May I have your name for the reservation?"
[Collect name]
Sofia: "And your contact number, please?"
[Collect phone]

Booking Confirmation:
- Summarize all collected details
- Confirm booking
- Mention that a confirmation SMS will be sent
- Share parking information and dress code if applicable

Additional Functions:
- Provide directions to the restaurant
- Answer questions about dietary restrictions
- Explain current COVID-19 policies
- Share information about private dining options

Note: Always verify the booking details before confirmation and handle schedule conflicts professionally.
"""

VOICE = 'alloy'  # Using a professional female voice
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Restaurant AI Hostess is ready to assist you!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response."""
    body = await request.body()
    print("Headers:", request.headers)
    print("Body:", body.decode())
    
    response = VoiceResponse()
    response.say(
        "Welcome to Fine Dining Restaurant. This is Sofia, your virtual hostess. "
        "How may I assist you today?"
    )
    response.pause(length=1)
    
    host = "5a84-73-215-146-74.ngrok-free.app"  # Replace with your domain
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

async def send_reservation_sms(booking_details: dict):
    """Send SMS confirmation using Twilio."""
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    msg_body = (
        f"Your reservation at Fine Dining Restaurant is confirmed!\n"
        f"Details:\n"
        f"Name: {booking_details['name']}\n"
        f"Guests: {booking_details['guests']}\n"
        f"Date: {booking_details['date']}\n"
        f"Time: {booking_details['time']}\n"
        f"Special Requests: {booking_details.get('special_requests', 'None')}\n\n"
        f"Please arrive 5-10 minutes before your reservation time.\n"
        f"For any changes, call us at (555) 123-4567."
    )

    message = client.messages.create(
        body=msg_body,
        from_=TWILIO_PHONE_NUMBER,
        to=booking_details['phone']  # Must be in +1XXXXXXXXXX format if in the U.S.
    )

    return {"status": "success", "message_sid": message.sid}

# [Previous WebSocket handling code remains the same]
# ... [Rest of the WebSocket handling code from the original file]

async def initialize_session(openai_ws):
    """Initialize session with OpenAI with restaurant-specific settings."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.7,  # Slightly lower temperature for more consistent responses
        }
    }
    print('Initializing restaurant hostess AI session:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))
    
from fastapi import WebSocket, WebSocketDisconnect

# ...

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive()

            # Check the kind of frame we received
            if "text" in message:
                # We got a text frame (JSON)
                text_data = message["text"]
                # Parse or handle the JSON
                # e.g., parse Twilio event: start, media, mark, etc.
                handle_twilio_json(text_data)

            elif "bytes" in message:
                # We got binary data (less common with Twilio unless configured for raw audio frames)
                audio_data = message["bytes"]
                # Process raw audio if Twilio is sending mu-law frames directly

    except WebSocketDisconnect:
        print("WebSocket disconnected",audio_data)
        
        
import json

def handle_twilio_json(text_data: str):
    try:
        event = json.loads(text_data)
        # event["event"] might be "start", "media", "stop", etc.
        if event.get("event") == "media":
            # The actual audio payload in base64
            base64_audio = event["media"]["payload"]
            # decode it if needed
            # audio_bytes = base64.b64decode(base64_audio)
            # Now you have raw audio in mu-law or whichever format was set
        elif event.get("event") == "start":
            print("Media stream started!")
        elif event.get("event") == "stop":
            print("Media stream stopped!")
        # etc.
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing Twilio JSON: {e}, text={text_data}")




if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)