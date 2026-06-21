web: gunicorn --bind=0.0.0.0:$PORT --timeout 600 --workers 2 --worker-class gthread --threads 4 --access-logfile '-' --error-logfile '-' app:app
