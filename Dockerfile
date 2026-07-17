# syntax=docker/dockerfile:1.7
ARG PYTHON_BUILD_IMAGE=dhi.io/python:3.13-debian13-dev
ARG PYTHON_RUNTIME_IMAGE=dhi.io/python:3.13-debian13
FROM ${PYTHON_BUILD_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_RUNTIME_IMAGE}

ARG APP_UID=65532
ARG APP_GID=65532

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:/opt/python/bin:$PATH" \
    AIFOUNDRY_CONFIG_PATH=/app/examples/foundry.json \
    HOME=/tmp

WORKDIR /app

COPY --chown=${APP_UID}:${APP_GID} --from=builder /app/.venv /app/.venv
COPY --chown=${APP_UID}:${APP_GID} app ./app
COPY --chown=${APP_UID}:${APP_GID} examples/foundry.json ./examples/foundry.json

EXPOSE 8000

USER ${APP_UID}:${APP_GID}

CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
