FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/srv/data

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY fixtures ./fixtures
COPY docker/entrypoint.sh /entrypoint.sh

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /srv/data \
    && chmod +x /entrypoint.sh \
    && chown -R appuser:appuser /srv

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

ENTRYPOINT ["/entrypoint.sh"]
