"""
config.py

Central configuration for all environments.
All tunable values live here — never scattered in modules.
"""
from __future__ import annotations
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent        # Coolattin-app/ (project root)


class Config:
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

    # ------------------------------------------------------------------ #
    # Database — SQLite lives at project root                             #
    # Override DATABASE_URL env var for production (PostgreSQL etc.)      #
    # ------------------------------------------------------------------ #
    DATABASE_PATH: Path = BASE_DIR / "coolattin.db"
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'coolattin.db'}"
    )

    # ------------------------------------------------------------------ #
    # VRTI Knowledge Graph                                                 #
    # ------------------------------------------------------------------ #
    VRTI_SPARQL_ENDPOINT: str = "https://virtuoso.virtualtreasury.ie/sparql/"
    VRTI_REQUEST_TIMEOUT: int = int(os.environ.get("VRTI_REQUEST_TIMEOUT", "30"))

    # ------------------------------------------------------------------ #
    # Refresh / staleness TTL (days)                                      #
    # ------------------------------------------------------------------ #
    CENSUS_STALE_AFTER_DAYS: int = 7
    TOWNLAND_STALE_AFTER_DAYS: int = 30

    # ------------------------------------------------------------------ #
    # Paths                                                                #
    # ------------------------------------------------------------------ #
    STATIC_DATA_DIR: Path = BASE_DIR / "frontend" / "static" / "data"

    DATA_SEED_DIR: Path = BASE_DIR / "data" / "seed"
    DATA_SNAPSHOT_DIR: Path = BASE_DIR / "data" / "source_snapshots"
    EXPORTS_DIR: Path = BASE_DIR / "exports"

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(Config):
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"


class ProductionConfig(Config):
    DEBUG: bool = False
    CENSUS_STALE_AFTER_DAYS: int = 1
    TOWNLAND_STALE_AFTER_DAYS: int = 7


# Active config — override with FLASK_ENV=production
config_by_name: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}

ActiveConfig = config_by_name.get(
    os.environ.get("FLASK_ENV", "development"), DevelopmentConfig
)
