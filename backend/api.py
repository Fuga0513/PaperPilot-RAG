"""Compatibility router that aggregates split API route modules."""

from fastapi import APIRouter

from routes.auth_routes import router as auth_router
from routes.chat_routes import router as chat_router
from routes.document_routes import router as document_router
from routes.session_routes import router as session_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(session_router)
router.include_router(chat_router)
router.include_router(document_router)
