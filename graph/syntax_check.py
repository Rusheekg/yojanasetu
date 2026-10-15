"""Check that our FalkorDB instance supports every Cypher feature graph/queries.py relies on.

WHY: docs/graph_model.md (section 3.4) drafts the eligibility, document and
memory queries using features we have not tested on FalkorDB yet. Running one
tiny query per feature on a throwaway graph tells us, before we write the real
queries, which ones work and which need the documented fallback.

Run:  python graph/syntax_check.py
Only touches a scratch graph named `syntax_check`, which it deletes at the end.
All data here is made up and has nothing to do with real schemes.
"""

import os
import sys

from falkordb import FalkorDB

GRAPH_NAME = "syntax_check"

# Tiny made-up graph: one farmer citizen, three schemes, a small paperwork chain.
#   s_farm   FOR_OCCUPATION farmer         -> citizen should match
#   s_open   no FOR_OCCUPATION edge        -> open to all, should match
#   s_weave  FOR_OCCUPATION weaver         -> should NOT match
#   s_farm -NEEDS_DOC-> grp -SATISFIED_BY-> d_a, d_b ; d_a -NEEDS_DOC-> d_c
#   citizen HAS d_b
SETUP = """
CREATE (c:Citizen {citizen_id: 'c1', age: 40}),
       (farmer:Occupation {id: 'farmer', name: 'Farmer'}),
       (weaver:Occupation {id: 'weaver', name: 'Weaver'}),
       (itp:Status {id: 'itp', name: 'Test status'}),
       (s_farm:Scheme  {scheme_id: 's_farm',  name: 'Test farm scheme', name_te: 'test te'}),
       (s_open:Scheme  {scheme_id: 's_open',  name: 'Test open scheme'}),
       (s_weave:Scheme {scheme_id: 's_weave', name: 'Test weave scheme'}),
       (grp:DocumentGroup {doc_id: 'grp', name: 'Test group'}),
       (d_a:Document {doc_id: 'd_a', name: 'Test doc A'}),
       (d_b:Document {doc_id: 'd_b', name: 'Test doc B'}),
       (d_c:Document {doc_id: 'd_c', name: 'Test doc C'}),
       (c)-[:WORKS_AS]->(farmer),
       (c)-[:HAS]->(d_b),
       (s_farm)-[:FOR_OCCUPATION]->(farmer),
       (s_weave)-[:FOR_OCCUPATION]->(weaver),
       (s_open)-[:EXCLUDES]->(itp),
       (s_farm)-[:NEEDS_DOC]->(grp),
       (grp)-[:SATISFIED_BY]->(d_a),
       (grp)-[:SATISFIED_BY]->(d_b),
       (d_a)-[:NEEDS_DOC]->(d_c)
"""

# Each check: (feature, cypher, params, function(result_rows) -> bool).
CHECKS = [
    (
        "NOT pattern predicate in WHERE",
        "MATCH (s:Scheme) WHERE NOT (s)-[:FOR_OCCUPATION]->(:Occupation) "
        "RETURN s.scheme_id ORDER BY s.scheme_id",
        {},
        lambda rows: rows == [["s_open"]],
    ),
    (
        "Pattern predicate reusing bound c (ELIGIBLE_WHERE shape)",
        "MATCH (c:Citizen {citizen_id: 'c1'}) MATCH (s:Scheme) "
        "WHERE (NOT (s)-[:FOR_OCCUPATION]->(:Occupation) "
        "       OR (s)-[:FOR_OCCUPATION]->(:Occupation)<-[:WORKS_AS]-(c)) "
        "RETURN s.scheme_id ORDER BY s.scheme_id",
        {},
        lambda rows: rows == [["s_farm"], ["s_open"]],
    ),
    (
        "Pattern comprehension inside WITH",
        "MATCH (c:Citizen {citizen_id: 'c1'}) "
        "WITH c, [(c)-[:WORKS_AS]->(x) | x.id] AS has_occ RETURN has_occ",
        {},
        lambda rows: rows == [[["farmer"]]],
    ),
    (
        # A pattern inside a pattern comprehension's WHERE errors on FalkorDB
        # ("Type mismatch: expected Null but was Pointer"), so queries.py
        # filters precomputed id lists instead. This checks that fallback.
        "Filter pattern-comprehension result by an id list",
        "MATCH (c:Citizen {citizen_id: 'c1'}), (s:Scheme {scheme_id: 's_open'}) "
        "WITH s, [(c)-[:HAS_STATUS]->(y) | y.id] AS has_status "
        "WITH [(s)-[:EXCLUDES]->(x:Status) | {id: x.id, name: x.name}] AS excl, has_status "
        "RETURN [x IN excl WHERE NOT x.id IN has_status | x.id]",
        {},
        lambda rows: rows == [[["itp"]]],
    ),
    (
        "Pattern comprehension through bound c (excluded_by shape)",
        "MATCH (c:Citizen {citizen_id: 'c1'}), (s:Scheme {scheme_id: 's_farm'}) "
        "RETURN [(s)-[:FOR_OCCUPATION]->(x)<-[:WORKS_AS]-(c) | x.id]",
        {},
        lambda rows: rows == [[["farmer"]]],
    ),
    (
        "Pattern comprehension inside all(...)",
        "MATCH (d:Document {doc_id: 'd_a'}) "
        "RETURN all(pre IN [(d)-[:NEEDS_DOC]->(x) | x.doc_id] WHERE pre IN ['d_c'])",
        {},
        lambda rows: rows == [[True]],
    ),
    (
        # Exact-length hops (*3 or *3..3) silently return no rows on FalkorDB,
        # so we always use a range and filter on length(p) when needed.
        "List slice nodes(p)[1..-1] on a ranged path + length(p) filter",
        "MATCH p = (:Scheme {scheme_id: 's_farm'})-[:NEEDS_DOC|SATISFIED_BY*1..8]->(:Document {doc_id: 'd_c'}) "
        "WHERE length(p) = 3 "
        "RETURN [n IN nodes(p)[1..-1] | n.doc_id]",
        {},
        lambda rows: rows == [[["grp", "d_a"]]],
    ),
    (
        "labels(x)[0]",
        "MATCH (x:Status {id: 'itp'}) RETURN labels(x)[0]",
        {},
        lambda rows: rows == [["Status"]],
    ),
    (
        "Map literal inside CASE",
        "MATCH (c:Citizen {citizen_id: 'c1'}) "
        "RETURN CASE WHEN c.age < 60 THEN {check: 'min_age', required: 60, actual: c.age} END",
        {},
        lambda rows: rows == [[{"check": "min_age", "required": 60, "actual": 40}]],
    ),
    (
        "List of CASE maps filtered by IS NOT NULL",
        "RETURN [r IN [CASE WHEN true THEN {k: 1} END, CASE WHEN false THEN {k: 2} END] "
        "WHERE r IS NOT NULL]",
        {},
        lambda rows: rows == [[[{"k": 1}]]],
    ),
    (
        "Variable-length path over two types",
        "MATCH (:Scheme {scheme_id: 's_farm'})-[:NEEDS_DOC|SATISFIED_BY*1..8]->(d) "
        "RETURN DISTINCT d.doc_id ORDER BY d.doc_id",
        {},
        lambda rows: rows == [["d_a"], ["d_b"], ["d_c"], ["grp"]],
    ),
    (
        "max(length(p)) grouped per node",
        "MATCH p = (:Scheme {scheme_id: 's_farm'})-[:NEEDS_DOC|SATISFIED_BY*1..8]->(d) "
        "RETURN d.doc_id, max(length(p)) ORDER BY d.doc_id",
        {},
        lambda rows: rows == [["d_a", 2], ["d_b", 2], ["d_c", 3], ["grp", 1]],
    ),
    (
        # `owned + collect(...)` in one projection errors on FalkorDB, so the
        # DONE_DOCS fragment aggregates first and concatenates in the next WITH.
        "List concatenation after aggregation (DONE_DOCS shape)",
        "MATCH (c:Citizen {citizen_id: 'c1'}) "
        "OPTIONAL MATCH (c)-[:HAS]->(h:Document) WITH c, collect(h.doc_id) AS owned "
        "OPTIONAL MATCH (g:DocumentGroup)-[:SATISFIED_BY]->(:Document)<-[:HAS]-(c) "
        "WITH c, owned, collect(DISTINCT g.doc_id) AS groups_done "
        "WITH c, owned + groups_done AS done RETURN done",
        {},
        lambda rows: rows == [[["d_b", "grp"]]],
    ),
    (
        "ORDER BY ... LIMIT 1 then OPTIONAL MATCH (recall shape)",
        "MATCH (s:Scheme) WITH s ORDER BY s.scheme_id DESC LIMIT 1 "
        "OPTIONAL MATCH (s)-[:FOR_OCCUPATION]->(o) RETURN s.scheme_id, o.id",
        {},
        lambda rows: rows == [["s_weave", "weaver"]],
    ),
    (
        "SET c += $props",
        "MATCH (c:Citizen {citizen_id: 'c1'}) SET c += $props RETURN c.age, c.land_acres",
        {"props": {"age": 41, "land_acres": 2.5}},
        lambda rows: rows == [[41, 2.5]],
    ),
    (
        "timestamp()",
        "RETURN timestamp() > 0",
        {},
        lambda rows: rows == [[True]],
    ),
    (
        "String concatenation inside a MERGE key",
        "MATCH (c:Citizen {citizen_id: $cid}) "
        "MERGE (c)-[:REMEMBERS]->(f:MemoryFact {fact_id: $cid + ':' + $key}) "
        "ON CREATE SET f.key = $key, f.created_at = timestamp() "
        "SET f.value = $value RETURN f.fact_id, f.value",
        {"cid": "c1", "key": "land", "value": "2.5"},
        lambda rows: rows == [["c1:land", "2.5"]],
    ),
    (
        "MERGE upsert does not duplicate",
        "MATCH (c:Citizen {citizen_id: $cid}) "
        "MERGE (c)-[:REMEMBERS]->(f:MemoryFact {fact_id: $cid + ':' + $key}) "
        "SET f.value = $value WITH c MATCH (c)-[:REMEMBERS]->(f) RETURN count(f)",
        {"cid": "c1", "key": "land", "value": "3"},
        lambda rows: rows == [[1]],
    ),
    (
        "UNWIND $rows with map access",
        "UNWIND $rows AS row MATCH (s:Scheme {scheme_id: row.scheme_id}) RETURN s.name",
        {"rows": [{"scheme_id": "s_open"}]},
        lambda rows: rows == [["Test open scheme"]],
    ),
    (
        "CREATE INDEX FOR (n:L) ON (n.p)",
        "CREATE INDEX FOR (n:Scheme) ON (n.scheme_id)",
        {},
        lambda rows: True,
    ),
    (
        "Full-text index on two properties + query",
        None,  # handled specially in run_fulltext_check
        {},
        None,
    ),
]


def connect():
    """Build a client from environment variables only.

    WHY: AGENTS.md forbids hardcoded hosts or passwords; failing early with a
    clear message is friendlier than a socket error later.
    """
    missing = [v for v in ("FALKORDB_HOST", "FALKORDB_PORT") if not os.environ.get(v)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))
    return FalkorDB(
        host=os.environ["FALKORDB_HOST"],
        port=int(os.environ["FALKORDB_PORT"]),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
    )


def run_check(graph, cypher, params, expect):
    """Run one query; PASS only if it neither errors nor returns a wrong result.

    WHY: some engines accept syntax but compute something different, so we
    check the answer as well as the absence of an error.
    """
    try:
        rows = graph.query(cypher, params).result_set
    except Exception as exc:  # noqa: BLE001 - we want to report any failure
        return False, "error: {}".format(exc)
    if expect(rows):
        return True, ""
    return False, "unexpected result: {!r}".format(rows)


def run_fulltext_check(graph):
    """Create a two-property full-text index and search the second property.

    WHY: the agent needs to find schemes by English or Telugu name, so the
    index must cover both `name` and `name_te`.
    """
    try:
        graph.query("CALL db.idx.fulltext.createNodeIndex('Scheme', 'name', 'name_te')")
        rows = graph.query(
            "CALL db.idx.fulltext.queryNodes('Scheme', 'te') YIELD node RETURN node.scheme_id"
        ).result_set
    except Exception as exc:  # noqa: BLE001
        return False, "error: {}".format(exc)
    if rows == [["s_farm"]]:
        return True, ""
    return False, "unexpected result: {!r}".format(rows)


def run_unique_constraint_check(graph):
    """Create a unique constraint the way the loader will.

    WHY: section 4 of graph_model.md relies on unique constraints to stop
    duplicate scheme ids; the client creates the needed range index first.
    """
    try:
        graph.create_node_unique_constraint("Document", "doc_id")
    except Exception as exc:  # noqa: BLE001
        return False, "error: {}".format(exc)
    return True, ""


def main():
    """Create the scratch graph, run every check, always delete the graph."""
    db = connect()
    if GRAPH_NAME in db.list_graphs():
        sys.exit("Graph '{}' already exists; refusing to overwrite it.".format(GRAPH_NAME))

    graph = db.select_graph(GRAPH_NAME)
    failures = 0
    try:
        graph.query(SETUP)
        results = []
        for feature, cypher, params, expect in CHECKS:
            if cypher is None:
                results.append((feature,) + run_fulltext_check(graph))
            else:
                results.append((feature,) + run_check(graph, cypher, params, expect))
        results.append(("Unique constraint (client helper)",) + run_unique_constraint_check(graph))

        for feature, ok, detail in results:
            print("{}  {}{}".format("PASS" if ok else "FAIL", feature,
                                    "  -> " + detail if detail else ""))
            failures += 0 if ok else 1
    finally:
        graph.delete()
        print("Deleted scratch graph '{}'.".format(GRAPH_NAME))

    print("\n{} of {} checks failed.".format(failures, len(CHECKS) + 1))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
