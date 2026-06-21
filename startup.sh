#!/bin/bash
set -e
cd /home/site/wwwroot

# Create venv on first boot; always sync packages so new requirements.txt
# changes take effect on the next container restart. pip is fast when
# packages are already installed (just version checks, < 5s).
if [ ! -d "antenv" ]; then
    python3 -m venv antenv
fi
antenv/bin/pip install -q -r requirements.txt

exec antenv/bin/python3 -m gunicorn \
    --bind=0.0.0.0:${PORT:-8000} \
    --timeout 600 \
    --workers 2 \
    --worker-class gthread \
    --threads 4 \
    --access-logfile '-' \
    --error-logfile '-' \
    app:app
