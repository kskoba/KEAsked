# KEA Physician Scheduler — backend only (FastAPI + CP-SAT).
#
# The Electron/React frontend is not part of this image — it always runs
# on the user's desktop and talks to whichever backend is configured
# (this container, or the one it spawns locally on app start). This image
# exists so the CP-SAT solve — the only CPU-heavy part of the app — can
# run on a bigger machine (e.g. an Unraid box) instead of the desktop
# running the UI.
#
# Build (from the repo root, so the `scheduler` package resolves):
#   docker build -t kea-scheduler-backend .
#
# Run, with the org's private physician config folder mounted in (never
# baked into the image — see .gitignore: physicians.yaml and
# scheduler_config.yaml are real per-org data, not committed):
#   docker run -d -p 5000:5000 \
#     -v /path/to/your/config:/config \
#     --name kea-scheduler-backend \
#     kea-scheduler-backend
#
# CONFIG_DIR is the same environment variable the desktop app's packaged
# build already sets (see scheduler/backend/config.py's _resolve_config_dir
# and scheduler/api/server.py's matching _CONFIG_DIR) — pointing it at a
# mounted volume here needs no code changes at all.

FROM python:3.12-slim

WORKDIR /app

# System deps for building any wheel that needs compiling (ortools ships
# manylinux wheels for this Python version, but keep a C toolchain around
# as a fallback so a version bump doesn't silently break the build).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY scheduler/requirements.txt scheduler/requirements.txt
RUN pip install --no-cache-dir -r scheduler/requirements.txt

# Only the backend package and tools it might need at runtime — not the
# frontend, not real physician data (that's volume-mounted, see below).
COPY scheduler/ scheduler/
COPY tools/ tools/

ENV CONFIG_DIR=/config
ENV PYTHONUNBUFFERED=1
# Required for a container: without this the app binds to loopback only,
# which Docker's -p port mapping cannot reach from outside the container
# no matter how the port is published (see server.py's bind_host).
ENV BACKEND_HOST=0.0.0.0

EXPOSE 5000

CMD ["python3", "-m", "scheduler.api.server"]
