# syntax=docker/dockerfile:1

FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for scientific / XML packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
        libzstd-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Flask defaults; override in compose or `docker run` if needed
ENV FLASK_APP=app:create_app \
    FLASK_ENV=production \
    FLASK_DEBUG=0 \
    INSTANCE_DIR=/app/instance \
    PYTHONPATH=/app/backend

# Run as a non-root user. uid/gid 1000 matches the host owner used by the dev
# bind mount; in production (named volumes / no bind mount) the chowned dirs
# below are used. NOTE: for the local bind-mounted dev stack, the root-owned
# host instance/ and data/ dirs must be chowned to 1000:1000 before rebuilding
# (sudo chown -R 1000:1000 instance data var).
RUN groupadd -g 1000 appuser \
    && useradd -u 1000 -g 1000 -m -s /usr/sbin/nologin appuser \
    && mkdir -p /app/instance /app/data /app/var \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "90", "app:create_app()"]
