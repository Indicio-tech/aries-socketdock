# Base image pinned by digest (python 3.12, Debian bookworm). Update the digest
# deliberately, e.g. via Dependabot's docker ecosystem.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS base
WORKDIR /usr/src/app

ENV POETRY_VERSION=2.5.1
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"
RUN poetry config virtualenvs.in-project true

# Setup project (production dependencies only)
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /usr/src/app
COPY --from=base /usr/src/app/.venv /usr/src/app/.venv
ENV PATH="/usr/src/app/.venv/bin:$PATH"

COPY socketdock socketdock

ENTRYPOINT ["python",  "-m", "socketdock" ]
