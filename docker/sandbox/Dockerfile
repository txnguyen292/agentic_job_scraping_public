FROM python:3.13-slim

LABEL org.opencontainers.image.title="job-scraper-sandbox"
LABEL org.opencontainers.image.description="No-network runtime image for job page analysis sandboxes."

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        jq \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY docker/sandbox/requirements.txt /tmp/sandbox-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/sandbox-requirements.txt \
    && rm /tmp/sandbox-requirements.txt

USER 65532:65532
