"""Read the scheme CSVs and demo fixtures, and check every rule in docs/graph_model.md 2.3.

WHY: eligibility is only as good as the data behind it. The loader refuses to
touch the graph unless every rule here passes, so a typo in a CSV can never
produce a wrong answer for a citizen. This module needs no database, so it can
be tested and shown to the judges on its own.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FIXTURES_DIR = ROOT / "fixtures"

# Lookup `type` -> node label.
LOOKUP_LABELS = {
    "occupation": "Occupation",
    "category": "Category",
    "gender": "Gender",
    "state": "State",
    "status": "Status",
    "office": "Office",
}

# Numeric criteria become Scheme properties; the value is the Python type.
NUMERIC_CRITERIA = {"min_age": int, "max_age": int, "max_income": int, "max_land_acres": float}
INCOME_BASES = {"household", "individual"}

# Edge criteria: criterion -> (lookup type of the value, relationship type).
EDGE_CRITERIA = {
    "for_occupation": ("occupation", "FOR_OCCUPATION"),
    "for_category": ("category", "FOR_CATEGORY"),
    "for_gender": ("gender", "FOR_GENDER"),
    "for_status": ("status", "FOR_STATUS"),
    "available_in": ("state", "AVAILABLE_IN"),
    "excludes_occupation": ("occupation", "EXCLUDES"),
    "excludes_category": ("category", "EXCLUDES"),
    "excludes_status": ("status", "EXCLUDES"),
}
ALL_CRITERIA = set(NUMERIC_CRITERIA) | set(EDGE_CRITERIA) | {"income_basis"}

LEVELS = {"central", "state"}
DOC_KINDS = {"document", "group"}
DOC_LINKS = {"needs_doc", "satisfied_by", "issued_by"}

# Citizen link -> (lookup type of the value, relationship type); has_doc points at a document.
CITIZEN_LINKS = {
    "works_as": ("occupation", "WORKS_AS"),
    "belongs_to": ("category", "BELONGS_TO"),
    "has_status": ("status", "HAS_STATUS"),
    "has_doc": (None, "HAS"),
}
CITIZEN_NUMBERS = {"age": int, "annual_income": int, "land_acres": float}


@dataclass
class Row:
    """One CSV row plus where it came from, so errors can point at file:line."""

    file: str
    line: int
    values: dict

    def get(self, column):
        """Return the stripped cell value ('' when empty or missing)."""
        return (self.values.get(column) or "").strip()

    def where(self):
        """Location string used in every error message."""
        return "{}:{}".format(self.file, self.line)


@dataclass
class Dataset:
    """All CSV rows the loader needs, read once."""

    lookups: list = field(default_factory=list)
    schemes: list = field(default_factory=list)
    criteria: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    doc_links: list = field(default_factory=list)
    citizens: list = field(default_factory=list)
    citizen_links: list = field(default_factory=list)


def read_csv(path):
    """Read a CSV into Rows; utf-8-sig drops the BOM Excel sometimes adds.

    WHY: line numbers start at 2 (line 1 is the header) so error messages
    match what a person sees when they open the file.
    """
    rel = path.relative_to(ROOT).as_posix()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [Row(rel, n, values) for n, values in enumerate(csv.DictReader(handle), start=2)]


def read_all(data_dir=DATA_DIR, fixtures_dir=FIXTURES_DIR):
    """Read the 5 data CSVs and the 2 fixture CSVs into one Dataset."""
    return Dataset(
        lookups=read_csv(data_dir / "lookups.csv"),
        schemes=read_csv(data_dir / "schemes.csv"),
        criteria=read_csv(data_dir / "scheme_criteria.csv"),
        documents=read_csv(data_dir / "documents.csv"),
        doc_links=read_csv(data_dir / "document_links.csv"),
        citizens=read_csv(fixtures_dir / "citizens.csv"),
        citizen_links=read_csv(fixtures_dir / "citizen_links.csv"),
    )


def parse_number(text, kind):
    """Convert a cell to int/float; None when empty; raises ValueError when not a number.

    WHY: an empty cell means "no limit" or "unknown", which must stay null in
    the graph, never become 0.
    """
    if text == "":
        return None
    return kind(text)


def is_number(text, kind):
    """True when the cell is empty or parses as `kind` (int cells must be whole numbers)."""
    try:
        parse_number(text, kind)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Individual rule groups. Each appends human-readable messages to `errors`.
# --------------------------------------------------------------------------

def check_required(rows, columns, errors):
    """Every listed column must be non-empty."""
    for row in rows:
        for column in columns:
            if not row.get(column):
                errors.append("{} - column '{}' is empty".format(row.where(), column))


def check_unique(rows, key_fn, label, errors):
    """No two rows may share the same key (e.g. two schemes with one scheme_id)."""
    seen = {}
    for row in rows:
        key = key_fn(row)
        if key in seen:
            errors.append("{} - duplicate {} {!r} (first at {})".format(row.where(), label, key, seen[key]))
        else:
            seen[key] = row.where()


def lookup_ids(ds):
    """Map lookup type -> set of ids, for resolving references."""
    ids = {kind: set() for kind in LOOKUP_LABELS}
    for row in ds.lookups:
        if row.get("type") in ids:
            ids[row.get("type")].add(row.get("id"))
    return ids


def check_lookups(ds, errors):
    """Known `type`, required id/name, ids unique within their type."""
    check_required(ds.lookups, ["type", "id", "name"], errors)
    for row in ds.lookups:
        if row.get("type") and row.get("type") not in LOOKUP_LABELS:
            errors.append("{} - unknown lookup type {!r}".format(row.where(), row.get("type")))
    check_unique(ds.lookups, lambda r: (r.get("type"), r.get("id")), "lookup", errors)


def check_schemes(ds, errors):
    """Required columns (including source_url), valid level, numeric benefit or empty."""
    check_required(ds.schemes, ["scheme_id", "name", "level", "benefit", "source_url"], errors)
    check_unique(ds.schemes, lambda r: r.get("scheme_id"), "scheme_id", errors)
    for row in ds.schemes:
        if row.get("level") and row.get("level") not in LEVELS:
            errors.append("{} - level must be central or state, got {!r}".format(row.where(), row.get("level")))
        if not is_number(row.get("benefit_amount_inr"), int):
            errors.append("{} - benefit_amount_inr must be a whole number or empty".format(row.where()))


def check_criterion_row(row, scheme_ids, lookups, errors):
    """One scheme_criteria row: known scheme, known criterion, value of the right kind."""
    scheme, criterion, value = row.get("scheme_id"), row.get("criterion"), row.get("value")
    if scheme not in scheme_ids:
        errors.append("{} - unknown scheme_id {!r}".format(row.where(), scheme))
    if criterion not in ALL_CRITERIA:
        errors.append("{} - unknown criterion {!r}".format(row.where(), criterion))
    elif criterion in NUMERIC_CRITERIA:
        if value == "" or not is_number(value, NUMERIC_CRITERIA[criterion]):
            errors.append("{} - {} must be a number, got {!r}".format(row.where(), criterion, value))
    elif criterion == "income_basis":
        if value not in INCOME_BASES:
            errors.append("{} - income_basis must be household or individual, got {!r}".format(row.where(), value))
    else:
        kind = EDGE_CRITERIA[criterion][0]
        if value not in lookups[kind]:
            errors.append("{} - {} {!r} is not a {} in lookups.csv".format(row.where(), criterion, value, kind))


def check_criteria(ds, errors):
    """Every criteria row, plus: no numeric criterion twice per scheme, min_age <= max_age."""
    scheme_ids = {r.get("scheme_id") for r in ds.schemes}
    lookups = lookup_ids(ds)
    for row in ds.criteria:
        check_criterion_row(row, scheme_ids, lookups, errors)
    single = [r for r in ds.criteria if r.get("criterion") in NUMERIC_CRITERIA or r.get("criterion") == "income_basis"]
    check_unique(single, lambda r: (r.get("scheme_id"), r.get("criterion")), "criterion for scheme", errors)
    check_age_range(ds, errors)


def check_age_range(ds, errors):
    """A scheme's min_age must not be above its max_age."""
    ages = {}
    for row in ds.criteria:
        if row.get("criterion") in ("min_age", "max_age") and is_number(row.get("value"), int):
            ages.setdefault(row.get("scheme_id"), {})[row.get("criterion")] = int(row.get("value"))
    for scheme, pair in ages.items():
        if "min_age" in pair and "max_age" in pair and pair["min_age"] > pair["max_age"]:
            errors.append("data/scheme_criteria.csv - {}: min_age {} > max_age {}".format(
                scheme, pair["min_age"], pair["max_age"]))


def check_documents(ds, errors):
    """Required columns, known kind, unique doc_id, no doc_id that is also a scheme_id."""
    check_required(ds.documents, ["doc_id", "kind", "name"], errors)
    check_unique(ds.documents, lambda r: r.get("doc_id"), "doc_id", errors)
    scheme_ids = {r.get("scheme_id") for r in ds.schemes}
    for row in ds.documents:
        if row.get("kind") and row.get("kind") not in DOC_KINDS:
            errors.append("{} - kind must be document or group, got {!r}".format(row.where(), row.get("kind")))
        if row.get("doc_id") in scheme_ids:
            errors.append("{} - doc_id {!r} is also a scheme_id; needs_doc rows would be ambiguous".format(
                row.where(), row.get("doc_id")))


def check_doc_link_row(row, kinds, scheme_ids, offices, errors):
    """One document_links row: the from/to ids must exist and have the right kind."""
    link, src, dst = row.get("link"), row.get("from_id"), row.get("to_id")
    where = row.where()
    if link not in DOC_LINKS:
        errors.append("{} - unknown link {!r}".format(where, link))
    elif link == "needs_doc":
        if src not in scheme_ids and src not in kinds:
            errors.append("{} - needs_doc from {!r}: not a scheme_id or doc_id".format(where, src))
        if dst not in kinds:
            errors.append("{} - needs_doc to {!r}: not a doc_id".format(where, dst))
    elif link == "satisfied_by":
        if kinds.get(src) != "group":
            errors.append("{} - satisfied_by must start at a group, {!r} is not one".format(where, src))
        if kinds.get(dst) != "document":
            errors.append("{} - satisfied_by must point at a document (no nested groups), got {!r}".format(where, dst))
    else:  # issued_by
        if kinds.get(src) != "document":
            errors.append("{} - issued_by must start at a document, got {!r}".format(where, src))
        if dst not in offices:
            errors.append("{} - issued_by to {!r}: not an office in lookups.csv".format(where, dst))


def check_doc_links(ds, errors):
    """Every link row, plus: groups have 2+ members and the paperwork chain has no cycle."""
    kinds = {r.get("doc_id"): r.get("kind") for r in ds.documents}
    scheme_ids = {r.get("scheme_id") for r in ds.schemes}
    offices = lookup_ids(ds)["office"]
    for row in ds.doc_links:
        check_doc_link_row(row, kinds, scheme_ids, offices, errors)
    check_unique(ds.doc_links, lambda r: (r.get("from_id"), r.get("link"), r.get("to_id")), "link", errors)
    check_group_sizes(ds, kinds, errors)
    check_no_cycles(ds, errors)


def check_group_sizes(ds, kinds, errors):
    """A group is "any one of", so it needs at least 2 documents to choose from."""
    members = {doc_id: 0 for doc_id, kind in kinds.items() if kind == "group"}
    for row in ds.doc_links:
        if row.get("link") == "satisfied_by" and row.get("from_id") in members:
            members[row.get("from_id")] += 1
    for group, count in members.items():
        if count < 2:
            errors.append("data/documents.csv - group {!r} has {} member(s); needs at least 2".format(group, count))


def check_no_cycles(ds, errors):
    """Depth-first search over needs_doc + satisfied_by; a cycle would make the
    paperwork path endless ("A needs B needs A")."""
    edges = {}
    for row in ds.doc_links:
        if row.get("link") in ("needs_doc", "satisfied_by"):
            edges.setdefault(row.get("from_id"), []).append(row.get("to_id"))
    state = {}  # node -> "visiting" | "done"

    def visit(node, path):
        state[node] = "visiting"
        for nxt in edges.get(node, []):
            if state.get(nxt) == "visiting":
                errors.append("data/document_links.csv - cycle: {}".format(" -> ".join(path + [nxt])))
            elif nxt not in state:
                visit(nxt, path + [nxt])
        state[node] = "done"

    for start in list(edges):
        if start not in state:
            visit(start, [start])


def check_citizens(ds, errors):
    """Fixtures: unique ids, numbers are numbers, gender/state exist in data/."""
    lookups = lookup_ids(ds)
    check_required(ds.citizens, ["citizen_id", "display_name"], errors)
    check_unique(ds.citizens, lambda r: r.get("citizen_id"), "citizen_id", errors)
    for row in ds.citizens:
        for column, kind in CITIZEN_NUMBERS.items():
            if not is_number(row.get(column), kind):
                errors.append("{} - {} must be a number or empty, got {!r}".format(row.where(), column, row.get(column)))
        for column, kind in (("gender_id", "gender"), ("state_id", "state")):
            if row.get(column) and row.get(column) not in lookups[kind]:
                errors.append("{} - {} {!r} is not a {} in lookups.csv".format(row.where(), column, row.get(column), kind))


def check_citizen_links(ds, errors):
    """Fixtures: known citizen, known link, value exists in data/ (has_doc -> a document, not a group)."""
    lookups = lookup_ids(ds)
    citizen_ids = {r.get("citizen_id") for r in ds.citizens}
    kinds = {r.get("doc_id"): r.get("kind") for r in ds.documents}
    for row in ds.citizen_links:
        link, value = row.get("link"), row.get("value")
        if row.get("citizen_id") not in citizen_ids:
            errors.append("{} - unknown citizen_id {!r}".format(row.where(), row.get("citizen_id")))
        if link not in CITIZEN_LINKS:
            errors.append("{} - unknown link {!r}".format(row.where(), link))
        elif link == "has_doc":
            if kinds.get(value) != "document":
                errors.append("{} - has_doc {!r} is not a document (groups can't be held)".format(row.where(), value))
        elif value not in lookups[CITIZEN_LINKS[link][0]]:
            errors.append("{} - {} {!r} is not a {} in lookups.csv".format(
                row.where(), link, value, CITIZEN_LINKS[link][0]))
    check_unique(ds.citizen_links, lambda r: (r.get("citizen_id"), r.get("link"), r.get("value")), "link", errors)


def validate(ds):
    """Run every rule; return the list of error messages (empty = all good).

    WHY: we collect all errors instead of stopping at the first, so whoever
    is entering data can fix everything in one pass.
    """
    errors = []
    for check in (check_lookups, check_schemes, check_criteria, check_documents,
                  check_doc_links, check_citizens, check_citizen_links):
        check(ds, errors)
    return errors
