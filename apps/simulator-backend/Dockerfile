# Unified control-plane image. The default command exposes the API only.
# Compose's PSTN overlay changes the command to add `--with-pstn` after all
# public-webhook credentials are configured and /api remains private.

FROM python:3.12-slim

# uv for fast, locked installs (mirrors local dev).
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 simulator \
    && mkdir -p /var/lib/telephony-voice-simulator \
    && chown simulator:simulator /var/lib/telephony-voice-simulator

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    PYTHONPATH=/app/src
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=simulator:simulator src/ src/

ENV SIMULATOR_RUNTIME_DIR=/var/lib/telephony-voice-simulator \
    SIMULATOR_CORPUS_ASSETS_DIR=/var/lib/telephony-voice-simulator/corpus-assets \
    SIMULATOR_CORPUS_MANIFEST=/var/lib/telephony-voice-simulator/assets_manifest.json
USER simulator

EXPOSE 8978
CMD ["python", "-m", "telephony_voice_simulator.control.cli", "serve", "--host", "0.0.0.0"]
