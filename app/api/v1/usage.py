from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.job import UsageOut
from app.services.quota import usage_report

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=list[UsageOut])
def get_usage(db: Session = Depends(get_db)) -> list[UsageOut]:
    """Free-tier usage this month per billable SKU."""
    return [
        UsageOut(
            sku=u.sku,
            day_count=u.day_count,
            daily_limit=u.daily_limit,
            month_count=u.month_count,
            monthly_limit=u.monthly_limit,
            remaining=u.remaining,
        )
        for u in usage_report(db)
    ]
