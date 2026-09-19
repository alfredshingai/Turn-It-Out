FROM python:3.13-slim

WORKDIR /app

# No third-party dependencies — stdlib only.
COPY app ./app
COPY public ./public
COPY run.py ./

ENV PYTHONUNBUFFERED=1 \
    TURNITOUT_DEMO=1

# SQLite data lives here; mount a volume for persistence.
VOLUME ["/app/data"]

EXPOSE 8333

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8333"]
