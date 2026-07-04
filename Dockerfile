# syntax=docker/dockerfile:1

# ---- builder stage -------------------------------------------------------
# Install Python deps into a virtualenv so the final image only contains
# what it actually needs.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt \
    && pip install "gunicorn==22.0.0"

# ---- runtime stage -------------------------------------------------------
FROM python:3.12-slim AS runtime

# System tools needed by the checks themselves:
#   curl, wget           -> connectivity probes
#   git                  -> GitHub clone check
#   ca-certificates      -> proper TLS verification
#   openssl              -> for wget TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash curl wget git ca-certificates openssl tini \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT_HTTP=18080 \
    RUN_ON_STARTUP=true

# Copy the pre-built virtualenv.
COPY --from=builder /opt/venv /opt/venv

# Non-root user with a real home for HF cache + pip.
RUN groupadd -r precheck && useradd -r -g precheck -m -d /home/precheck precheck

WORKDIR /app
COPY app/ /app/app/
COPY scripts/precheck.sh /usr/local/bin/precheck.sh
# Bake the default domain list into the image so `docker run` (without a
# ConfigMap mount) uses the same list as the Helm chart. In-cluster this file
# is shadowed by the ConfigMap mounted at /etc/precheck.
COPY config/domains.txt /etc/precheck/domains.txt
RUN chmod +x /usr/local/bin/precheck.sh \
    && chown -R precheck:precheck /app /home/precheck /etc/precheck

USER precheck
EXPOSE 18080

# Use tini so signals reach gunicorn cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "--bind", "0.0.0.0:18080", "--workers", "1", "--threads", "4", \
     "--timeout", "600", "--access-logfile", "-", "app.main:app"]
