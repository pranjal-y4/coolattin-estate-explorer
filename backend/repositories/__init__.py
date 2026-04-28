# coolattin/repositories/__init__.py
#
# Repository modules — the ONLY layer that reads and writes the local DB.
#
# Rules:
#   - Repositories know about the DB schema and SQL.
#   - Repositories do NOT know about SPARQL, HTTP, or business rules.
#   - Services call repositories; routes never call repositories directly.
