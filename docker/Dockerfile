# syntax=docker/dockerfile:1
# Image for the lineageheal command-line tool and the dbt project. Base images are pinned
# to exact versions so a rebuild gives the same result; uv is pinned to the version that
# created uv.lock.
FROM python:3.12.15-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

# Compile bytecode once at build time, copy rather than hard-link (the uv cache lives in a
# separate mount), and use the image's Python instead of letting uv download another.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Run as an ordinary user from the start. Creating it first means nothing has to be
# re-owned later: a recursive chown would copy the whole environment into a second layer.
RUN useradd --create-home --uid 1000 app && mkdir /app && chown app:app /app
USER app
WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lockfile changes. The cache mount
# keeps uv's download cache out of the image.
COPY --chown=app:app pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --no-install-project --no-dev

COPY --chown=app:app README.md LICENSE ./
COPY --chown=app:app src ./src
COPY --chown=app:app dbt ./dbt
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["lineageheal"]
CMD ["--help"]
