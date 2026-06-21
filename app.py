"""
app.py — entry point

Run with:
  python3 app.py
"""
from create_app import create_app

app = create_app()

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    # Only enable debug when explicitly running development mode.
    # Defaults to False so a missing env var never exposes the debugger.
    debug = os.environ.get("FLASK_ENV", "").strip().lower() == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
