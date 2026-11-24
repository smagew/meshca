FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY migrate/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY migrate/meshca_migrate ./meshca_migrate
COPY db/migrations ./migrations

RUN useradd --system --uid 10001 --no-create-home meshca
USER meshca

CMD ["python", "-m", "meshca_migrate"]
