FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMFYBENCH_HOME=/data \
    COMFYBENCH_HOST=0.0.0.0 \
    COMFYBENCH_PORT=8765

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Program
COPY bench.py report_template.html ./
# Seed data – copied to /data on first start if nothing is there yet
COPY config.yaml prompts.yaml README.md /app/defaults/
COPY workflows /app/defaults/workflows
COPY inputs /app/defaults/inputs
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/bench /usr/local/bin/bench
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/bench \
 && mkdir -p /data && chmod 777 /data

VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"COMFYBENCH_PORT\",\"8765\")}/api/run/status', timeout=4)" || exit 1

ENTRYPOINT ["entrypoint.sh"]
CMD ["serve"]
