FROM python:3.11-slim

# system deps (git is needed for applying patches / cloning)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copy minimal sources first for better cache
COPY pyproject.toml README.md /workspace/
COPY sdlc_agent /workspace/sdlc_agent

RUN pip install --no-cache-dir -e ".[dev]"

# (Optional) workflows are useful for template repo; not required for runtime
COPY .github /workspace/.github

ENTRYPOINT ["code-agent"]
