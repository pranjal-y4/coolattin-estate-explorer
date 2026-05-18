"""
coolattin/routes/main.py

Page routes — serve HTML templates.
No data fetching happens here; templates pull data via API calls.
"""
from flask import Blueprint, render_template

bp = Blueprint("main", __name__)


@bp.get("/")
def home():
    return render_template("index.html", title="Coolattin Lineage")


@bp.get("/about")
def about():
    return render_template("about.html", title="About Coolattin Lives")


@bp.get("/analytics")
def analytics():
    return render_template("analytics.html", title="Analytics")


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


@bp.get("/explore-knowledge")
def explore_knowledge():
    return render_template("kg_explore.html", title="Explore Knowledge Graph · Coolattin")
