#!/bin/bash
# Local development only.
# On Azure, Oryx reads the Procfile to start gunicorn automatically.
gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 --workers 2 --worker-class gthread --threads 4 --access-logfile '-' --error-logfile '-' app:app
