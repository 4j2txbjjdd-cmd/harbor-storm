# Cloud Run image for the HarborWindow / StormSlot substrate.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so application edits do not bust the layer cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY pyproject.toml ./

# Defaults are the deterministic path. A deployment that wants live weather
# sets WEATHER_PROVIDER=google and supplies the key; app/config.py refuses to
# start if it is set without one.
ENV STATE_BACKEND=firestore \
    WEATHER_PROVIDER=mock \
    PORT=8080

RUN useradd --create-home --uid 1001 harbor
USER harbor

EXPOSE 8080
# Cloud Run injects PORT; one worker because the demo pins to one instance.
CMD exec uvicorn app.api:app --host 0.0.0.0 --port ${PORT} --workers 1
