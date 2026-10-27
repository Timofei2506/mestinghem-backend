FROM python:3.13-slim

# Stockfish (chess engine) — installs to /usr/games/stockfish
RUN apt-get update && apt-get install -y --no-install-recommends \
        stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (cached layer)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/backend/requirements.txt

# Application tree. ROOT resolves to /app (backend/app/core/config.py -> parents[3]),
# so workspace/ and legal/ must sit next to backend/.
COPY backend /app/backend
COPY workspace /app/workspace
COPY legal /app/legal

RUN mkdir -p /app/workspace/storage

ENV HOST=0.0.0.0 \
    PORT=7860 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 7860

WORKDIR /app/backend
CMD ["sh", "-c", "exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1 --proxy-headers --forwarded-allow-ips='*' --log-level info"]
