from __future__ import annotations

import logging
from pathlib import Path
from flask import Flask

from backend.config import ActiveConfig

log = logging.getLogger(__name__)

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _LIMITER_AVAILABLE = True
except ImportError:
    _LIMITER_AVAILABLE = False
    log.warning("create_app.flask_limiter_missing — rate limiting disabled; run: pip install flask-limiter")


def create_app(config_class=None) -> Flask:
    if config_class is None:
        config_class = ActiveConfig

    _root = Path(__file__).resolve().parents[1]

    app = Flask(
        __name__,
        template_folder=str(_root / "frontend" / "templates"),
        static_folder=str(_root / "frontend" / "static"),
    )

    app.config["SECRET_KEY"] = config_class.SECRET_KEY
    if not config_class.DEBUG and config_class.SECRET_KEY == "dev-secret-change-in-prod":
        log.error(
            "create_app.insecure_secret_key — SECRET_KEY is the default placeholder. "
            "Set a strong random value in the SECRET_KEY environment variable before deploying."
        )
    app.config["DATABASE_PATH"] = config_class.DATABASE_PATH
    app.config["VRTI_SPARQL_ENDPOINT"] = config_class.VRTI_SPARQL_ENDPOINT
    app.config["CENSUS_STALE_AFTER_DAYS"] = config_class.CENSUS_STALE_AFTER_DAYS
    app.config["EXPORTS_DIR"] = config_class.EXPORTS_DIR
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

    from backend.extensions import init_db, ensure_schema
    init_db(config_class.DATABASE_PATH)
    ensure_schema()

    if _LIMITER_AVAILABLE:
        limiter = Limiter(
            key_func=get_remote_address,
            app=app,
            default_limits=[],
            storage_uri="memory://",
        )
        app.extensions["limiter"] = limiter
    else:
        app.extensions["limiter"] = None

    from backend.routes.main import bp as main_bp
    from backend.routes.census import bp as census_bp
    from backend.routes.unified import bp as unified_bp
    from backend.routes.map_config import bp as map_bp
    from backend.routes.townlands import bp as townlands_bp
    from backend.routes.exports import bp as exports_bp
    from backend.routes.ask import bp as ask_bp
    from backend.routes.kg_explore import bp as kg_explore_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(census_bp,    url_prefix="/api/census")
    app.register_blueprint(unified_bp,   url_prefix="/api/unified")
    app.register_blueprint(map_bp,       url_prefix="/api/map")
    app.register_blueprint(townlands_bp, url_prefix="/api/townlands")
    app.register_blueprint(exports_bp,   url_prefix="/api/exports")
    app.register_blueprint(ask_bp,       url_prefix="/api/ask")
    app.register_blueprint(kg_explore_bp, url_prefix="/api/kg")

    _apply_ask_rate_limits(app)

    @app.after_request
    def _add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if "text/html" in response.content_type:
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net https://www.youtube.com https://www.youtube-nocookie.com https://www.google-analytics.com; "
                    "style-src 'self' 'unsafe-inline' https://unpkg.com https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com; "
                    "img-src 'self' data: https:; "
                    "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
                    "connect-src 'self';"
                ),
            )
        return response

    _register_legacy_routes(app)

    import threading

    def _prewarm():
        with app.app_context():
            try:
                from backend.services.unified_service import _get_all_records
                _get_all_records()
                log.info("create_app.prewarm_complete")
            except Exception as exc:
                log.warning("create_app.prewarm_failed error=%s", exc)

    threading.Thread(target=_prewarm, daemon=True).start()

    log.info("create_app.ready | blueprints registered")
    return app


def _apply_ask_rate_limits(app: Flask) -> None:
    limiter = app.extensions.get("limiter")
    if limiter is None:
        return
    limiter.limit("30 per minute; 200 per hour")(
        app.view_functions["ask_api.ask_query"]
    )
    limiter.limit("60 per minute")(
        app.view_functions["ask_api.ask_feedback"]
    )


def _register_legacy_routes(app: Flask) -> None:
    from flask import jsonify

    @app.get("/api/centroids")
    def api_centroids_legacy():
        from backend.services.map_service import build_centroids
        return jsonify(build_centroids())

    @app.get("/api/workhouse/match/<record_id>")
    def api_workhouse_match_legacy(record_id: str):
        from backend.services.workhouse_service import get_matches_for_record
        return jsonify(get_matches_for_record(record_id))
