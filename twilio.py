import asyncio
import base64
import json
import sys
import websockets
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
import requests
from typing import Dict
import time
from flowise import Flowise, PredictionData, IMessage, IFileUpload
from dotenv import load_dotenv
load_dotenv()
import os


app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Specify your allowed origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FUNCTION_DEFINITIONS = [
    {
        "name": "flowiseQuery",
        "description": """Use this function to provide natural conversational with our ai agent.""",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "Type of filler message to use. Use 'lookup' when about to search for information.",
                    "enum": ["general"]
                },
                "question": {
                    "type": "string",
                    "description": "The question asked by the user",
                    "enum": ["general"]
                }
            },
            "required": ["message_type", "question"]
        }
    }
]

def sts_connect():
    sts_ws = websockets.connect(
        "wss://agent.deepgram.com/agent",
        subprotocols=["token", os.getenv('DEEPGRAM_TOKEN')],
    )
    return sts_ws

async def twilio_handler(twilio_ws: WebSocket):
    print(os.getenv('FLOWISE_TOKEN'))
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    async with sts_connect() as sts_ws:
        config_message = {
            "type": "SettingsConfiguration",
            "audio": {
                "input": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                },
                "output": {
                    "encoding": "mulaw",
                    "sample_rate": 8000,
                    "container": "none",
                },
            },
            "agent": {
                "listen": {"model": "nova-2"},
                "think": {
                    "provider": {
                        "type": "anthropic",
                    },
                    "model": "claude-3-haiku-20240307",
                    "instructions": "You own a mexican restaurant.",
                    "functions": FUNCTION_DEFINITIONS,

                },
                "speak": {"model": "aura-asteria-en"},
            },
        }

        await sts_ws.send(json.dumps(config_message))

        initial_message = {
            "type": "InjectAgentMessage",
            "message": "Hello! I'm lola, welcome to Aychihuahua Restaurant. How can I help you today?"
        }

        await asyncio.sleep(1)
        await sts_ws.send(json.dumps(initial_message))

        async def sts_sender(sts_ws):
            print("sts_sender started")
            while True:
                chunk = await audio_queue.get()
                await sts_ws.send(chunk)

        async def sts_receiver(sts_ws):
            print("sts_receiver started")
            streamsid = await streamsid_queue.get()
            async for message in sts_ws:
                if isinstance(message, str):
                    print(message)
                    decoded = json.loads(message)
                    if decoded['type'] == 'UserStartedSpeaking':
                        clear_message = {
                            "event": "clear",
                            "streamSid": streamsid
                        }
                        await twilio_ws.send_text(json.dumps(clear_message))
                    elif decoded['type'] == 'FunctionCallRequest':
                        function_name = decoded['function_name']
                        func = FUNCTION_MAP.get(function_name)
                        question = decoded["input"]["question"]
                        print(question)
                        if not func:
                            raise ValueError(f"Function {function_name} not found")

                        if function_name in ["flowiseQuery"]:
                            result = await func(question)
                            await sts_ws.send(json.dumps({
                                "type": "InjectAgentMessage",
                                "message": result
                            }))
                        
                    #     continue
                    continue

                raw_mulaw = message
                media_message = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                }
                await twilio_ws.send_text(json.dumps(media_message))

        async def twilio_receiver(twilio_ws):
            print("twilio_receiver started")
            BUFFER_SIZE = 20 * 160
            inbuffer = bytearray(b"")
            try:
                async for message in twilio_ws.iter_text():
                    try:
                        data = json.loads(message)
                        if data["event"] == "start":
                            await streamsid_queue.put(data["start"]["streamSid"])
                        if data["event"] == "media":
                            chunk = base64.b64decode(data["media"]["payload"])
                            if data["media"]["track"] == "inbound":
                                inbuffer.extend(chunk)
                        while len(inbuffer) >= BUFFER_SIZE:
                            await audio_queue.put(inbuffer[:BUFFER_SIZE])
                            inbuffer = inbuffer[BUFFER_SIZE:]
                    except json.JSONDecodeError:
                        print("Error decoding JSON message")
                    except Exception as e:
                        print(f"Error processing message: {e}")
            except Exception as e:
                print(f"WebSocket error: {e}")

        await asyncio.gather(
            sts_sender(sts_ws),
            sts_receiver(sts_ws),
            twilio_receiver(twilio_ws)
        )

# HTTP Routes
@app.get("/")
async def root():
    return {"message": "Twilio Voice AI Server"}


@app.get("/twilio/numbers")
async def root():
    # Find your Account SID and Auth Token at twilio.com/console
    # and set the environment variables. See http://twil.io/secure
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    
    # API endpoint
    url = f'https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json'

    # Make the request
    response = requests.get(url, auth=(account_sid, auth_token))

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the JSON response
        phone_numbers = response.json()
        return phone_numbers
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return {"error": "Unable to fetch phone numbers"}

    

# WebSocket route
@app.websocket("/twilio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(f"New WebSocket connection established")
    try:
        await twilio_handler(websocket)
    except Exception as e:
        print(f"Error in twilio_handler: {e}")
    finally:
        await websocket.close()


async def flowiseQuery(question: str):
    """
    Handle agent filler messages while maintaining proper function call protocol.
    """
    client = Flowise('https://flowiseai.dbctechnology.com', os.getenv('FLOWISE_TOKEN'))

    # Create a prediction without streaming
    completion = client.create_prediction(
        PredictionData(
            chatflowId="675de8d4-72bf-4d75-b9e0-975acb4c8999",
            question=question,
            streaming=False  # Non-streaming mode
        )
    )

    # Process and print the full response
    for response in completion:
        return response["text"]


FUNCTION_MAP = {
    "flowiseQuery": flowiseQuery,
}

def run_server():
    uvicorn.run(
        "twilio:app",
        host="0.0.0.0",
        port=5000,
        reload=True,  # Enable auto-reload during development
        ws_ping_interval=30,  # WebSocket ping interval in seconds
        ws_ping_timeout=30,  # WebSocket ping timeout in seconds
    )

if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        print("Server stopped by user")
        sys.exit(0)