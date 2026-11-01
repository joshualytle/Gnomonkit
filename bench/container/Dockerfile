# Gnomonkit Bench runner image: Claude Code in a throwaway Linux container.
#
# Why a container: on native Windows Claude Code runs shell commands unsandboxed, so a benchmark run
# would execute as the user with access to everything they can reach. Here each run sees only its
# own run directory, its plugins (read-only) and a Claude config directory, as a non-root user with
# no capabilities. The container is the sandbox: Claude Code's bubblewrap sandbox cannot start under
# these restrictions, and loosening them to allow it would weaken the container (see container.py).
#
# Pins: base image by digest; Claude Code by exact version, at least two weeks old when pinned.
FROM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --ignore-scripts=false @anthropic-ai/claude-code@2.1.296 \
 && npm cache clean --force

RUN useradd --no-create-home --home-dir /work/home --uid 10001 bench \
 && mkdir -p /work/run /work/plugins /work/claude-config /work/home \
 && chown -R bench:bench /work
USER bench
WORKDIR /work/run
