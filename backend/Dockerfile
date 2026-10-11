# Build from the repository root:  docker build -f backend/Dockerfile -t sotp-backend .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt backend/constraints.txt backend/
# The tested versions (backend/constraints.txt), not whatever is newest today.
RUN pip install -r backend/requirements.txt -c backend/constraints.txt

COPY backend backend
COPY scripts scripts

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Apply migrations, then serve. DATABASE_URL and AUTH_JWT_SECRET come from the environment.
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && uvicorn backend.api.main:app --host 0.0.0.0 --port 8000"]
