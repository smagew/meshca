FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY collector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY collector/meshca_collector ./meshca_collector

RUN useradd --system --uid 10001 --no-create-home meshca
USER meshca

CMD ["python", "-m", "meshca_collector.main"]
