import os
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

@router.get("/stores")
def get_stores():
    from backend.database import get_active_stores
    return get_active_stores()

def _require_owner(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user["is_owner"]:
        raise HTTPException(status_code=403, detail="Only the owner account can change this setting.")
    return user

ITEM_MATCHING_AI_SETTING_KEY = "item_matching_ai_enabled"

def _item_matching_ai_default() -> bool:
    return os.getenv("ITEM_MATCHING_AI_ENABLED", "true").strip().lower() not in ("false", "0", "no")

class ToggleRequest(BaseModel):
    enabled: bool

@router.get("/item-matching-ai")
def get_item_matching_ai(request: Request):
    _require_owner(request)
    from backend.database import get_app_setting
    stored = get_app_setting(ITEM_MATCHING_AI_SETTING_KEY)
    enabled = (stored.strip().lower() not in ("false", "0", "no")) if stored is not None else _item_matching_ai_default()
    return {"enabled": enabled}

@router.put("/item-matching-ai")
def set_item_matching_ai(payload: ToggleRequest, request: Request):
    _require_owner(request)
    from backend.database import set_app_setting
    set_app_setting(ITEM_MATCHING_AI_SETTING_KEY, "true" if payload.enabled else "false")
    return {"enabled": payload.enabled}

EXTRACTION_PROVIDER_SETTING_KEY = "extraction_provider"
VALID_EXTRACTION_PROVIDERS = ("gemini", "qwen")

def _extraction_provider_default() -> str:
    provider = os.getenv("EXTRACTION_PROVIDER", "gemini").strip().lower()
    return provider if provider in VALID_EXTRACTION_PROVIDERS else "gemini"

class ExtractionProviderRequest(BaseModel):
    provider: str

@router.get("/extraction-provider")
def get_extraction_provider_setting(request: Request):
    _require_owner(request)
    from backend.database import get_app_setting
    stored = get_app_setting(EXTRACTION_PROVIDER_SETTING_KEY)
    provider = stored.strip().lower() if stored else _extraction_provider_default()
    if provider not in VALID_EXTRACTION_PROVIDERS:
        provider = "gemini"
    return {"provider": provider}

@router.put("/extraction-provider")
def set_extraction_provider_setting(payload: ExtractionProviderRequest, request: Request):
    _require_owner(request)
    provider = payload.provider.strip().lower()
    if provider not in VALID_EXTRACTION_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"provider must be one of {VALID_EXTRACTION_PROVIDERS}")
    from backend.database import set_app_setting
    set_app_setting(EXTRACTION_PROVIDER_SETTING_KEY, provider)
    return {"provider": provider}
