# coolattin/routes/__init__.py
#
# Route blueprints.
#
# Rules:
#   - Routes validate HTTP parameters only.
#   - Routes delegate all logic to services.
#   - Routes never call repositories, integrations, or construct SPARQL.
#   - Routes return consistent JSON envelopes (data + meta).
