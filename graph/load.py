r"""Load data/*.csv and fixtures/*.csv into the FalkorDB graph `yojanasetu`.

Run from the repo root:
    .venv\Scripts\python -m graph.load            # validate + show what --reset would delete
    .venv\Scripts\python -m graph.load --reset    # validate, wipe the graph, reload

WHY: the graph is rebuilt from the CSVs every time, so the CSVs stay the single
source of truth and a reload is always safe to repeat. Every rule in
docs/graph_model.md 2.3 must pass before anything in the database changes.
A reset also erases sessions and memories, so it needs an explicit flag.
"""

import argparse
import sys

from graph.db import GRAPH_NAME, connect
from graph.validate import (
    CITIZEN_LINKS, CITIZEN_NUMBERS, EDGE_CRITERIA, LOOKUP_LABELS, NUMERIC_CRITERIA,
    parse_number, read_all, validate,
)

# Section 4 of docs/graph_model.md.
RANGE_INDEXES = [
    ("Scheme", "scheme_id"), ("Scheme", "level"),
    ("Occupation", "id"), ("Category", "id"), ("Gender", "id"), ("State", "id"),
    ("Status", "id"), ("Office", "id"),
    ("Document", "doc_id"), ("DocumentGroup", "doc_id"), ("Source", "url"),
    ("Citizen", "citizen_id"), ("Session", "session_id"), ("MemoryFact", "fact_id"),
]
UNIQUE_KEYS = [pair for pair in RANGE_INDEXES if pair != ("Scheme", "level")]
FULLTEXT_INDEXES = [("Scheme", "name", "name_te"), ("Document", "name", "name_te")]

DOC_LABELS = {"document": "Document", "group": "DocumentGroup"}


def compact(props):
    """Drop empty values so missing data stays null in the graph.

    WHY: an empty benefit_amount_inr (free bus travel) or an unknown age must
    be absent, never stored as '' or 0, or the eligibility rules would misread it.
    """
    return {key: value for key, value in props.items() if value not in ("", None)}


# --------------------------------------------------------------------------
# Building rows (pure Python) from the validated Dataset.
# --------------------------------------------------------------------------

def lookup_rows(ds):
    """Group lookup rows by node label."""
    by_label = {label: [] for label in LOOKUP_LABELS.values()}
    for row in ds.lookups:
        by_label[LOOKUP_LABELS[row.get("type")]].append(compact({
            "id": row.get("id"), "name": row.get("name"),
            "name_te": row.get("name_te"), "website": row.get("website"),
        }))
    return by_label


def scheme_rows(ds):
    """Scheme properties, with numeric limits and income_basis folded in from criteria.

    WHY: rules live in scheme_criteria.csv (one place for humans), but the
    queries read limits as Scheme properties (one lookup per scheme).
    """
    extra = {}
    for row in ds.criteria:
        criterion = row.get("criterion")
        if criterion in NUMERIC_CRITERIA:
            extra.setdefault(row.get("scheme_id"), {})[criterion] = parse_number(
                row.get("value"), NUMERIC_CRITERIA[criterion])
        elif criterion == "income_basis":
            extra.setdefault(row.get("scheme_id"), {})["income_basis"] = row.get("value")
    rows = []
    for row in ds.schemes:
        props = {
            "scheme_id": row.get("scheme_id"), "name": row.get("name"), "name_te": row.get("name_te"),
            "level": row.get("level"), "benefit": row.get("benefit"),
            "benefit_amount_inr": parse_number(row.get("benefit_amount_inr"), int),
            "source_url": row.get("source_url"), "last_verified": row.get("last_verified"),
        }
        props.update(extra.get(row.get("scheme_id"), {}))
        rows.append(compact(props))
    return rows


def criteria_edges(ds):
    """Edge criteria grouped by (relationship type, target label) -> [(scheme_id, lookup id)]."""
    edges = {}
    for row in ds.criteria:
        if row.get("criterion") in EDGE_CRITERIA:
            kind, rel = EDGE_CRITERIA[row.get("criterion")]
            edges.setdefault((rel, LOOKUP_LABELS[kind]), []).append((row.get("scheme_id"), row.get("value")))
    return edges


def document_rows(ds):
    """Document and DocumentGroup properties, grouped by label."""
    by_label = {label: [] for label in DOC_LABELS.values()}
    for row in ds.documents:
        by_label[DOC_LABELS[row.get("kind")]].append(compact({
            "doc_id": row.get("doc_id"), "name": row.get("name"),
            "name_te": row.get("name_te"), "description": row.get("description"),
        }))
    return by_label


def document_edges(ds):
    """document_links rows grouped by (rel, from label, from key, to label, to key)."""
    kinds = {r.get("doc_id"): DOC_LABELS[r.get("kind")] for r in ds.documents}
    edges = {}
    for row in ds.doc_links:
        src, dst, link = row.get("from_id"), row.get("to_id"), row.get("link")
        if link == "issued_by":
            spec = ("ISSUED_BY", "Document", "doc_id", "Office", "id")
        elif link == "satisfied_by":
            spec = ("SATISFIED_BY", "DocumentGroup", "doc_id", "Document", "doc_id")
        elif src in kinds:
            spec = ("NEEDS_DOC", kinds[src], "doc_id", kinds[dst], "doc_id")
        else:
            spec = ("NEEDS_DOC", "Scheme", "scheme_id", kinds[dst], "doc_id")
        edges.setdefault(spec, []).append((src, dst))
    return edges


def citizen_rows(ds):
    """Citizen properties; empty numbers stay null (= unknown)."""
    rows = []
    for row in ds.citizens:
        props = {"citizen_id": row.get("citizen_id"), "display_name": row.get("display_name")}
        for column, kind in CITIZEN_NUMBERS.items():
            props[column] = parse_number(row.get(column), kind)
        rows.append(compact(props))
    return rows


def citizen_edges(ds):
    """Citizen links (plus gender/state columns) grouped like document_edges."""
    edges = {}
    for row in ds.citizens:
        cid = row.get("citizen_id")
        if row.get("gender_id"):
            edges.setdefault(("IS", "Gender", "id"), []).append((cid, row.get("gender_id")))
        if row.get("state_id"):
            edges.setdefault(("LIVES_IN", "State", "id"), []).append((cid, row.get("state_id")))
    for row in ds.citizen_links:
        kind, rel = CITIZEN_LINKS[row.get("link")]
        spec = (rel, "Document", "doc_id") if kind is None else (rel, LOOKUP_LABELS[kind], "id")
        edges.setdefault(spec, []).append((row.get("citizen_id"), row.get("value")))
    return {(rel, "Citizen", "citizen_id", label, key): pairs for (rel, label, key), pairs in edges.items()}


# --------------------------------------------------------------------------
# Writing to FalkorDB.
# --------------------------------------------------------------------------

def create_nodes(graph, label, rows):
    """Create all nodes of one label in a single UNWIND query.

    WHY: labels can't be query parameters, so `label` always comes from the
    fixed lists above, never from CSV text.
    """
    if rows:
        graph.query("UNWIND $rows AS r CREATE (n:{}) SET n += r".format(label), {"rows": rows})


def create_edges(graph, spec, pairs):
    """Create all relationships of one shape; fail if any endpoint was not found.

    WHY: MATCH silently skips missing nodes. Validation should make that
    impossible, but checking the count proves no relationship was dropped.
    """
    rel, from_label, from_key, to_label, to_key = spec
    cypher = (
        "UNWIND $pairs AS p "
        "MATCH (a:{fl} {{{fk}: p[0]}}) MATCH (b:{tl} {{{tk}: p[1]}}) "
        "CREATE (a)-[:{rel}]->(b) RETURN count(*)"
    ).format(fl=from_label, fk=from_key, tl=to_label, tk=to_key, rel=rel)
    created = graph.query(cypher, {"pairs": [list(p) for p in pairs]}).result_set[0][0]
    if created != len(pairs):
        raise RuntimeError("{}: expected {} relationships, created {}".format(spec, len(pairs), created))


def create_sources(graph, schemes):
    """One Source node per URL (schemes may share a URL), linked by SOURCED_FROM."""
    rows = [{"scheme_id": s["scheme_id"], "url": s["source_url"], "last_verified": s.get("last_verified")}
            for s in schemes]
    graph.query(
        "UNWIND $rows AS r MATCH (s:Scheme {scheme_id: r.scheme_id}) "
        "MERGE (src:Source {url: r.url}) ON CREATE SET src.last_verified = r.last_verified "
        "CREATE (s)-[:SOURCED_FROM]->(src)",
        {"rows": rows},
    )


def create_indexes(graph):
    """Range and full-text indexes from section 4, created before loading so
    the MATCH lookups during the load are fast."""
    for label, prop in RANGE_INDEXES:
        graph.query("CREATE INDEX FOR (n:{}) ON (n.{})".format(label, prop))
    for label, *props in FULLTEXT_INDEXES:
        args = ", ".join("'{}'".format(x) for x in [label] + props)
        graph.query("CALL db.idx.fulltext.createNodeIndex({})".format(args))


def create_constraints(graph):
    """Unique constraints on every id, added after loading (the range index already exists)."""
    for label, prop in UNIQUE_KEYS:
        graph.create_node_unique_constraint(label, prop)


def load(graph, ds):
    """Write every node, then every relationship, from the validated Dataset."""
    for label, rows in lookup_rows(ds).items():
        create_nodes(graph, label, rows)
    schemes = scheme_rows(ds)
    create_nodes(graph, "Scheme", schemes)
    for label, rows in document_rows(ds).items():
        create_nodes(graph, label, rows)
    create_nodes(graph, "Citizen", citizen_rows(ds))
    graph.query("MATCH (c:Citizen) SET c.created_at = timestamp()")

    create_sources(graph, schemes)
    for (rel, to_label), pairs in criteria_edges(ds).items():
        create_edges(graph, (rel, "Scheme", "scheme_id", to_label, "id"), pairs)
    for spec, pairs in list(document_edges(ds).items()) + list(citizen_edges(ds).items()):
        create_edges(graph, spec, pairs)


# --------------------------------------------------------------------------
# Reporting and the command line.
# --------------------------------------------------------------------------

def count_by_label(graph):
    """[(label, count)] read back from the graph, not counted in Python."""
    return graph.query("MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY label").result_set


def count_by_type(graph):
    """[(relationship type, count)] read back from the graph."""
    return graph.query("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) ORDER BY rel").result_set


def print_table(title, rows):
    """Print a small aligned table with a total line."""
    print(title)
    for name, count in rows:
        print("  {:<16} {:>5}".format(name, count))
    print("  {:<16} {:>5}".format("total", sum(count for _, count in rows)))


def preview(db):
    """Dry run: say what --reset would delete, change nothing."""
    if GRAPH_NAME not in db.list_graphs():
        print("Graph '{}' does not exist yet; --reset would delete nothing.".format(GRAPH_NAME))
    else:
        graph = db.select_graph(GRAPH_NAME)
        print_table("--reset would DELETE everything in graph '{}':".format(GRAPH_NAME), count_by_label(graph))
        print("This includes all Session and MemoryFact nodes (citizens' memories).")
    print("Nothing was changed. Run again with --reset to reload.")


def reset_and_load(db, ds):
    """Delete the graph, recreate indexes, load, add constraints, report counts."""
    if GRAPH_NAME in db.list_graphs():
        db.select_graph(GRAPH_NAME).delete()
    graph = db.select_graph(GRAPH_NAME)
    create_indexes(graph)
    load(graph, ds)
    create_constraints(graph)
    print("Graph '{}' was reset: all previous sessions and memories were ERASED.".format(GRAPH_NAME))
    print_table("\nNodes created:", count_by_label(graph))
    print_table("\nRelationships created:", count_by_type(graph))


def main():
    """Validate first; only then preview or reset."""
    parser = argparse.ArgumentParser(description="Load the YojanaSetu CSVs into FalkorDB.")
    parser.add_argument("--reset", action="store_true",
                        help="delete the graph (including sessions and memories) and reload it")
    args = parser.parse_args()

    ds = read_all()
    errors = validate(ds)
    if errors:
        print("Validation failed - nothing was changed:", file=sys.stderr)
        for error in errors:
            print("  " + error, file=sys.stderr)
        sys.exit(1)
    print("Validation passed.")

    db = connect()
    if args.reset:
        reset_and_load(db, ds)
    else:
        preview(db)


if __name__ == "__main__":
    main()
