Start the Flask development server.

```bash
source venv/bin/activate && python3 app.py
```

The server starts at http://127.0.0.1:5001 in debug mode.

Pages available:
- `/` — Interactive map (Leaflet + townland boundaries)
- `/census` — Census data browser (1841–1891)
- `/analytics` — KPI dashboards (emigration, eviction, workhouse trends)
- `/ask` — Natural-language Q&A (requires LLM config in `.env.local`)
- `/heritage` — NMS heritage monuments overlay
- `/about` — Project information

API health check:
```bash
curl http://127.0.0.1:5001/api/ask/llm-status
curl http://127.0.0.1:5001/api/townlands/list
```
