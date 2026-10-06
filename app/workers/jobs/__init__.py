# Importing a job module registers its pipeline.
from app.workers.jobs import (  # noqa: F401
    diagnostic,
    discover_business,
    gbp_audit,
    ranking_check,
    review_analysis,
    website_discovery,
)
