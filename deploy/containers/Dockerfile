FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/deckhand/.venv/bin:$PATH"

RUN groupadd --system --gid 19470 deckhand \
    && useradd --system --uid 19470 --gid deckhand --home /nonexistent --shell /usr/sbin/nologin deckhand

WORKDIR /opt/deckhand
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY apps/broker/src ./apps/broker/src
COPY apps/macos-agent/src ./apps/macos-agent/src
COPY packages ./packages
COPY config ./config
RUN uv sync --frozen --no-dev --no-editable \
    && rm -rf /root/.cache/uv \
    # Provide a safe default activation (dh-core only) so a fresh deployment boots
    # read-only without an operator-supplied plugins.yaml. A site overlay can
    # bind-mount its own plugins.yaml over this default.
    && cp config/plugins.example.yaml config/plugins.yaml

USER deckhand:deckhand
EXPOSE 19470
# No hardcoded app entrypoint: each service (broker/worker/scheduler) selects its
# own console script via the compose/systemd command. A hardcoded ENTRYPOINT plus
# a compose `command:` would make the command ARGUMENTS to the entrypoint rather
# than replace it, so every service would run the API. CMD is overridable.
CMD ["deckhand-api"]
