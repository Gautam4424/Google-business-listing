FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home appuser
WORKDIR /app
RUN chown appuser:appuser /app

COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=true
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; else pip install -r requirements.txt; fi

# Headless Chromium for websites that only render with JavaScript (Phase 2 fallback).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG INSTALL_BROWSER=true
RUN if [ "$INSTALL_BROWSER" = "true" ]; then       python -m playwright install --with-deps chromium && chmod -R a+rx /ms-playwright;     fi

COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
