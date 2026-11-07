"""Import a supplier books CSV into books.db, following the approved rules in rules.md.

Usage (from PowerShell, in this folder):
    python ingest.py books.csv

books.db and ingestion-log.json are kept in the same folder as this script.
The program is deterministic: no AI, no network, and the CSV file is only read.
"""

import csv
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "books.db"
LOG_PATH = HERE / "ingestion-log.json"

COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count", "work_ratings_count",
    "work_text_reviews_count", "ratings_1", "ratings_2", "ratings_3", "ratings_4",
    "ratings_5", "image_url", "small_image_url",
]
ISBN_FIELDS = ("isbn", "isbn13")
MAX_SQLITE_INT = 2**63 - 1

SCI_RE = re.compile(r"(\d)(?:\.(\d+))?[eE]\+?(\d+)")


# ---------------------------------------------------------------------------
# ISBN arithmetic
# ---------------------------------------------------------------------------

def isbn10_valid(s):
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)) % 11 == 0


def isbn13_valid(s):
    """13 digits, ISBN prefix 978/979 (but not 9790, which is ISMN), modulus-10 check."""
    if not re.fullmatch(r"\d{13}", s) or not s.startswith(("978", "979")) or s.startswith("9790"):
        return False
    return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s)) % 10 == 0


def isbn10_to_13(s10):
    core = "978" + s10[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def isbn13_to_10(s13):
    """Only meaningful for 978 ISBN-13s (Rule 6)."""
    core = s13[3:12]
    r = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
    return core + ("X" if r == 10 else str(r))


def describe_13_digit_problem(s):
    if not s.startswith(("978", "979")):
        return "Rule 12", f"non-ISBN prefix {s[:3]} (not 978 or 979): treated as a non-ISBN product code"
    if s.startswith("9790"):
        return "Rule 12", "prefix 9790 is reserved for ISMN (music), not ISBN"
    return "Rule 5", "ISBN-13 check digit fails the modulus-10 test"


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

def to_null(value):
    """Blank or whitespace-only cells become NULL (Answer 6, Final decision 2)."""
    if value is None or value.strip() == "":
        return None
    return value


def same_identity_text(a, b):
    """Title/author comparison: trim surrounding whitespace, then exact and case-sensitive."""
    return (a or "").strip() == (b or "").strip()


def event(kind, rule, field, message, original=None, result=None):
    e = {"type": kind, "rule": rule, "field": field, "message": message}
    if original is not None:
        e["original"] = original
    if result is not None:
        e["result"] = result
    return e


def compare_event(kind, rule, field, message, incoming, stored):
    """An event about an incoming CSV value versus the value already in books.db."""
    e = event(kind, rule, field, message)
    e["incoming"], e["stored"] = incoming, stored
    return e


# ---------------------------------------------------------------------------
# Row-level ISBN processing (no database access)
# ---------------------------------------------------------------------------

def classify_isbn10_field(raw):
    """Return (kind, value, events). kind: empty | valid10 | valid13 | invalid."""
    if to_null(raw) is None:
        return "empty", None, []
    events = []
    text = raw.strip().replace("-", "").replace(" ", "")
    if text != raw:
        events.append(event("correction", "Rule 3", "isbn", "removed hyphens/spaces", raw, text))
    if text.endswith("x"):
        fixed = text[:-1] + "X"
        events.append(event("correction", "Rule 3", "isbn", "changed final lowercase x to X", text, fixed))
        text = fixed

    if re.fullmatch(r"\d{9}[\dX]", text):
        if isbn10_valid(text):
            return "valid10", text, events
        return "invalid", None, events + [event(
            "warning", "Rule 5", "isbn", "ISBN-10 check digit fails the modulus-11 test; stored NULL", raw)]
    if text.isdigit() and len(text) < 10:
        padded = text.rjust(10, "0")
        if isbn10_valid(padded):
            return "valid10", padded, events + [event(
                "correction", "Rule 2", "isbn", "restored missing leading zeros", text, padded)]
        return "invalid", None, events + [event(
            "warning", "Rule 2", "isbn",
            f"{len(text)} digits; still fails the ISBN-10 check after adding leading zeros; stored NULL", raw)]
    if text.isdigit() and len(text) == 13:
        if isbn13_valid(text):
            return "valid13", text, events
        rule, why = describe_13_digit_problem(text)
        return "invalid", None, events + [event("warning", rule, "isbn", f"{why}; stored NULL", raw)]
    if "." in text:
        why = "damaged decimal value"
    elif re.search(r"[^0-9X]", text) or "X" in text[:-1]:
        why = "illegal characters"
    else:
        why = f"wrong length ({len(text)} characters)"
    return "invalid", None, events + [event("warning", "Rule 12", "isbn", f"{why}; stored NULL", raw)]


def classify_isbn13_field(raw):
    """Return (kind, value, events). kind: empty | valid13 | valid10 | sci | invalid."""
    if to_null(raw) is None:
        return "empty", None, []
    stripped = raw.strip()
    m = SCI_RE.fullmatch(stripped)
    if m:
        digits = m.group(1) + (m.group(2) or "")
        exponent = int(m.group(3))
        if exponent != 12 or len(digits) > 13:
            return "invalid", None, [event(
                "warning", "Rule 12", "isbn13",
                "scientific-notation value is not a 13-digit number; not an ISBN-13; stored NULL", raw)]
        if not digits.startswith(("978", "979")) or digits.startswith("9790"):
            rule, why = describe_13_digit_problem(digits.ljust(13, "0"))
            return "invalid", None, [event("warning", rule, "isbn13", f"{why}; stored NULL", raw)]
        return "sci", digits, []
    if "." in stripped:
        return "invalid", None, [event(
            "warning", "Rule 12", "isbn13", "damaged decimal value (.0 form); stored NULL", raw)]

    events = []
    text = stripped.replace("-", "").replace(" ", "")
    if text != raw:
        events.append(event("correction", "Rule 3", "isbn13", "removed hyphens/spaces", raw, text))
    if len(text) == 10 and text.endswith("x"):
        fixed = text[:-1] + "X"
        events.append(event("correction", "Rule 3", "isbn13", "changed final lowercase x to X", text, fixed))
        text = fixed

    if re.fullmatch(r"\d{13}", text):
        if isbn13_valid(text):
            return "valid13", text, events
        rule, why = describe_13_digit_problem(text)
        return "invalid", None, events + [event("warning", rule, "isbn13", f"{why}; stored NULL", raw)]
    if isbn10_valid(text):
        return "valid10", text, events
    why = "illegal characters" if re.search(r"\D", text) else f"wrong length ({len(text)} digits)"
    return "invalid", None, events + [event("warning", "Rule 12", "isbn13", f"{why}; stored NULL", raw)]


def sci_matches(digits, rebuilt13):
    """Final decision 1: round the rebuilt ISBN-13 half-to-even to the number of significant
    digits shown in the CSV, using exact decimal arithmetic, and require an exact match."""
    shown = len(digits)
    rounded = Decimal(rebuilt13).scaleb(shown - 13).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return rounded == Decimal(digits)


def process_isbns(raw10, raw13):
    """Apply Rules 2-6 and 10-12 to one row's two ISBN fields.

    Returns a dict with the final isbn/isbn13 (None for NULL), which final values were
    calculated from the other field, whether the pair conflicts (Rule 11), and events.
    """
    k10, v10, ev10 = classify_isbn10_field(raw10)
    k13, v13, ev13 = classify_isbn13_field(raw13)
    events = ev10 + ev13
    pair_conflict = False
    # A field counts as "genuinely empty" for Rule 6 when it was blank in the CSV, or when its
    # value was moved to the correct field by Rule 10.
    empty10 = k10 == "empty"
    empty13 = k13 == "empty"
    final10 = v10 if k10 == "valid10" else None
    final13 = v13 if k13 == "valid13" else None
    moved13_from_isbn = v10 if k10 == "valid13" else None
    moved10_from_isbn13 = v13 if k13 == "valid10" else None

    # Rule 10: valid identifiers found in the wrong field.
    if moved13_from_isbn is not None:
        if final13 is not None and final13 != moved13_from_isbn:
            pair_conflict = True
            events.append(event("conflict", "Rule 10/11", "isbn",
                                "valid ISBN-13 found in the isbn field, but isbn13 holds a different valid "
                                "ISBN-13; not choosing one", raw10, moved13_from_isbn))
        else:
            if k13 not in ("empty", "valid13"):
                events.append(event("warning", "Rule 10", "isbn13",
                                    "isbn13 held an unusable value; replaced by the valid ISBN-13 found in the "
                                    "isbn field", raw13))
            final13 = moved13_from_isbn
            empty10 = True
            events.append(event("correction", "Rule 10", "isbn",
                                "moved valid ISBN-13 from the isbn field to isbn13", raw10, moved13_from_isbn))
    if moved10_from_isbn13 is not None:
        if final10 is not None and final10 != moved10_from_isbn13:
            pair_conflict = True
            events.append(event("conflict", "Rule 10/11", "isbn13",
                                "valid ISBN-10 found in the isbn13 field, but isbn holds a different valid "
                                "ISBN-10; not choosing one", raw13, moved10_from_isbn13))
        else:
            if k10 not in ("empty", "valid10"):
                events.append(event("warning", "Rule 10", "isbn",
                                    "isbn held an unusable value; replaced by the valid ISBN-10 found in the "
                                    "isbn13 field", raw10))
            final10 = moved10_from_isbn13
            empty13 = True
            events.append(event("correction", "Rule 10", "isbn13",
                                "moved valid ISBN-10 from the isbn13 field to isbn", raw13, moved10_from_isbn13))

    # Rule 4: rebuild a scientific-notation ISBN-13 only from a validated ISBN-10 in this row.
    if k13 == "sci" and moved13_from_isbn is None:
        if final10 is None:
            events.append(event("warning", "Rule 4", "isbn13",
                                "scientific-notation ISBN-13 has lost digits and there is no validated ISBN-10 "
                                "to rebuild it from; stored NULL", raw13))
        else:
            rebuilt = isbn10_to_13(final10)
            if sci_matches(v13, rebuilt):
                final13 = rebuilt
                events.append(event("correction", "Rule 4", "isbn13",
                                    f"rebuilt ISBN-13 from ISBN-10 {final10}; matches the {len(v13)} significant "
                                    "digits shown (round half to even)", raw13, rebuilt))
            else:
                events.append(event("warning", "Rule 4", "isbn13",
                                    f"scientific-notation value is inconsistent with the ISBN-13 {rebuilt} rebuilt "
                                    f"from ISBN-10 {final10}; stored NULL", raw13))

    # Rule 11: both individually valid but not the same identifier.
    if not pair_conflict and final10 is not None and final13 is not None and isbn10_to_13(final10) != final13:
        pair_conflict = True
        events.append(event("conflict", "Rule 11", "isbn/isbn13",
                            f"ISBN-10 {final10} and ISBN-13 {final13} are each valid but do not correspond "
                            "to the same identifier", f"{raw10} | {raw13}"))

    derived = set()
    if pair_conflict:
        conflict_pair = (final10, final13)
        final10 = final13 = None
    else:
        conflict_pair = None
        # Rule 6: derive the missing form, only when that field was genuinely empty.
        if final10 is not None and final13 is None and empty13:
            final13 = isbn10_to_13(final10)
            derived.add("isbn13")
            events.append(event("correction", "Rule 6", "isbn13",
                                f"calculated ISBN-13 from ISBN-10 {final10}", None, final13))
        if final13 is not None and final10 is None and empty10 and final13.startswith("978"):
            final10 = isbn13_to_10(final13)
            derived.add("isbn")
            events.append(event("correction", "Rule 6", "isbn",
                                f"calculated ISBN-10 from ISBN-13 {final13}", None, final10))

    return {
        "isbn": final10,
        "isbn13": final13,
        "derived": derived,
        "pair_conflict": conflict_pair,
        "empty_raw": (k10 == "empty", k13 == "empty"),
        "events": events,
    }


def missing_isbn_warnings(result):
    """Rules 1 and 6: warn about NULL ISBNs that were blank in the source (unusable values
    already carry their own warning). No missing ISBN-10 warning for a 979 ISBN-13."""
    events = []
    if result["pair_conflict"]:
        return events
    if result["isbn"] is None and result["isbn13"] is None:
        events.append(event("warning", "Rule 6", "isbn/isbn13",
                            "no usable ISBN in this row; both ISBN fields stored NULL"))
        return events
    blank10, blank13 = result["empty_raw"]
    if result["isbn"] is None and blank10 and not result["isbn13"].startswith("979"):
        events.append(event("warning", "Rule 6", "isbn", "ISBN-10 missing from source; stored NULL"))
    if result["isbn13"] is None and blank13:
        events.append(event("warning", "Rule 6", "isbn13", "ISBN-13 missing from source; stored NULL"))
    return events


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_database():
    conn = sqlite3.connect(DB_PATH)
    cols = ",\n    ".join(
        "book_id INTEGER PRIMARY KEY" if c == "book_id" else f"{c} TEXT" for c in COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS books (\n    {cols}\n)")
    return conn


class Index:
    """In-memory lookups over the books table, kept current as rows are written."""

    def __init__(self, conn):
        self.by_isbn10, self.by_isbn13, self.by_identity = {}, {}, {}
        for book_id, isbn, isbn13, title, authors in conn.execute(
                "SELECT book_id, isbn, isbn13, title, authors FROM books"):
            self.add(book_id, isbn, isbn13, title, authors)

    def add(self, book_id, isbn, isbn13, title, authors):
        self.add_isbns(book_id, isbn, isbn13)
        key = ((title or "").strip(), (authors or "").strip())
        self.by_identity.setdefault(key, set()).add(book_id)

    def add_isbns(self, book_id, isbn, isbn13):
        if isbn:
            self.by_isbn10[isbn] = book_id
        if isbn13:
            self.by_isbn13[isbn13] = book_id


def check_assigned_elsewhere(result, book_id, index):
    """Rule 11 / Clarification 4: an ISBN already assigned to a different book_id is kept on that
    book; the incoming value becomes NULL, along with any value calculated from it."""
    events = []
    owners = {"isbn": index.by_isbn10, "isbn13": index.by_isbn13}
    conflicting = [f for f in ISBN_FIELDS if result[f] is not None and owners[f].get(result[f], book_id) != book_id]
    for field in conflicting:
        other = owners[field][result[field]]
        events.append(event("conflict", "Rule 11", field,
                            f"{result[field]} is already assigned to book_id {other}; kept on that book and "
                            "stored NULL here", result[field]))
    for field in conflicting:
        result[field] = None
    other_field = {"isbn": "isbn13", "isbn13": "isbn"}
    for field in conflicting:
        dep = other_field[field]
        if dep in result["derived"] and result[dep] is not None:
            events.append(event("conflict", "Rule 11", dep,
                                f"{result[dep]} was calculated from the conflicting {field}; stored NULL",
                                result[dep]))
            result[dep] = None
    return events


# ---------------------------------------------------------------------------
# One CSV row
# ---------------------------------------------------------------------------

def validate_essentials(row):
    """Rule 13. Returns a list of problems (empty when the row is usable)."""
    problems = []
    raw_id = row.get("book_id")
    text_id = (raw_id or "").strip()
    if text_id == "":
        problems.append("book_id is blank")
    elif not re.fullmatch(r"[1-9]\d*", text_id) or int(text_id) > MAX_SQLITE_INT:
        problems.append(f"book_id {raw_id!r} is not a positive whole number")
    if to_null(row.get("title")) is None:
        problems.append("title is blank")
    if to_null(row.get("authors")) is None:
        problems.append("authors is blank")
    return problems


def process_row(conn, index, row, line, seen_rows):
    entry = {"line": line, "book_id": row.get("book_id"), "title": row.get("title"), "events": []}

    if None in row or any(v is None for v in row.values()):
        n = sum(v is not None for k, v in row.items() if k is not None) + len(row.get(None) or [])
        entry.update(outcome="rejected", reason=f"row has {n} fields; expected {len(COLUMNS)}",
                     original_values=row_for_log(row))
        return entry

    problems = validate_essentials(row)
    if problems:
        entry.update(outcome="rejected", reason="Rule 13: " + "; ".join(problems),
                     original_values=row_for_log(row))
        return entry

    book_id = int(row["book_id"].strip())
    entry["book_id"] = book_id
    raw_key = tuple(row[c] for c in COLUMNS)
    duplicate_of = seen_rows.get(raw_key)
    seen_rows.setdefault(raw_key, line)

    stored = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM books WHERE book_id = ?", (book_id,)).fetchone()
    if stored is not None:
        return process_existing(conn, index, row, entry, dict(zip(COLUMNS, stored)), duplicate_of)
    return process_new(conn, index, row, entry, book_id)


def row_for_log(row):
    values = {c: row.get(c) for c in COLUMNS if c in row}
    if row.get(None):
        values["extra_fields"] = row[None]
    return values


def process_new(conn, index, row, entry, book_id):
    result = process_isbns(row["isbn"], row["isbn13"])
    events = result["events"] + check_assigned_elsewhere(result, book_id, index)
    events += missing_isbn_warnings(result)
    if result["pair_conflict"]:
        events.append(event("review", "Rule 11", "isbn/isbn13",
                            "new book kept with both ISBNs NULL; decide which ISBN is correct"))

    key = (row["title"].strip(), row["authors"].strip())
    others = sorted(index.by_identity.get(key, set()) - {book_id})
    if others:
        events.append(event("warning", "Answer 7", "title/authors",
                            f"possible duplicate: same title and author as book_id {', '.join(map(str, others))}; "
                            "kept as a separate record"))

    values = {c: to_null(row[c]) for c in COLUMNS}
    values["book_id"] = book_id
    values["isbn"], values["isbn13"] = result["isbn"], result["isbn13"]
    conn.execute(f"INSERT INTO books ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                 [values[c] for c in COLUMNS])
    index.add(book_id, values["isbn"], values["isbn13"], values["title"], values["authors"])

    entry.update(outcome="loaded", reason="new book inserted", events=events,
                 stored={"isbn": values["isbn"], "isbn13": values["isbn13"]})
    return entry


def process_existing(conn, index, row, entry, stored, duplicate_of):
    book_id = entry["book_id"]
    # Rules 7 and 15, Answers 4 and 5: identity must agree after trimming, or the row is rejected.
    mismatched = [c for c in ("title", "authors") if not same_identity_text(row[c], stored[c])]
    if mismatched:
        entry.update(outcome="rejected",
                     reason=f"Answer 5: book_id {book_id} already exists with a different "
                            f"{' and '.join(mismatched)}; existing record preserved",
                     original_values=row_for_log(row),
                     events=[compare_event("review", "Rule 15", c, "identity conflict: incoming value differs from "
                                           "stored value", row[c], stored[c]) for c in mismatched])
        return entry

    result = process_isbns(row["isbn"], row["isbn13"])
    events = check_assigned_elsewhere(result, book_id, index)
    if result["pair_conflict"]:
        events.append(event("review", "Rule 11", "isbn/isbn13",
                            "incoming ISBN pair conflicts; stored ISBNs preserved",
                            " | ".join(v or "" for v in result["pair_conflict"]),
                            f"{stored['isbn']} | {stored['isbn13']}"))

    # Rule 8: fill NULL ISBN fields with validated values; never erase or overwrite good values.
    updates = {}
    for field in ISBN_FIELDS:
        incoming, current = result[field], stored[field]
        if incoming is None and current is not None:
            events.append(compare_event("warning", "Rule 8", field,
                                        "incoming value is blank or unusable; stored valid value kept",
                                        row[field], current))
        if incoming is None or incoming == current:
            continue
        if current is not None:
            events.append(compare_event("conflict", "Rule 8", field,
                                        "incoming valid ISBN differs from stored valid value; stored value preserved",
                                        incoming, current))
            continue
        updates[field] = incoming
    # A filled value must agree with the other ISBN as it will be stored.
    new10 = updates.get("isbn", stored["isbn"])
    new13 = updates.get("isbn13", stored["isbn13"])
    if updates and new10 is not None and new13 is not None and isbn10_to_13(new10) != new13:
        for field, value in updates.items():
            events.append(event("conflict", "Rule 8", field,
                                f"incoming {value} does not correspond to the stored "
                                f"{'isbn13' if field == 'isbn' else 'isbn'}; stored values preserved", value))
        updates = {}

    # Rule 14 / Answer 6 / Final decision 3: report other field differences, never apply them.
    for c in COLUMNS:
        if c in ISBN_FIELDS or c == "book_id":
            continue
        if c in ("title", "authors"):
            continue  # already equal after trimming; whitespace-only differences are not reported
        if to_null(row[c]) != stored[c]:
            events.append(compare_event("review", "Rule 14", c,
                                        "incoming value differs from stored value; stored value kept",
                                        row[c], stored[c]))

    if updates:
        conn.execute(f"UPDATE books SET {', '.join(f'{f} = ?' for f in updates)} WHERE book_id = ?",
                     [*updates.values(), book_id])
        index.add_isbns(book_id, updates.get("isbn"), updates.get("isbn13"))
        for field, value in updates.items():
            e = event("update", "Rule 8", field, "filled missing ISBN with a validated value")
            e["old"], e["new"] = stored[field], value
            events.append(e)
        corrections = [e for e in result["events"] if e["type"] == "correction" and e["field"] in updates]
        entry.update(outcome="updated", reason="existing book: safe ISBN improvement applied",
                     events=corrections + events)
        return entry

    if duplicate_of is not None:
        reason = f"exact duplicate of line {duplicate_of} in this file; already stored"
        events.insert(0, event("duplicate", "Rule 7", "row", reason))
    else:
        reason = "book_id already stored; no permitted ISBN improvement"
    entry.update(outcome="skipped", reason=reason, events=events)
    return entry


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def load_log():
    if not LOG_PATH.exists():
        return []
    try:
        runs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"ERROR: {LOG_PATH.name} exists but cannot be read ({exc}). "
                         "Nothing was imported; fix or move the file and run again.")
    if not isinstance(runs, list):
        raise SystemExit(f"ERROR: {LOG_PATH.name} is not a list of runs. Nothing was imported.")
    return runs


def save_log(runs):
    fd, tmp = tempfile.mkstemp(dir=HERE, prefix=".ingestion-log-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(runs, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, LOG_PATH)


def summarize(entries, rows_read):
    counts = {k: 0 for k in ("loaded", "updated", "skipped", "rejected")}
    by_type = {}
    review = 0
    for e in entries:
        counts[e["outcome"]] += 1
        for ev in e["events"]:
            by_type[ev["type"]] = by_type.get(ev["type"], 0) + 1
        e["needs_review"] = e["outcome"] == "rejected" or any(
            ev["type"] in ("conflict", "review") for ev in e["events"])
        review += e["needs_review"]
    assert sum(counts.values()) == rows_read, "every row must have exactly one outcome"
    return counts, by_type, review


def record_failure(runs, run, message):
    run.update(status="failed", error=message, rows_read=0,
               counts={"loaded": 0, "updated": 0, "skipped": 0, "rejected": 0},
               event_totals={}, rows_needing_review=0, rows=[])
    runs.append(run)
    save_log(runs)
    print(f"ERROR: {message}. Run recorded in {LOG_PATH.name}.", file=sys.stderr)


def main(argv):
    if len(argv) != 2:
        print("Usage: python ingest.py <books-file.csv>", file=sys.stderr)
        return 2
    source = Path(argv[1])
    runs = load_log()  # fail before touching the database if the log is unreadable
    run = {
        "run_number": len(runs) + 1,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_file": source.name,
        "source_path": str(source.resolve()),
        "database": str(DB_PATH),
    }

    try:
        with open(source, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            if sorted(header) != sorted(COLUMNS):
                missing = [c for c in COLUMNS if c not in header]
                extra = [c for c in header if c not in COLUMNS]
                raise ValueError(f"unexpected CSV columns (missing: {missing or 'none'}; "
                                 f"unexpected: {extra or 'none'})")
            records = []
            start = 2
            for row in reader:
                records.append((start, row))
                start = reader.line_num + 1
    except (OSError, UnicodeDecodeError, ValueError, csv.Error) as exc:
        record_failure(runs, run, f"could not read {source}: {exc}")
        return 1

    conn = open_database()
    try:
        index = Index(conn)
        seen_rows = {}
        entries = [process_row(conn, index, row, line, seen_rows) for line, row in records]
        counts, by_type, review = summarize(entries, len(records))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        record_failure(runs, run, f"import stopped and the database was left unchanged: {exc!r}")
        raise
    finally:
        conn.close()

    # Every row with something to report: anything not loaded, and loaded rows with events.
    logged = [e for e in entries if e["outcome"] != "loaded" or e["events"]]
    run.update(status="completed", rows_read=len(records), counts=counts,
               event_totals=by_type, rows_needing_review=review, rows=logged)
    runs.append(run)
    save_log(runs)

    print(f"{source.name}: {len(records)} rows read -> "
          f"{counts['loaded']} loaded, {counts['updated']} updated, "
          f"{counts['skipped']} skipped, {counts['rejected']} rejected "
          f"({review} rows need review). Run {run['run_number']} appended to {LOG_PATH.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
