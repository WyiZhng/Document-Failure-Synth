FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/.playwright-browsers

WORKDIR /app

# Install dependencies before copying application files so this layer stays
# cacheable when only source code changes.
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.lock.txt \
    && mkdir -p /app/.playwright-browsers \
    && python -m playwright install --with-deps chromium

COPY src ./src
COPY tests ./tests
COPY explain ./explain
COPY config ./config
COPY scripts ./scripts
COPY pytest.ini README.md .env.example ./

ENTRYPOINT ["python", "-m", "src.synth.runner"]
CMD ["--config", "config/synth.yaml"]
