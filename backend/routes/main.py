from __future__ import annotations
import logging
from flask import Blueprint, render_template, request

log = logging.getLogger(__name__)
bp = Blueprint("main", __name__)


@bp.get("/")
def home():
    return render_template("index.html", title="Coolattin Lineage")


@bp.get("/about")
def about():
    return render_template("about.html", title="About Coolattin Lives")


@bp.get("/analytics")
def analytics():
    from analytics.registry import discover_modules

    dataset_id = request.args.get("d", "")
    modules: dict = {}
    try:
        modules = discover_modules()
    except Exception as e:
        log.warning("analytics discover_modules failed: %s", e)

    module_list = list(modules.values())
    current = modules.get(dataset_id) if dataset_id else None
    if current is None and module_list:
        current = module_list[0]

    result = None
    error = None
    if current:
        try:
            result = current.compute()
        except Exception as e:
            log.exception("analytics compute failed for %s", current.dataset_id)
            error = str(e)

    datasets = [(m.dataset_id, m.dataset_name) for m in module_list]
    current_dataset_id = current.dataset_id if current else None

    return render_template(
        "analytics.html",
        title="Analytics",
        datasets=datasets,
        current_dataset_id=current_dataset_id,
        result=result,
        error=error,
    )


@bp.get("/census")
def census():
    return render_template("census.html", title="Census Explorer")


@bp.get("/info")
def info():
    return render_template("info.html", title="Coolattin Estate & the Famine Clearances")


@bp.get("/ask")
def ask():
    return render_template("ask.html", title="Ask the Archive")


@bp.get("/heritage")
def heritage():
    return render_template("heritage.html", title="Historic Landscape · Coolattin")


@bp.get("/kg-explore")
@bp.get("/explore-knowledge")
def explore_knowledge():
    return render_template("kg_explore.html", title="Explore Knowledge Graph · Coolattin")
