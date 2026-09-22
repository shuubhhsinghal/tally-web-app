from fastapi import APIRouter

router = APIRouter()

@router.get("/stores")
def get_stores():
    from backend.database import get_active_stores
    return get_active_stores()
