from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.database import get_app_setting, set_app_setting

router = APIRouter()

class UpdateSettingRequest(BaseModel):
    key: str
    value: str

@router.get("/")
def get_all_settings():
    # Only gst_recording_method is needed for now
    gst_recording_method = get_app_setting("gst_recording_method", "separate_ledger")
    return {
        "gst_recording_method": gst_recording_method
    }

@router.put("/")
def update_setting(payload: UpdateSettingRequest):
    allowed_keys = ["gst_recording_method"]
    if payload.key not in allowed_keys:
        raise HTTPException(status_code=400, detail=f"Setting key {payload.key} not allowed.")
    
    if payload.key == "gst_recording_method":
        if payload.value not in ["separate_ledger", "included_in_rate"]:
            raise HTTPException(status_code=400, detail="Invalid value for gst_recording_method.")
            
    set_app_setting(payload.key, payload.value)
    return {"status": "success", "key": payload.key, "value": payload.value}

@router.get("/stores")
def get_stores():
    from backend.database import get_active_stores
    return get_active_stores()
