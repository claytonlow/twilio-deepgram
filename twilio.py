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
from typing import Dict
import time
from flowise import Flowise, PredictionData, IMessage, IFileUpload
import os
from dotenv import load_dotenv
load_dotenv()


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
        "description": """Use this function to provide natural conversational filler before looking up information.
        ALWAYS call this function first with message_type='lookup' when you're about to look up customer information.
        After calling this function, you MUST immediately follow up with the appropriate lookup function (e.g., find_customer).""",
        "parameters": {
            "type": "object",
            "properties": {
                "message_type": {
                    "type": "string",
                    "description": "Type of filler message to use. Use 'lookup' when about to search for information.",
                    "enum": ["lookup", "general"]
                },
                "question": {
                    "type": "string",
                    "description": "The question asked by the user",
                    "enum": ["lookup", "general"]
                }
            },
            "required": ["message_type", "question"]
        }
    }
]

# Optionally serve static files
# app.mount("/static", StaticFiles(directory="static"), name="static")

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
                        print("yooo")
                        function_name = decoded['function_name']
                        func = FUNCTION_MAP.get(function_name)
                        question = decoded["input"]["question"]
                        print(question)
                        if not func:
                            raise ValueError(f"Function {function_name} not found")

                        if function_name in ["test"]:
                            result = await func(question)
                            
                            print(result)

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

@app.post("/flowise/chat/{chat_id}")
async def root(request: Request):
    body = await request.json()
    print(body)
    return {
    "id": "chatcmpl-7d364854-19c4-4a3d-868f-14a3623d636c",
    "object": "chat.completion",
    "created": 1737589428,
    "model": "customflowise",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Fast language models are crucial in the field of natural language processing (NLP) due to their ability to efficiently and accurately process vast amounts of language data. The importance of fast language models can be summarized as follows:\n\n1. **Improved Response Times**: Fast language models can respond to user queries in real-time, which is essential for applications that require immediate interaction, such as chatbots, virtual assistants, and language translation systems.\n2. **Enhanced User Experience**: By providing quick and accurate responses, fast language models can significantly improve the overall user experience. This is particularly important for applications that rely on user engagement, such as customer service chatbots, language learning platforms, and online forums.\n3. **Increased Efficiency**: Fast language models can process large volumes of language data quickly, allowing them to be used in a wide range of applications, including text analysis, sentiment analysis, and topic modeling. This increased efficiency enables organizations to automate tasks, reduce manual labor, and improve overall productivity.\n4. **Real-Time Analytics**: Fast language models can analyze streaming data, such as social media posts, news articles, and online reviews, in real-time. This allows organizations to gain instant insights into customer opinions, market trends, and emerging topics.\n5. **Competitive Advantage**: Organizations that adopt fast language models can gain a competitive advantage by being able to respond quickly to changing market conditions, customer needs, and emerging trends.\n6. **Scalability**: Fast language models can handle large volumes of data, making them scalable for applications that require processing vast amounts of language data, such as data warehousing, business intelligence, and data science.\n7. **Multilingual Support**: Fast language models can support multiple languages, enabling organizations to expand their reach and interact with customers worldwide.\n8. **Improved Accuracy**: By processing language data quickly, fast language models can also improve accuracy by reducing the impact of noise, ambiguity, and uncertainty in the data.\n9. **Support for Edge AI**: Fast language models can run on edge devices, such as smartphones, smart home devices, and autonomous vehicles, enabling AI-powered applications to operate in real-time, even in areas with limited internet connectivity.\n10. **Advancements in Research**: Fast language models can accelerate research in NLP, enabling researchers to explore new ideas, test hypotheses, and develop more advanced language models, which can lead to breakthroughs in areas like language understanding, generation, and reasoning.\n\nSome of the applications that benefit from fast language models include:\n\n* Virtual assistants (e.g., Siri, Alexa, Google Assistant)\n* Chatbots and customer service platforms\n* Language translation systems\n* Sentiment analysis and opinion mining\n* Text summarization and extraction\n* Language learning platforms\n* Social media monitoring and analytics\n* Edge AI applications (e.g., smart home devices, autonomous vehicles)\n\nIn summary, fast language models are essential for a wide range of applications that require efficient, accurate, and real-time processing of language data. Their importance lies in their ability to improve response times, enhance user experience, increase efficiency, and provide real-time insights, ultimately leading to a competitive advantage and advancements in research."
            },
            "logprobs": None,
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "queue_time": 0.026058129000000003,
        "prompt_tokens": 43,
        "prompt_time": 0.00741068,
        "completion_tokens": 632,
        "completion_time": 2.2981818179999998,
        "total_tokens": 675,
        "total_time": 2.305592498
    },
    "system_fingerprint": "fp_4196e754db",
    "x_groq": {
        "id": "req_01jj86d4gjeqraajfvd58p7bsf"
    }

}

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
    Returns a simple confirmation first, then sends the actual message to the client.
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