FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd -u 1000 -m store && mkdir -p /data && chown store:store /data
USER store

EXPOSE 8000
CMD ["gunicorn", "--preload", "-w", "2", "-b", "0.0.0.0:8000", "--access-logfile", "-", "--access-logformat", "%(h)s host=%({host}i)s via=%({x-forwarded-for}i)s \"%(r)s\" %(s)s", "app:app"]
