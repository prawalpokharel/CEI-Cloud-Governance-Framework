import sys
from pathlib import Path

# Tests import both `src.*` (the application) and `tests.*` (the golden spec),
# so the core-engine directory must be on the path regardless of where pytest
# was invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import os

import pytest


@pytest.fixture(scope="session")
def api_client():
    """
    One TestClient for the whole session, shared by every HTTP-level suite.

    Session-scoped deliberately. The database engine and its asyncpg pool are
    module-level singletons created on first use and bound to whichever event
    loop was running then; a second TestClient starts a second loop, and the
    pooled connections fail with "attached to a different loop". One client
    means one loop and one pool.

    The app decides at import time whether to mount the product routers, so
    the environment has to be right before src.main is first imported.
    """
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    os.environ.setdefault("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    os.environ.setdefault("APP_SECRET_KEY", "test-only-secret-not-for-production")

    from fastapi.testclient import TestClient

    import src.main

    assert src.main.PRODUCT_API_ENABLED, (
        "product routers did not mount; the dashboard API would 404 in production"
    )
    with TestClient(src.main.app) as client:
        yield client
