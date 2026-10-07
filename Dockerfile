FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 DISPLAY=:99 MOON_CHROME_PATH=/usr/bin/chromium MOON_CHROME_PROFILE=/data/chrome MOON_DN_LANES=1
RUN apt-get update && apt-get install -y --no-install-recommends chromium xvfb xauth ca-certificates fonts-liberation tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m -u 10001 downloader && mkdir -p /data/chrome && chown -R downloader:downloader /data /app && chmod +x /app/entrypoint.sh
USER downloader
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
