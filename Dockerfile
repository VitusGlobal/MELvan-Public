FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MELVAN_HOST=0.0.0.0 \
    MELVAN_PORT=8000

WORKDIR /app

RUN groupadd --system melvan && useradd --system --gid melvan --home /app melvan

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir ".[production]"

RUN mkdir -p /tmp/melvan /var/lib/melvan/attachments && chown -R melvan:melvan /app /tmp/melvan /var/lib/melvan
USER melvan

EXPOSE 8000

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "melvan.production:create_application()"]
