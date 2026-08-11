"""
backend/routes/_auth.py

Shared admin-key guard for privileged API routes.

Extracted from census.py so every admin endpoint enforces the same rule:
without ADMIN_API_KEY configured the endpoint is disabled outright, so it can
never be reachable on a public deployment with no key set.
"""
from __future__ import annotations

import functools

from flask import jsonify, request


def require_admin_key(fn):
    """Reject requests that don't carry a valid ADMIN_API_KEY header or query param."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from config import ActiveConfig
        required = (ActiveConfig.ADMIN_API_KEY or "").strip()
        if not required:
            return jsonify({
                "error": "Admin operations are disabled — set ADMIN_API_KEY in the environment."
            }), 403
        provided = (
            request.headers.get("X-Admin-Key")
            or request.args.get("admin_key")
            or ""
        ).strip()
        if not provided or provided != required:
            return jsonify({"error": "Forbidden"}), 403
        return fn(*args, **kwargs)
    return wrapper
