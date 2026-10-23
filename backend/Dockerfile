FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
RUN useradd --system --uid 10001 ajo && chown -R ajo /srv
USER ajo
EXPOSE 8000
# API (override the command for worker / scheduler in docker-compose)
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:8000", "--access-logfile", "-"]
