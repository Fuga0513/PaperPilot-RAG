"""Central runtime configuration for PaperPilot-RAG.

The project still keeps module-level constants in a few legacy modules, but new
code should read environment-backed values from this module so paths and service
settings do not drift across routers, services, and tools.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DOCUMENT_UPLOAD_DIR = DATA_DIR / "documents"
PAPER_UPLOAD_ROOT = DATA_DIR / "uploads"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LLMConfig:
    api_key: str | None = os.getenv("ARK_API_KEY")
    model: str | None = os.getenv("MODEL")
    base_url: str | None = os.getenv("BASE_URL")
    grade_model: str = os.getenv("GRADE_MODEL", "gpt-4.1")


@dataclass(frozen=True)
class RerankConfig:
    model: str | None = os.getenv("RERANK_MODEL")
    binding_host: str | None = os.getenv("RERANK_BINDING_HOST")
    api_key: str | None = os.getenv("RERANK_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.model and self.binding_host and self.api_key)

    @property
    def endpoint(self) -> str:
        if not self.binding_host:
            return ""
        host = self.binding_host.strip().rstrip("/")
        return host if host.endswith("/v1/rerank") else f"{host}/v1/rerank"


@dataclass(frozen=True)
class RetrievalConfig:
    auto_merge_enabled: bool = env_bool("AUTO_MERGE_ENABLED", True)
    auto_merge_threshold: int = env_int("AUTO_MERGE_THRESHOLD", 2)
    leaf_retrieve_level: int = env_int("LEAF_RETRIEVE_LEVEL", 3)


@dataclass(frozen=True)
class WeatherConfig:
    amap_weather_api: str | None = os.getenv("AMAP_WEATHER_API")
    amap_api_key: str | None = os.getenv("AMAP_API_KEY")


@dataclass(frozen=True)
class ServerConfig:
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = env_int("PORT", 8000)


LLM = LLMConfig()
RERANK = RerankConfig()
RETRIEVAL = RetrievalConfig()
WEATHER = WeatherConfig()
SERVER = ServerConfig()
