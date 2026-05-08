from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from models import User
from schemas import MessageInfo, SessionDeleteResponse, SessionInfo, SessionListResponse, SessionMessagesResponse
from services.chat_service import delete_session_for_user, get_session_messages_for_user, list_session_infos_for_user

router = APIRouter()


@router.get("/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, current_user: User = Depends(get_current_user)):
    try:
        messages = [
            MessageInfo(
                type=msg["type"],
                content=msg["content"],
                timestamp=msg["timestamp"],
                rag_trace=msg.get("rag_trace"),
            )
            for msg in get_session_messages_for_user(current_user.username, session_id)
        ]
        return SessionMessagesResponse(messages=messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(current_user: User = Depends(get_current_user)):
    try:
        sessions = [SessionInfo(**item) for item in list_session_infos_for_user(current_user.username)]
        return SessionListResponse(sessions=sessions)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    try:
        deleted = delete_session_for_user(current_user.username, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse(session_id=session_id, message="Session deleted")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
