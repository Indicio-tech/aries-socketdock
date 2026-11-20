# Base image pinned by digest (python 3.12, Debian bookworm).
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
WORKDIR /usr/src/app

ENV POETRY_VERSION=2.5.1
# The base image ships an older pip with known advisories.
RUN pip install --no-cache-dir --upgrade "pip>=26.2" "poetry==$POETRY_VERSION"

COPY poetry.lock pyproject.toml ./
RUN poetry config virtualenvs.create false \
  && poetry install --only main --no-root --no-interaction --no-ansi

COPY ./ locust/

ENTRYPOINT ["locust", "-f", "./locust/locustfile.py" ]
