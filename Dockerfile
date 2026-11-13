FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd -u 1000 -m store && mkdir -p /data && chown store:store /data
USER store

EXPOSE 8000

# Docker marks the container unhealthy if the store stops answering
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# 2 processes x 8 threads: slow visitors (or building an order photo) no longer block everyone else
CMD ["gunicorn", "--preload", "-w", "2", "-k", "gthread", "--threads", "8", \
     "--timeout", "60", "--graceful-timeout", "20", "--keep-alive", "5", \
     "-b", "0.0.0.0:8000", "--access-logfile", "-", \
     "--access-logformat", "%({cf-connecting-ip}i)s host=%({host}i)s \"%(r)s\" %(s)s %(M)sms", \
     "app:app"]
