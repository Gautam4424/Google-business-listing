import os
import tempfile

# Must run before any app import: isolated SQLite DB and fake keys (tests never call real APIs).
_DB_FILE = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE}"
os.environ["GOOGLE_API_KEY"] = "test-google-key"
os.environ["SERPAPI_KEY"] = "test-serpapi-key"
os.environ["QUOTA_PLACES_TEXTSEARCH_MONTHLY"] = "5"
os.environ["QUOTA_PLACES_TEXTSEARCH_DAILY"] = "3"
os.environ["PROVIDER_RETRY_DELAY_SECONDS"] = "0"  # retries happen, without waiting
os.environ["DEBUG"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Any request that is not mocked fails loudly instead of reaching Google/SerpApi/websites."""
    import httpx

    def refuse(self, request):
        raise httpx.ConnectError(f"Tests must not call real services ({request.url.host})", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def fresh_db():
    from app.core.config import reset_overrides

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_overrides()  # settings changed in the app by an earlier test never leak into this one
    yield
    reset_overrides()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def enqueued(monkeypatch):
    """Capture enqueued job IDs instead of sending them to Redis."""
    ids = []
    monkeypatch.setattr("app.workers.dispatch.enqueue_job", ids.append)
    return ids


@pytest.fixture
def client(enqueued):
    return TestClient(app)
