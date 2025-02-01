
# Base image
# load R packages from binary very quickly
FROM ghcr.io/bioconductor/bioc2u-user:jammy AS base
RUN apt update -y \
    && apt -y install r-bioc-glmgampoi r-cran-sctransform r-cran-lazyeval \
    && apt autoremove --purge && apt clean

# Poetry build image
FROM base AS builder
# Install build dependencies
RUN apt update -y && apt install -y python3-pip \
    python3 python3-pip git wget make build-essential \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libsqlite3-dev wget curl llvm libncurses5-dev xz-utils \
    tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    python3-dev python3-venv libpcre2-dev libdeflate-dev \
    libblas-dev liblapack-dev gfortran \
    && pip3 install poetry==2.0.1
#Poetry setup
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_VIRTUALENVS_CREATE=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache
# Poetry build
WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN touch README.md \
    && poetry install --no-root && rm -rf $POETRY_CACHE_DIR

# Runtime image
FROM base
# Copy virtual env
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
COPY --from=builder ${VIRTUAL_ENV} ${VIRTUAL_ENV}
# Start shell
CMD ["bash"]
