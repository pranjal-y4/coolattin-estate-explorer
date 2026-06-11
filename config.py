"""
config.py

Central configuration for all environments.
All tunable values live here — never scattered in modules.
"""
from __future__ import annotations
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent        # Coolattin-app/ (project root)


def _load_local_env_files(root: Path = BASE_DIR) -> None:
    """
    Load local key=value env files before config values are resolved.

    Existing process env wins over file values so Azure/App Service settings
    still take precedence over local development files.
    """
    for env_path in (root / ".env.local", root / ".env"):
        if not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_local_env_files()


def _resolve_database_path() -> Path:
    raw = os.environ.get("DATABASE_PATH", "").strip()
    if not raw:
        return BASE_DIR / "coolattin.db"
    return Path(raw).expanduser()


class Config:
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")

    # ------------------------------------------------------------------ #
    # Database — SQLite lives at project root                             #
    # Override DATABASE_URL env var for production (PostgreSQL etc.)      #
    # ------------------------------------------------------------------ #
    DATABASE_PATH: Path = _resolve_database_path()
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{DATABASE_PATH}"
    )

    # ------------------------------------------------------------------ #
    # VRTI Knowledge Graph                                                 #
    # ------------------------------------------------------------------ #
    VRTI_SPARQL_ENDPOINT: str = "https://virtuoso.virtualtreasury.ie/sparql/"
    VRTI_REQUEST_TIMEOUT: int = int(os.environ.get("VRTI_REQUEST_TIMEOUT", "30"))

    # ------------------------------------------------------------------ #
    # Local GraphDB (RDF/KG comparative prototype — D8)                   #
    # ------------------------------------------------------------------ #
    GRAPHDB_SPARQL_ENDPOINT: str = os.environ.get(
        "GRAPHDB_SPARQL_ENDPOINT",
        "http://localhost:7200/repositories/coolattin",
    )
    GRAPHDB_ENABLED: bool = os.environ.get("GRAPHDB_ENABLED", "true").lower() == "true"
    GRAPHDB_REQUEST_TIMEOUT: int = int(os.environ.get("GRAPHDB_REQUEST_TIMEOUT", "15"))

    # ------------------------------------------------------------------ #
    # In-process GraphRAG (property graph — no external server)           #
    # Build the graph: python3 scripts/build_graph.py                     #
    # ------------------------------------------------------------------ #
    GRAPHRAG_ENABLED: bool = os.environ.get("GRAPHRAG_ENABLED", "true").lower() == "true"
    GRAPHRAG_VECTOR_TOP_K: int = int(os.environ.get("GRAPHRAG_VECTOR_TOP_K", "8"))
    GRAPHRAG_K_HOPS: int = int(os.environ.get("GRAPHRAG_K_HOPS", "2"))
    GRAPHRAG_MAX_NODES: int = int(os.environ.get("GRAPHRAG_MAX_NODES", "120"))

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
    # Embedding provider                                                   #
    # Values: local | cohere | voyage  (local = BAAI/bge-large-en-v1.5)  #
    # ------------------------------------------------------------------ #
    EMBEDDING_PROVIDER: str = os.environ.get("EMBEDDING_PROVIDER", "local")

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
