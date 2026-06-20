"""
Claude ↔ ElevenLabs Proxy Server
=================================
Bridges ElevenLabs Conversational AI (expects OpenAI format)
to the Anthropic Claude API.

How it works:
  ElevenLabs  →  POST /v1/chat/completions (OpenAI format)
                     ↓  this server
  Anthropic Claude API  ←→  streams back (SSE)
                     ↓
  ElevenLabs  ←  OpenAI-format SSE chunks

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python claude_proxy_server.py

  Then expose publicly (e.g. ngrok):
    ngrok http 8013

  In ElevenLabs Agent settings → Custom LLM:
    Server URL  : https://<your-ngrok>.ngrok.app/v1/chat/completions
    Model ID    : claude-sonnet-4-6   (or any Claude model)
    API Key     : your Anthropic API key (stored as a secret)
"""

import json
import os
import time
import logging
import fastapi
from fastapi.responses import StreamingResponse
from fastapi import Request
import anthropic
import uvicorn
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Anthropic client ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise EnvironmentError(
        "ANTHROPIC_API_KEY is not set. "
        "Run:  export ANTHROPIC_API_KEY=sk-ant-..."
    )

anth_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# ── Model aliases ─────────────────────────────────────────────────────────────
# Maps whatever ElevenLabs sends as "model" → real Anthropic model name.
# Add your own aliases here.
MODEL_ALIASES: Dict[str, str] = {
    # Claude 4.x (current)
    "claude-sonnet-4-6":            "claude-sonnet-4-6",
    "claude-opus-4-6":              "claude-opus-4-6",
    "claude-haiku-4-5":             "claude-haiku-4-5-20251001",
    # If ElevenLabs is set to an OpenAI model name, redirect to Claude
    "gpt-4o":                       "claude-sonnet-4-6",
    "gpt-4-turbo":                  "claude-sonnet-4-6",
    "gpt-3.5-turbo":                "claude-haiku-4-5-20251001",
}
DEFAULT_MODEL = "claude-sonnet-4-6"

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = fastapi.FastAPI(
    title="Claude ↔ ElevenLabs Proxy",
    description="OpenAI-compatible proxy that routes to Anthropic Claude",
    version="1.0.0",
)


# ── Pydantic models (OpenAI-compatible request) ───────────────────────────────
class Message(BaseModel):
    role: str
    content: str


class FunctionDef(BaseModel):
    name: str
    description: Optional[str] = ""
    parameters: Optional[Dict[str, Any]] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class Tool(BaseModel):
    type: str = "function"
    function: FunctionDef


class ChatCompletionRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = DEFAULT_MODEL
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    stream: Optional[bool] = True
    tools: Optional[List[Tool]] = None
    user_id: Optional[str] = None
    # ElevenLabs may inject extra metadata — accept & ignore it
    elevenlabs_extra_body: Optional[Dict[str, Any]] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def resolve_model(model: Optional[str]) -> str:
    if not model:
        return DEFAULT_MODEL
    return MODEL_ALIASES.get(model, model)  # pass through if unknown


def openai_tools_to_anthropic(tools: List[Tool]) -> List[dict]:
    """Convert OpenAI-format tool definitions → Anthropic tool format."""
    return [
        {
            "name": t.function.name,
            "description": t.function.description or "",
            "input_schema": t.function.parameters
            or {"type": "object", "properties": {}},
        }
        for t in tools
    ]


def make_chunk(
    call_id: str,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    """Build an OpenAI-format SSE data line."""
    payload = {
        "id": call_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "default_model": DEFAULT_MODEL}


@app.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
) -> StreamingResponse:

    # ── Step 1: Separate system prompt (Anthropic uses a dedicated param) ────
    system_parts: List[str] = []
    messages: List[Dict[str, str]] = []

    for msg in request.messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        else:
            messages.append({"role": msg.role, "content": msg.content})

    system_text: Optional[str] = "\n\n".join(system_parts) or None

    # Anthropic requires at least one user message
    if not messages:
        messages = [{"role": "user", "content": "Hello"}]

    # ── Step 2: Build Anthropic API kwargs ────────────────────────────────────
    model = resolve_model(request.model)
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens or 1024,
        "messages": messages,
        "temperature": float(min(max(request.temperature or 0.7, 0.0), 1.0)),
    }
    if system_text:
        kwargs["system"] = system_text
    if request.tools:
        kwargs["tools"] = openai_tools_to_anthropic(request.tools)

    logger.info("→ Claude  model=%s  msgs=%d", model, len(messages))

    # ── Step 3: Stream Claude response, re-emit in OpenAI SSE format ─────────
    async def event_stream():
        call_id = f"chatcmpl-{int(time.time() * 1000)}"

        # State for in-progress tool-call accumulation
        in_tool_block: bool = False
        current_tool_id: Optional[str] = None
        current_tool_name: Optional[str] = None
        current_tool_json: str = ""

        try:
            async with anth_client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    etype = event.type

                    # ── A new content block starts ────────────────────────
                    if etype == "content_block_start":
                        block = event.content_block
                        if block.type == "tool_use":
                            in_tool_block = True
                            current_tool_id = block.id
                            current_tool_name = block.name
                            current_tool_json = ""
                            logger.info("  tool_use start: %s", block.name)
                        else:
                            in_tool_block = False

                    # ── Delta chunk arrives ───────────────────────────────
                    elif etype == "content_block_delta":
                        delta = event.delta

                        if not in_tool_block and delta.type == "text_delta":
                            # Normal text — stream immediately
                            yield make_chunk(
                                call_id, model,
                                {"content": delta.text},
                            )

                        elif in_tool_block and delta.type == "input_json_delta":
                            # Tool input JSON — accumulate
                            current_tool_json += delta.partial_json

                    # ── A content block ends ──────────────────────────────
                    elif etype == "content_block_stop":
                        if in_tool_block and current_tool_name:
                            # Emit the complete tool call in OpenAI format
                            yield make_chunk(
                                call_id, model,
                                {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": current_tool_id,
                                            "type": "function",
                                            "function": {
                                                "name": current_tool_name,
                                                "arguments": current_tool_json,
                                            },
                                        }
                                    ]
                                },
                                finish_reason="tool_calls",
                            )
                            logger.info(
                                "  tool_use end: %s  args=%s",
                                current_tool_name, current_tool_json[:80],
                            )
                            in_tool_block = False
                            current_tool_name = None

                    # ── Message finished ──────────────────────────────────
                    elif etype == "message_stop":
                        yield make_chunk(call_id, model, {}, finish_reason="stop")
                        logger.info("← Done   model=%s", model)

            yield "data: [DONE]\n\n"

        except anthropic.APIStatusError as e:
            logger.error("Anthropic API %s: %s", e.status_code, e.message)
            yield f"data: {json.dumps({'error': e.message})}\n\n"

        except anthropic.APIConnectionError as e:
            logger.error("Anthropic connection error: %s", str(e))
            yield f"data: {json.dumps({'error': 'Failed to connect to Anthropic'})}\n\n"

        except Exception as e:
            logger.error("Proxy error: %s", str(e), exc_info=True)
            yield f"data: {json.dumps({'error': 'Internal proxy error'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🔗  Claude ↔ ElevenLabs Proxy")
    print(f"    Default model : {DEFAULT_MODEL}")
    print("    Listening on  : http://0.0.0.0:8013\n")
    uvicorn.run(app, host="0.0.0.0", port=8013, log_level="warning")
