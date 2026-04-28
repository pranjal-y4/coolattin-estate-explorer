from flask import Blueprint, jsonify
from .datahub import DataHub
from .kg import FamilyGraph

bp = Blueprint("search", __name__)

hub = DataHub()
kg = FamilyGraph()
families = kg.build(hub.tenancies, hub.evictions, hub.emigrations)


@bp.route("/api/families")
def list_families():
    return jsonify(families)
