from __future__ import annotations

import networkx as nx

from backend.services import ask_service, graphrag


def test_analyse_question_marks_same_parish_townlands_as_list():
    analysis = ask_service._analyse_question(
        "Which townlands are in the same parish as Ballard?",
        "BALLARD",
    )

    assert analysis["primary_intent"] == "geography"
    assert analysis["output_mode"] == "list"


def test_graphrag_uses_exact_townland_seed_without_vector_search(monkeypatch):
    graph = nx.MultiDiGraph()
    graph.add_node("townland:BALLARD", label="Townland", name="BALLARD", props={}, community="")
    graph.add_node("parish:CARNEW", label="CivilParish", name="CARNEW", props={}, community="")
    graph.add_edge("townland:BALLARD", "parish:CARNEW", rel_type="WITHIN")

    monkeypatch.setattr(graphrag, "_GRAPH", graph)
    monkeypatch.setattr(graphrag, "_node_ids", [])
    monkeypatch.setattr(graphrag, "_node_matrix", None)
    monkeypatch.setattr(graphrag, "vector_seed", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("vector_seed should not run")))

    result = graphrag.retrieve_subgraph(
        "Which townlands are in the same parish as Ballard?",
        intent="relational",
        entity_hints={"canonical_townland": "BALLARD"},
    )

    assert result.available is True
    assert [node["node_id"] for node in result.seed_nodes] == ["townland:BALLARD"]
    assert "exact_townland" in result.path_used


def test_graphrag_prioritises_place_hierarchy_and_same_parish_summary(monkeypatch):
    graph = nx.MultiDiGraph()
    graph.add_node("townland:BALLARD", label="Townland", name="BALLARD", props={}, community="")
    graph.add_node("townland:COOLBOY", label="Townland", name="COOLBOY", props={}, community="")
    graph.add_node("townland:CARNEW", label="Townland", name="CARNEW", props={}, community="")
    graph.add_node("parish:CARNEW", label="CivilParish", name="CARNEW", props={}, community="")
    graph.add_node("barony:SCARAWALSH", label="Barony", name="SCARAWALSH", props={}, community="")
    graph.add_node("county:WICKLOW", label="County", name="WICKLOW", props={}, community="")

    graph.add_edge("townland:BALLARD", "parish:CARNEW", rel_type="WITHIN")
    graph.add_edge("townland:COOLBOY", "parish:CARNEW", rel_type="WITHIN")
    graph.add_edge("townland:CARNEW", "parish:CARNEW", rel_type="WITHIN")
    graph.add_edge("parish:CARNEW", "barony:SCARAWALSH", rel_type="WITHIN")
    graph.add_edge("barony:SCARAWALSH", "county:WICKLOW", rel_type="WITHIN")

    # Add lots of noise so the pruning logic has to prefer the place structure.
    for i in range(260):
        person_id = f"person:{i}"
        event_id = f"event:{i}"
        graph.add_node(person_id, label="Person", name=f"Person {i}", props={"surname": "BYRNE"}, community="")
        graph.add_node(event_id, label="EmigrationEvent", name=f"Emigration {i}", props={}, community="")
        graph.add_edge(person_id, "townland:BALLARD", rel_type="LOCATED_IN")
        graph.add_edge(person_id, event_id, rel_type="HAS_EVENT")
        graph.add_edge(event_id, "townland:BALLARD", rel_type="OCCURRED_IN")

    monkeypatch.setattr(graphrag, "_GRAPH", graph)
    monkeypatch.setattr(graphrag, "_node_ids", [])
    monkeypatch.setattr(graphrag, "_node_matrix", None)
    monkeypatch.setattr(graphrag, "vector_seed", lambda *args, **kwargs: [])

    result = graphrag.retrieve_subgraph(
        "Which townlands are in the same parish as Ballard?",
        intent="relational",
        entity_hints={"canonical_townland": "BALLARD"},
        k_hops=2,
    )

    assert result.available is True
    assert "Townlands in the same parish: CARNEW, COOLBOY." in result.linearized
    assert "(Townland:BALLARD)-[WITHIN]->(CivilParish:CARNEW)" in result.linearized
    assert any(edge["rel_type"] == "WITHIN" for edge in result.subgraph_rels)
