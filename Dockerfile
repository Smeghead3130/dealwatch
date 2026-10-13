FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/Smeghead3130/dealwatch"
LABEL org.opencontainers.image.description="Evidence-first OzBargain steal alerts"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src ./src
COPY eval ./eval
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && groupadd -g 568 apps && useradd -u 568 -g 568 -M -s /usr/sbin/nologin apps \
    && mkdir -p /data && chown 568:568 /data
USER 568:568
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import os,time,pathlib; p=pathlib.Path(os.getenv('DATA_DIR','/data'))/'heartbeat'; assert time.time()-float(p.read_text())<90"
CMD ["python", "-m", "dealwatch", "run"]
