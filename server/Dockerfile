FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/server

COPY server/requirements.txt ./requirements.txt
RUN python -m pip install --disable-pip-version-check -r requirements.txt

COPY server/app ./app
COPY server/launcher-release.json server/site.json ./

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin battermc \
    && mkdir -p /app/server/data \
    && chown -R battermc:battermc /app

USER battermc
EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/healthz', timeout=4)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--proxy-headers", "--forwarded-allow-ips=172.16.0.0/12"]
