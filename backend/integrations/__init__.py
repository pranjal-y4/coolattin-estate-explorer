# coolattin/integrations/__init__.py
#
# External integration modules.
# Rules:
#   - Only modules in this package may construct SPARQL queries.
#   - Only modules in this package may call external HTTP endpoints.
#   - Services call integrations; routes never call integrations directly.
