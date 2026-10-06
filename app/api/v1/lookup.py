from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.lookup import LookupRequest, LookupResponse
from app.services.business_lookup import lookup_business

router = APIRouter(prefix="/lookup", tags=["lookup"])


@router.post("/business", response_model=LookupResponse)
def lookup(body: LookupRequest, db: Session = Depends(get_db)) -> dict:
    """Paste one line (name + address) and get project fields back, from Google Places when possible.

    Uses 1 Google Places Text Search (Enterprise) call; identical lookups within 7 days are served from cache.
    """
    return lookup_business(db, body.query).as_dict()
