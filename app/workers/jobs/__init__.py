# Importing a job module registers its pipeline.
from app.workers.jobs import (  # noqa: F401
    competitor_analysis,
    diagnostic,
    discover_business,
    full_audit,
    gbp_audit,
    maintenance,
    ranking_check,
    review_analysis,
    website_discovery,
)
