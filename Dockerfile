FROM python:3.11-slim


RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace


COPY pyproject.toml README.md /workspace/
COPY sdlc_agent /workspace/sdlc_agent

RUN pip install --no-cache-dir -e ".[dev]"


COPY .github /workspace/.github


CMD ["sh", "-c", "tail -f /dev/null"]

