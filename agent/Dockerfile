# Multi-stage so the runtime image carries no build toolchain.
#
# Built for linux/amd64 and linux/arm64: Graviton and Ampere node pools are
# common, and an amd64-only image fails to schedule on them with an error
# most operators read as "the chart is broken".
FROM python:3.12-slim AS build

WORKDIR /build
COPY requirements.txt .

# --prefix keeps the install relocatable into the runtime stage without
# dragging pip, setuptools, and their caches along with it.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim

# Non-root by design. The agent only reads the Kubernetes API over the
# network; it needs no filesystem privileges, and a read-only agent that runs
# as root undermines the trust argument for installing it at all.
RUN groupadd --system --gid 10001 agent \
 && useradd --system --uid 10001 --gid agent --no-create-home agent

COPY --from=build /install /usr/local
COPY cloudoptimizer_agent /app/cloudoptimizer_agent

WORKDIR /app
USER 10001:10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No EXPOSE and no listening socket: the agent makes outbound HTTPS requests
# only, so installing it does not widen the cluster's attack surface.
ENTRYPOINT ["python", "-m", "cloudoptimizer_agent"]
