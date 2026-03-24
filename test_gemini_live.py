# test_gemini_live.py
import asyncio
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = os.getenv("GEMINI_MODEL")

print(f"Using project: {PROJECT_ID}")
print(f"Location : {LOCATION}")
print(f"Using model: {MODEL}")

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION
)

async def test():
    config = {"response_modalities": ["AUDIO"]}

    print("\nConnecting to Gemini Live API...")

    async with client.aio.live.connect(
        model=MODEL,
        config=config
    ) as session:
        print("Connected! Sending test message...\n")

        await session.send_client_content(
            turns={
                "role": "user",
                "parts": [{"text": "You are Priya, customer care agent for Imagicaa theme park. A customer named Rahul abandoned a cart for 2 adults + 1 child for 29 March visit. Give your opening line in Hinglish."}]
            },
            turn_complete=True
        )   

        audio_data = bytearray()

        async for response in session.receive():
            server_content = response.server_content
            if server_content is not None:
                model_turn = server_content.model_turn
                if model_turn is not None:
                    for part in model_turn.parts:
                        # Print transcript if available
                        if part.text:
                            print("Gemini Transcript:", part.text)
                        # Collect audio bytes
                        if part.inline_data and part.inline_data.data:
                            audio_data.extend(part.inline_data.data)
                
                # Break the loop when the model is done speaking its turn
                if server_content.turn_complete:
                    break

        if audio_data:
            print(f"\nReceived {len(audio_data)} bytes of audio. Saving to 'output.wav'...")
            import wave
            with wave.open("output.wav", "wb") as wf:
                wf.setnchannels(1)       # Mono
                wf.setsampwidth(2)       # 16-bit PCM
                wf.setframerate(24000)   # 24kHz
                wf.writeframes(audio_data)
            print("✅ Audio saved successfully! You can listen to it by running: afplay output.wav")
        else:
            print("\n❌ No audio data received.")

        print("\n✅ Gemini Live API testing complete!")

asyncio.run(test())