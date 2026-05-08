import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from auth import get_current_user
from models import User
from schemas import ChatRequest, ChatResponse
from services.chat_service import map_model_exception, run_chat, stream_chat_events

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        return ChatResponse(**run_chat(request, current_user))
    except Exception as e:
        raise map_model_exception(e)


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    async def event_generator():
        try:
            async for chunk in stream_chat_events(request, current_user):
                yield chunk
        except Exception as e:
            error_data = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
