from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend import config


def test_load_local_env_files_reads_env_local_without_overriding_process_env(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.delenv("GRAPHDB_SPARQL_ENDPOINT", raising=False)
    monkeypatch.delenv("GRAPHDB_ENABLED", raising=False)

    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "GRAPHDB_SPARQL_ENDPOINT=http://example.test:7200/repositories/coolattin",
                "GRAPHDB_ENABLED=false",
            ]
        ),
        encoding="utf-8",
    )

    config._load_local_env_files(tmp_path)

    assert os.environ["GRAPHDB_SPARQL_ENDPOINT"] == (
        "http://example.test:7200/repositories/coolattin"
    )
    assert os.environ["GRAPHDB_ENABLED"] == "false"

    monkeypatch.setenv(
        "GRAPHDB_SPARQL_ENDPOINT",
        "http://already-set.example/repositories/coolattin",
    )
    (tmp_path / ".env").write_text(
        "GRAPHDB_SPARQL_ENDPOINT=http://file-should-not-win/repositories/coolattin\n",
        encoding="utf-8",
    )

    config._load_local_env_files(tmp_path)

    assert os.environ["GRAPHDB_SPARQL_ENDPOINT"] == (
        "http://already-set.example/repositories/coolattin"
    )
