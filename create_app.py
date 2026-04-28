"""
create_app.py

Flask application factory.

To run:
  python3 app.py                   (direct)
  flask --app app run              (Flask CLI)
"""
from __future__ import annotations

import logging
from pathlib import Path
from flask import Flask

from config import ActiveConfig

log = logging.getLogger(__name__)


def create_app(config_class=None) -> Flask:
    """
    Application factory.

    Parameters
    ----------
    config_class : optional config class override (e.g. for testing)
    """
    if config_class is None:
        config_class = ActiveConfig

    # Resolve absolute paths for template/static folders so Flask finds them
    # regardless of the working directory the process is started from.
    _root = Path(__file__).resolve().parent

    app = Flask(
        __name__,
        template_folder=str(_root / "frontend" / "templates"),
        static_folder=str(_root / "frontend" / "static"),
    )

    # ------------------------------------------------------------------ #
    # Config                                                               #
    # ------------------------------------------------------------------ #
    app.config["SECRET_KEY"] = config_class.SECRET_KEY
    app.config["DATABASE_PATH"] = config_class.DATABASE_PATH
    app.config["VRTI_SPARQL_ENDPOINT"] = config_class.VRTI_SPARQL_ENDPOINT
    app.config["CENSUS_STALE_AFTER_DAYS"] = config_class.CENSUS_STALE_AFTER_DAYS
    app.config["EXPORTS_DIR"] = config_class.EXPORTS_DIR

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    from extensions import init_db, ensure_schema
    init_db(config_class.DATABASE_PATH)
    ensure_schema()

    # ------------------------------------------------------------------ #
    # Blueprints                                                           #
    # Each URL prefix is registered here — not in the route files.        #
    # ------------------------------------------------------------------ #
    from backend.routes.main import bp as main_bp
    from backend.routes.census import bp as census_bp
    from backend.routes.unified import bp as unified_bp
    from backend.routes.map_config import bp as map_bp
    from backend.routes.townlands import bp as townlands_bp
    from backend.routes.exports import bp as exports_bp
    from backend.routes.ask import bp as ask_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(census_bp,    url_prefix="/api/census")
    app.register_blueprint(unified_bp,   url_prefix="/api/unified")
    app.register_blueprint(map_bp,       url_prefix="/api/map")
    app.register_blueprint(townlands_bp, url_prefix="/api/townlands")
    app.register_blueprint(exports_bp,   url_prefix="/api/exports")
    app.register_blueprint(ask_bp,       url_prefix="/api/ask")

    # ------------------------------------------------------------------ #
    # Legacy compatibility routes                                          #
    # ------------------------------------------------------------------ #
    _register_legacy_routes(app)

    log.info("create_app.ready | blueprints registered")
    return app


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
