# Core engine. Mirrors the Railway runtime (Procfile: uvicorn src.main:app)
# so what runs in docker compose locally is what runs in production, minus
# the platform.
FROM python:3.12-slim

# A non-root user, matching the posture the agent images already have.
RUN useradd --uid 10001 --create-home appuser

WORKDIR /app

# Requirements first, so source edits do not re-install dependencies.
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # Cloud extras included in the dev image: the CSPM endpoints should be
    # testable locally. Production deployments that never use them can build
    # with --build-arg INSTALL_CLOUD=0 later; ~40 MB is a fine trade for a
    # dev image that exercises everything.
    && pip install --no-cache-dir -r requirements-cloud.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src

USER appuser
EXPOSE 8000

# Migrations run before the server starts: a dev environment where the schema
# silently lags the code produces the exact class of confusing failure this
# repo has already debugged once (a test database that had never been
# migrated). Production platforms run migrations as a release step; this is
# the compose-local equivalent.
CMD ["sh", "-c", "python -m alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port 8000"]
