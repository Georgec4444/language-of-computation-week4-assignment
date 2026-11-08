"""Import a supplier books CSV into books.db, following the shared Module 4 rules in rules.md.

Usage (from PowerShell, in this folder):
    python ingest.py books.csv

books.db and ingestion-log.json are kept in the same folder as this script (R36).
The program is deterministic: no AI, no network, and the CSV file is only read (R01).
Rule references (R01-R40) point to the numbered rules in rules.md.
"""

import csv
import hashlib
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

# R02: the 23 required supplier columns.
COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count", "work_ratings_count",
    "work_text_reviews_count", "ratings_1", "ratings_2", "ratings_3", "ratings_4",
    "ratings_5", "image_url", "small_image_url",
]
ISBN_FIELDS = ("isbn", "isbn13")
# R03/R30: raw isbn/isbn13 text stays in the supplier columns; cleaned values live separately.
CLEAN = {"isbn": "isbn10_clean", "isbn13": "isbn13_clean"}
DB_COLUMNS = COLUMNS + [CLEAN["isbn"], CLEAN["isbn13"]]

# R27: supplier metadata that may be updated on a reliably matched existing book.
APPROVED_UPDATES = [
    "books_count", "average_rating", "ratings_count", "work_ratings_count",
    "work_text_reviews_count", "ratings_1", "ratings_2", "ratings_3", "ratings_4",
    "ratings_5", "image_url", "small_image_url",
]
# R28: identity-sensitive fields that are never changed automatically. Differences are reported.
PROTECTED_FOR_REVIEW = [
    "goodreads_book_id", "best_book_id", "work_id", "original_title",
    "original_publication_year", "language_code",
]
MAX_SQLITE_INT = 2**63 - 1

SCI_RE = re.compile(r"(\d)(?:\.(\d+))?[eE]\+?(\d+)")
DOT_ZERO_RE = re.compile(r"(.+)\.0")  # R17: a value followed by exactly ".0"


class SchemaError(Exception):
    pass


# ---------------------------------------------------------------------------
# ISBN arithmetic (R10, R11, R14)
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
    """Only meaningful for 978 ISBN-13s (R14)."""
    core = s13[3:12]
    r = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
    return core + ("X" if r == 10 else str(r))


def describe_13_digit_problem(s):
    if not s.startswith(("978", "979")):
        return "R18", f"non-ISBN prefix {s[:3]} (not 978 or 979): treated as a non-ISBN product code"
    if s.startswith("9790"):
        return "R11", "prefix 9790 is reserved for ISMN (music), not ISBN"
    return "R11", "ISBN-13 check digit fails the modulus-10 test"


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

def to_null(value):
    """Whitespace-only cells become NULL for normalized storage (R03)."""
    if value is None or value.strip() == "":
        return None
    return value


def same_identity_text(a, b):
    """Title/author comparison: trim surrounding whitespace, then exact and case-sensitive (R07)."""
    return (a or "").strip() == (b or "").strip()


def clean_goodreads_id(value):
    """R06: a usable Goodreads ID is digits only and greater than zero; otherwise None."""
    text = (value or "").strip()
    if re.fullmatch(r"\d+", text) and int(text) > 0:
        return str(int(text))
    return None


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


def change_event(rule, field, message, old, new):
    e = event("update", rule, field, message)
    e["old"], e["new"] = old, new
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
        events.append(event("correction", "R12", "isbn", "removed hyphens/spaces", raw, text))
    if text.endswith("x"):
        fixed = text[:-1] + "X"
        events.append(event("correction", "R12", "isbn", "changed final lowercase x to X", text, fixed))
        text = fixed

    if re.fullmatch(r"\d{9}[\dX]", text):
        if isbn10_valid(text):
            return "valid10", text, events
        return "invalid", None, events + [event(
            "warning", "R10/R18", "isbn", "ISBN-10 check digit fails the modulus-11 test; not changed; stored NULL",
            raw)]
    if text.isdigit() and 7 <= len(text) <= 9:
        padded = text.rjust(10, "0")
        if isbn10_valid(padded):
            return "valid10", padded, events + [event(
                "correction", "R12", "isbn", "restored missing leading zeros", text, padded)]
        return "invalid", None, events + [event(
            "warning", "R12/R18", "isbn",
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
    return "invalid", None, events + [event("warning", "R18", "isbn", f"{why}; stored NULL", raw)]


def classify_isbn13_field(raw):
    """Return (kind, value, events, info).

    kind: empty | valid13 | valid10 | sci | dot0 | invalid.
    info (for invalid values) tells R19 whether a companion ISBN-10 may replace the value:
      "rounded"  rounded scientific notation that is not an ISBN-13 candidate (safeguard 1)
      "bad978"   a 13-digit 978 candidate with a bad checksum (safeguard 2)
      "bad979"   a 13-digit 979 value (not 9790) that fails its checksum, written plainly or as exact
                 scientific notation (C1, C3). A 9790 ISMN value is a product code (info None), and a
                 rounded 9790 value is "rounded" (C1, C3).
      "decimal"  a decimal-looking value; R17 alone decides those, so R19 does not apply
      None       any other unusable value
    """
    if to_null(raw) is None:
        return "empty", None, [], None
    stripped = raw.strip()
    m = SCI_RE.fullmatch(stripped)
    if m:
        digits = m.group(1) + (m.group(2) or "")
        exponent = int(m.group(3))
        rounded = len(digits) < exponent + 1
        if exponent == 12 and len(digits) == 13 and digits.startswith("979") \
                and not digits.startswith("9790") and not isbn13_valid(digits):
            # C1, C3: exact notation for an invalid 13-digit 979 value is that value.
            why = f"{describe_13_digit_problem(digits)[1]} (exact scientific notation for {digits})"
            return "invalid", None, [event("warning", "R11", "isbn13", f"{why}; stored NULL", raw)], \
                ("bad979", digits)
        if exponent == 12 and len(digits) <= 13 and digits.startswith(("978", "979")) \
                and not digits.startswith("9790"):
            return "sci", digits, [], None
        if exponent != 12 or len(digits) > 13:
            why = "scientific-notation value is not a 13-digit number; not an ISBN-13"
        else:
            why = describe_13_digit_problem(digits.ljust(13, "0"))[1]
        return "invalid", None, [event("warning", "R16/R18", "isbn13", f"{why}; stored NULL", raw)], \
            ("rounded" if rounded else None)
    dot0 = DOT_ZERO_RE.fullmatch(stripped)
    if dot0:
        # R17, C2: apply the R12 repairs (including leading-zero padding) to the digits before
        # ".0". Only usable if the result matches the row's validated ISBN-10; decided in
        # process_isbns.
        kind, value, _ = classify_isbn10_field(dot0.group(1))
        if kind == "valid10":
            return "dot0", value, [], None
    if "." in stripped:
        return "invalid", None, [event(
            "warning", "R17/R18", "isbn13", "decimal-looking value is not an ISBN; not stripped; stored NULL",
            raw)], "decimal"

    events = []
    text = stripped.replace("-", "").replace(" ", "")
    if text != raw:
        events.append(event("correction", "R12", "isbn13", "removed hyphens/spaces", raw, text))
    if len(text) == 10 and text.endswith("x"):
        fixed = text[:-1] + "X"
        events.append(event("correction", "R12", "isbn13", "changed final lowercase x to X", text, fixed))
        text = fixed

    if re.fullmatch(r"\d{13}", text):
        if isbn13_valid(text):
            return "valid13", text, events, None
        rule, why = describe_13_digit_problem(text)
        if text.startswith("978"):
            info = "bad978"
        elif text.startswith("979") and not text.startswith("9790"):
            info = "bad979"  # C3: a 9790 ISMN is a product code instead
        else:
            info = None
        return "invalid", None, events + [event("warning", rule, "isbn13", f"{why}; stored NULL", raw)], \
            (info, text) if info else None
    if isbn10_valid(text):
        return "valid10", text, events, None
    why = "illegal characters" if re.search(r"\D", text) else f"wrong length ({len(text)} digits)"
    return "invalid", None, events + [event("warning", "R18", "isbn13", f"{why}; stored NULL", raw)], None


def sci_matches(digits, rebuilt13):
    """R16: round the rebuilt ISBN-13 half-to-even to the number of significant digits shown in
    the CSV, using exact decimal arithmetic, and require an exact match."""
    shown = len(digits)
    rounded = Decimal(rebuilt13).scaleb(shown - 13).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    return rounded == Decimal(digits)


def process_isbns(raw10, raw13):
    """Apply R10-R21 to one row's two ISBN fields.

    Returns a dict with the cleaned isbn/isbn13 (None for NULL), which cleaned values were
    derived from the other field, whether the pair conflicts (R21), and events.
    """
    k10, v10, ev10 = classify_isbn10_field(raw10)
    k13, v13, ev13, info13 = classify_isbn13_field(raw13)
    events = ev10 + ev13
    pair_conflict = False
    # A field counts as "genuinely empty" for R15 when it was blank in the CSV, or when its
    # value was moved to the correct field by R13.
    empty10 = k10 == "empty"
    empty13 = k13 == "empty"
    final10 = v10 if k10 == "valid10" else None
    final13 = v13 if k13 == "valid13" else None
    moved13_from_isbn = v10 if k10 == "valid13" else None
    moved10_from_isbn13 = v13 if k13 == "valid10" else None
    derived = set()

    # R13: valid identifiers found in the wrong field.
    if moved13_from_isbn is not None:
        if final13 is not None and final13 != moved13_from_isbn:
            pair_conflict = True
            events.append(event("conflict", "R13/R21", "isbn",
                                "valid ISBN-13 found in the isbn field, but isbn13 holds a different valid "
                                "ISBN-13; not choosing one", raw10, moved13_from_isbn))
        else:
            if k13 not in ("empty", "valid13"):
                events.append(event("warning", "R13", "isbn13",
                                    "isbn13 held an unusable value; replaced by the valid ISBN-13 found in the "
                                    "isbn field", raw13))
            final13 = moved13_from_isbn
            empty10 = True
            events.append(event("correction", "R13", "isbn",
                                "moved valid ISBN-13 from the isbn field to isbn13", raw10, moved13_from_isbn))
    if moved10_from_isbn13 is not None:
        if final10 is not None and final10 != moved10_from_isbn13:
            pair_conflict = True
            events.append(event("conflict", "R13/R21", "isbn13",
                                "valid ISBN-10 found in the isbn13 field, but isbn holds a different valid "
                                "ISBN-10; not choosing one", raw13, moved10_from_isbn13))
        else:
            if k10 not in ("empty", "valid10"):
                events.append(event("warning", "R13", "isbn",
                                    "isbn held an unusable value; replaced by the valid ISBN-10 found in the "
                                    "isbn13 field", raw10))
            final10 = moved10_from_isbn13
            empty13 = True
            events.append(event("correction", "R13", "isbn13",
                                "moved valid ISBN-10 from the isbn13 field to isbn", raw13, moved10_from_isbn13))

    # R17: narrow ".0" exception, confirmed only by the same validated ISBN-10 in this row.
    if k13 == "dot0":
        if final10 is not None and k10 == "valid10" and v13 == final10:
            final13 = isbn10_to_13(final10)
            derived.add("isbn13")
            events.append(event("correction", "R17", "isbn13",
                                f"confirmed: removing .0 gives (after the R12 repairs) the same validated ISBN-10 "
                                f"{final10} as the isbn field; derived ISBN-13", raw13, final13))
        else:
            events.append(event("warning", "R17/R18", "isbn13",
                                "decimal-looking value is not confirmed by the same validated ISBN-10 in this row; "
                                "not stripped; stored NULL", raw13))

    # R16: rebuild a scientific-notation ISBN-13 only from a validated ISBN-10 in this row.
    if k13 == "sci" and moved13_from_isbn is None:
        if final10 is None:
            events.append(event("warning", "R16", "isbn13",
                                "scientific-notation ISBN-13 has lost digits and there is no validated ISBN-10 "
                                "to rebuild it from; stored NULL", raw13))
        else:
            rebuilt = isbn10_to_13(final10)
            if sci_matches(v13, rebuilt):
                final13 = rebuilt
                events.append(event("correction", "R16", "isbn13",
                                    f"rebuilt ISBN-13 from ISBN-10 {final10}; matches the {len(v13)} significant "
                                    "digits shown (round half to even)", raw13, rebuilt))
            else:
                events.append(event("warning", "R16", "isbn13",
                                    f"scientific-notation value is inconsistent with the ISBN-13 {rebuilt} rebuilt "
                                    f"from ISBN-10 {final10}; stored NULL", raw13))

    # R19: a valid ISBN-10 may supply its 978 ISBN-13 when the raw isbn13 is an unusable value.
    if k13 == "invalid" and moved13_from_isbn is None and final10 is not None and final13 is None:
        candidate = isbn10_to_13(final10)
        if isinstance(info13, tuple) and info13[0] == "bad979":
            # C3: an invalid 979 value can never agree with a 978 derivation.
            events.append(event("review", "C3", "isbn13",
                                f"invalid 979 value contradicts the 978 ISBN-13 {candidate} that ISBN-10 "
                                f"{final10} would give; ISBN-13 not derived; stored NULL; review needed",
                                raw13))
        elif info13 == "rounded":
            events.append(event("warning", "R19", "isbn13",
                                "rounded scientific-notation value cannot be confirmed (R16); ISBN-13 not derived",
                                raw13))
        elif info13 == "decimal":
            pass  # R17: unconfirmed decimal values stay invalid; already warned above
        elif isinstance(info13, tuple) and info13[0] == "bad978" and info13[1][:12] != candidate[:12]:
            events.append(event("conflict", "R19", "isbn13",
                                f"invalid 978 ISBN-13 contradicts the ISBN-13 {candidate} derived from ISBN-10 "
                                f"{final10}; stored NULL", raw13, candidate))
        else:
            final13 = candidate
            derived.add("isbn13")
            events.append(event("correction", "R19", "isbn13",
                                f"raw isbn13 is not a usable ISBN; derived ISBN-13 from the valid ISBN-10 {final10}",
                                raw13, candidate))

    # R21: both individually valid but not the same identifier.
    if not pair_conflict and final10 is not None and final13 is not None and isbn10_to_13(final10) != final13:
        pair_conflict = True
        events.append(event("conflict", "R21", "isbn/isbn13",
                            f"ISBN-10 {final10} and ISBN-13 {final13} are each valid but do not correspond "
                            "to the same identifier", f"{raw10} | {raw13}"))

    if pair_conflict:
        conflict_pair = (final10, final13)
        final10 = final13 = None
        derived = set()
    else:
        conflict_pair = None
        # R14/R15: derive the missing form, only when that field was genuinely empty.
        if final10 is not None and final13 is None and empty13:
            final13 = isbn10_to_13(final10)
            derived.add("isbn13")
            events.append(event("correction", "R14/R15", "isbn13",
                                f"calculated ISBN-13 from ISBN-10 {final10}", None, final13))
        if final13 is not None and final10 is None and empty10 and final13.startswith("978"):
            final10 = isbn13_to_10(final13)
            derived.add("isbn")
            events.append(event("correction", "R14/R15", "isbn",
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
    """R15/R20: warn about NULL ISBNs that were blank in the source (unusable values already
    carry their own warning). No missing ISBN-10 warning for a 979 ISBN-13 (R14)."""
    events = []
    if result["pair_conflict"]:
        return events
    if result["isbn"] is None and result["isbn13"] is None:
        events.append(event("warning", "R15/R20", "isbn/isbn13",
                            "no usable ISBN in this row; both cleaned ISBNs stored NULL"))
        return events
    blank10, blank13 = result["empty_raw"]
    if result["isbn"] is None and blank10 and not result["isbn13"].startswith("979"):
        events.append(event("warning", "R15", "isbn", "ISBN-10 missing from source; stored NULL"))
    if result["isbn13"] is None and blank13:
        events.append(event("warning", "R15", "isbn13", "ISBN-13 missing from source; stored NULL"))
    return events


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def open_database():
    conn = sqlite3.connect(DB_PATH)
    cols = ",\n    ".join(
        "book_id INTEGER PRIMARY KEY" if c == "book_id" else f"{c} TEXT" for c in DB_COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS books (\n    {cols}\n)")
    present = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    missing = [c for c in DB_COLUMNS if c not in present]
    if missing:
        conn.close()
        raise SchemaError(f"{DB_PATH.name} has an older books table without columns {missing}; "
                          "move it aside and run again")
    return conn


class Index:
    """In-memory lookups over the books table, kept current as rows are written."""

    def __init__(self, conn):
        self.by_isbn10, self.by_isbn13, self.by_identity, self.by_goodreads = {}, {}, {}, {}
        for book_id, isbn, isbn13, title, authors, goodreads in conn.execute(
                f"SELECT book_id, {CLEAN['isbn']}, {CLEAN['isbn13']}, title, authors, goodreads_book_id "
                "FROM books"):
            self.add(book_id, isbn, isbn13, title, authors, goodreads)

    def add(self, book_id, isbn, isbn13, title, authors, goodreads):
        self.add_isbns(book_id, isbn, isbn13)
        key = ((title or "").strip(), (authors or "").strip())
        self.by_identity.setdefault(key, set()).add(book_id)
        gid = clean_goodreads_id(goodreads)
        if gid is not None:
            self.by_goodreads.setdefault(gid, book_id)

    def add_isbns(self, book_id, isbn, isbn13):
        if isbn:
            self.by_isbn10[isbn] = book_id
        if isbn13:
            self.by_isbn13[isbn13] = book_id

    def owners(self, field):
        return self.by_isbn10 if field == "isbn" else self.by_isbn13


def check_assigned_elsewhere(result, book_id, index, disputed):
    """R22: an ISBN already accepted for a different book_id is not reassigned, and an ISBN
    claimed by two different books in this delivery (with no accepted owner) goes to neither.
    The conflicting value becomes NULL here, along with any value derived from it."""
    events = []
    conflicting = []
    for field in ISBN_FIELDS:
        value = result[field]
        if value is None:
            continue
        owner = index.owners(field).get(value)
        if owner is not None and owner != book_id:
            conflicting.append(field)
            events.append(event("conflict", "R22", field,
                                f"{value} is already assigned to book_id {owner}; kept on that book and "
                                "stored NULL here", value))
        elif owner is None and value in disputed[field]:
            conflicting.append(field)
            events.append(event("conflict", "R22", field,
                                f"{value} is claimed by more than one book in this file and has no accepted "
                                "owner; not assigned to any of them; stored NULL", value))
    for field in conflicting:
        result[field] = None
    other_field = {"isbn": "isbn13", "isbn13": "isbn"}
    for field in conflicting:
        dep = other_field[field]
        if dep in result["derived"] and result[dep] is not None:
            events.append(event("conflict", "R22", dep,
                                f"{result[dep]} was derived from the conflicting {field}; stored NULL",
                                result[dep]))
            result[dep] = None
    return events


# ---------------------------------------------------------------------------
# Same-file conflicts, decided before any row is written (R08, R22, R24)
# ---------------------------------------------------------------------------

def is_malformed(row):
    return None in row or any(v is None for v in row.values())


def normalized_form(row):
    """Row values after normalization, so formatting-only differences are not conflicts (R24)."""
    result = process_isbns(row["isbn"], row["isbn13"])
    values = []
    for c in COLUMNS:
        if c in ISBN_FIELDS:
            continue
        v = to_null(row[c])
        values.append(v.strip() if v is not None and c in ("title", "authors") else v)
    values[0] = int(row["book_id"].strip())
    return tuple(values) + (result["isbn"], result["isbn13"])


def plan_delivery(conn, index, records):
    """Return (rejections, flags, disputed) for rows whose outcome depends on other rows in the
    same file, so that no outcome depends on file order."""
    stored_ids = {r[0] for r in conn.execute("SELECT book_id FROM books")}
    candidates, seen_raw = [], set()
    for line, row in records:
        if is_malformed(row) or validate_essentials(row):
            continue
        raw_key = tuple(row[c] for c in COLUMNS)
        if raw_key in seen_raw:
            continue  # exact duplicates follow R23, not these conflict rules
        seen_raw.add(raw_key)
        candidates.append((line, row, int(row["book_id"].strip())))

    rejections, flags = {}, {}

    # R24: nonidentical rows claiming the same book_id.
    by_id = {}
    for line, row, book_id in candidates:
        by_id.setdefault(book_id, []).append((line, row))
    for book_id, group in by_id.items():
        if len(group) < 2 or len({normalized_form(row) for _, row in group}) == 1:
            continue
        lines = [line for line, _ in group]
        for line, _ in group:
            others = ", ".join(str(x) for x in lines if x != line)
            if book_id in stored_ids:
                flags.setdefault(line, []).append(event(
                    "review", "R24", "row", f"book_id {book_id} also appears with different values on line(s) "
                                            f"{others} of this file"))
            else:
                rejections[line] = (f"R24: book_id {book_id} appears on lines {', '.join(map(str, lines))} "
                                    "with materially different values and has no accepted record; "
                                    "none of them chosen")

    # R08: two new rows with different book_ids claiming the same valid Goodreads ID.
    by_gid = {}
    for line, row, book_id in candidates:
        gid = clean_goodreads_id(row["goodreads_book_id"])
        if gid is not None and line not in rejections:
            by_gid.setdefault(gid, []).append((line, book_id))
    for gid, group in by_gid.items():
        if len({book_id for _, book_id in group}) < 2:
            continue
        lines = ", ".join(str(line) for line, _ in group)
        for line, book_id in group:
            if book_id not in stored_ids:
                rejections[line] = (f"R08: goodreads_book_id {gid} is claimed by different book_ids on lines "
                                    f"{lines}; competing identities, none of them chosen")

    # R22: the same valid ISBN claimed by two different books in this file.
    claims = {"isbn": {}, "isbn13": {}}
    for line, row, book_id in candidates:
        if line in rejections:
            continue
        result = process_isbns(row["isbn"], row["isbn13"])
        for field in ISBN_FIELDS:
            if result[field] is not None:
                claims[field].setdefault(result[field], set()).add(book_id)
    disputed = {field: {value for value, ids in claims[field].items()
                        if len(ids) > 1 and value not in index.owners(field)}
                for field in ISBN_FIELDS}
    return rejections, flags, disputed


# ---------------------------------------------------------------------------
# One CSV row
# ---------------------------------------------------------------------------

def validate_essentials(row):
    """R05. Returns a list of problems (empty when the row is usable)."""
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


def process_row(conn, index, row, line, seen_rows, plan):
    rejections, flags, disputed = plan
    entry = {"line": line, "book_id": row.get("book_id"), "title": row.get("title"), "events": []}

    if is_malformed(row):  # R38
        n = sum(v is not None for k, v in row.items() if k is not None) + len(row.get(None) or [])
        entry.update(outcome="rejected", reason=f"R38: row has {n} fields, expected {len(COLUMNS)}",
                     original_values=row_for_log(row))
        return entry

    problems = validate_essentials(row)
    if problems:
        entry.update(outcome="rejected", reason="R05: " + "; ".join(problems),
                     original_values=row_for_log(row))
        return entry

    book_id = int(row["book_id"].strip())
    entry["book_id"] = book_id
    entry["raw_isbns"] = {"isbn": row["isbn"], "isbn13": row["isbn13"]}
    raw_key = tuple(row[c] for c in COLUMNS)
    duplicate_of = seen_rows.get(raw_key)
    seen_rows.setdefault(raw_key, line)
    if duplicate_of is not None:  # R23
        reason = f"exact duplicate in input (same as line {duplicate_of})"
        entry.update(outcome="skipped", reason=reason, events=[event("duplicate", "R23", "row", reason)])
        return entry

    # C6: same book_id earlier in this file with values that normalize to the same result.
    normalized_key = ("normalized", normalized_form(row))
    normalized_of = seen_rows.get(normalized_key)
    seen_rows.setdefault(normalized_key, line)
    if normalized_of is not None:
        reason = f"normalized duplicate in input (same as line {normalized_of} after normalization)"
        entry.update(outcome="skipped", reason=reason, events=[event("duplicate", "C6", "row", reason)])
        return entry

    if line in rejections:
        entry.update(outcome="rejected", reason=rejections[line], original_values=row_for_log(row),
                     events=[event("review", rejections[line][:3], "row", rejections[line])])
        return entry

    stored = conn.execute(f"SELECT {', '.join(DB_COLUMNS)} FROM books WHERE book_id = ?", (book_id,)).fetchone()
    if stored is not None:
        entry = process_existing(conn, index, row, entry, dict(zip(DB_COLUMNS, stored)), disputed)
    else:
        entry = process_new(conn, index, row, entry, book_id, disputed)
    entry["events"] = flags.get(line, []) + entry["events"]
    return entry


def row_for_log(row):
    values = {c: row.get(c) for c in COLUMNS if c in row}
    if row.get(None):
        values["extra_fields"] = row[None]
    return values


def reject(entry, row, reason, events):
    entry.update(outcome="rejected", reason=reason, original_values=row_for_log(row), events=events)
    return entry


def process_new(conn, index, row, entry, book_id, disputed):
    events = []
    gid = clean_goodreads_id(row["goodreads_book_id"])
    if gid is None:
        events.append(event("warning", "R06", "goodreads_book_id",
                            "Goodreads ID is missing or not a positive whole number; secondary identity "
                            "unavailable", row["goodreads_book_id"]))
    elif index.by_goodreads.get(gid, book_id) != book_id:
        other = index.by_goodreads[gid]
        return reject(entry, row, f"R08: goodreads_book_id {gid} already identifies book_id {other}; "
                                  "existing book preserved",
                      [compare_event("review", "R08", "goodreads_book_id", "Goodreads identity collision",
                                     row["goodreads_book_id"], f"book_id {other}")])

    result = process_isbns(row["isbn"], row["isbn13"])
    events += result["events"] + check_assigned_elsewhere(result, book_id, index, disputed)
    events += missing_isbn_warnings(result)
    if result["pair_conflict"]:
        events.append(event("review", "R21", "isbn/isbn13",
                            "new book kept with both cleaned ISBNs NULL; decide which ISBN is correct"))

    key = (row["title"].strip(), row["authors"].strip())
    others = sorted(index.by_identity.get(key, set()) - {book_id})
    if others:
        events.append(event("warning", "R09", "title/authors",
                            f"possible duplicate: same title and author as book_id {', '.join(map(str, others))}; "
                            "kept as a separate record"))

    values = {c: to_null(row[c]) for c in COLUMNS}
    values["book_id"] = book_id
    values["isbn"], values["isbn13"] = row["isbn"], row["isbn13"]  # R03: raw text exactly as received
    values[CLEAN["isbn"]], values[CLEAN["isbn13"]] = result["isbn"], result["isbn13"]
    conn.execute(f"INSERT INTO books ({', '.join(DB_COLUMNS)}) VALUES ({', '.join('?' * len(DB_COLUMNS))})",
                 [values[c] for c in DB_COLUMNS])
    index.add(book_id, result["isbn"], result["isbn13"], values["title"], values["authors"],
              row["goodreads_book_id"])

    entry.update(outcome="loaded", reason="new book inserted", events=events,
                 stored={"isbn": result["isbn"], "isbn13": result["isbn13"]})
    return entry


def process_existing(conn, index, row, entry, stored, disputed):
    book_id = entry["book_id"]

    # R07: identity must agree after trimming, or the row is rejected.
    mismatched = [c for c in ("title", "authors") if not same_identity_text(row[c], stored[c])]
    if mismatched:
        return reject(entry, row, f"R07: book_id {book_id} already exists with a different "
                                  f"{' and '.join(mismatched)}; existing record preserved",
                      [compare_event("review", "R07", c, "identity conflict: incoming value differs from "
                                     "stored value", row[c], stored[c]) for c in mismatched])

    events = []
    incoming_gid, stored_gid = clean_goodreads_id(row["goodreads_book_id"]), clean_goodreads_id(
        stored["goodreads_book_id"])
    if incoming_gid is not None and stored_gid is not None and incoming_gid != stored_gid:
        return reject(entry, row, f"R07: book_id {book_id} already exists with a different Goodreads ID; "
                                  "existing record preserved",
                      [compare_event("review", "R07", "goodreads_book_id", "Goodreads identity conflict",
                                     row["goodreads_book_id"], stored["goodreads_book_id"])])
    if incoming_gid is not None and index.by_goodreads.get(incoming_gid, book_id) != book_id:
        other = index.by_goodreads[incoming_gid]
        return reject(entry, row, f"R08: goodreads_book_id {incoming_gid} already identifies book_id {other}; "
                                  "existing books preserved",
                      [compare_event("review", "R08", "goodreads_book_id", "Goodreads identity collision",
                                     row["goodreads_book_id"], f"book_id {other}")])
    if incoming_gid is None:
        events.append(event("warning", "R06", "goodreads_book_id",
                            "Goodreads ID is missing or not a positive whole number; stored value kept",
                            row["goodreads_book_id"]))

    result = process_isbns(row["isbn"], row["isbn13"])
    events += check_assigned_elsewhere(result, book_id, index, disputed)
    current = {f: stored[CLEAN[f]] for f in ISBN_FIELDS}
    entry["stored"] = dict(current)

    # R21: a conflicting incoming pair may not replace accepted ISBNs.
    if result["pair_conflict"]:
        pair = " | ".join(v or "" for v in result["pair_conflict"])
        if any(current.values()):
            return reject(entry, row, "R21: incoming ISBN pair conflicts with itself and cannot replace the "
                                      "accepted ISBNs; existing record preserved",
                          result["events"] + events + [event("review", "R21", "isbn/isbn13",
                                                             "incoming ISBN pair conflicts; stored ISBNs preserved",
                                                             pair, f"{current['isbn']} | {current['isbn13']}")])
        events.append(event("review", "R21", "isbn/isbn13", "incoming ISBN pair conflicts; no ISBN accepted", pair))

    # R26: a different validated ISBN for a field that already has one is rejected for review.
    differing = [f for f in ISBN_FIELDS
                 if result[f] is not None and current[f] is not None and result[f] != current[f]]
    if differing:
        return reject(entry, row, f"R26: incoming validated {' and '.join(differing)} differs from the accepted "
                                  "value; existing record preserved and no fields updated",
                      result["events"] + events + [compare_event(
                          "conflict", "R26", f, "incoming validated ISBN differs from the stored validated ISBN",
                          result[f], current[f]) for f in differing])

    # R25: fill missing cleaned ISBNs with validated values; never erase good values.
    updates, changes = {}, []
    fills = {}
    for field in ISBN_FIELDS:
        incoming = result[field]
        if incoming is None and current[field] is not None and to_null(row[field]) is not None:
            events.append(compare_event("warning", "R25", field,
                                        "incoming value is unusable; stored valid value kept",
                                        row[field], current[field]))
        if incoming is not None and current[field] is None:
            fills[field] = incoming
    new10 = fills.get("isbn", current["isbn"])
    new13 = fills.get("isbn13", current["isbn13"])
    if fills and new10 is not None and new13 is not None and isbn10_to_13(new10) != new13:
        # R26 (C8): an ISBN that would fill an empty field but contradicts the book's other
        # accepted ISBN is rejected for review; nothing on the book changes.
        conflicts = []
        for field, value in fills.items():
            other = "isbn13" if field == "isbn" else "isbn"
            conflicts.append(compare_event(
                "conflict", "R26", field,
                f"incoming validated {field} {value} does not correspond to the accepted {other} "
                f"{current[other]}", value, current[other]))
        return reject(entry, row, "R26: incoming validated ISBN contradicts the accepted ISBN in the other "
                                  "field; existing record preserved and no fields updated",
                      result["events"] + events + conflicts)
    for field, value in fills.items():
        updates[CLEAN[field]] = value
        changes.append(change_event("R25", CLEAN[field], "filled missing cleaned ISBN with a validated value",
                                    current[field], value))

    # R27: approved metadata changes (exact text comparison; never replace a value with a blank).
    for c in APPROVED_UPDATES:
        incoming = to_null(row[c])
        if incoming is not None and incoming != stored[c]:
            updates[c] = incoming
            changes.append(change_event("R27", c, "approved metadata updated", stored[c], incoming))

    # R28: protected fields are never changed; differences are recorded for review.
    for c in PROTECTED_FOR_REVIEW:
        if c == "goodreads_book_id" and (incoming_gid is None or incoming_gid == stored_gid):
            continue
        if to_null(row[c]) != stored[c]:
            events.append(compare_event("review", "R28", c,
                                        "incoming value differs from stored value; stored value kept",
                                        row[c], stored[c]))

    if updates:
        conn.execute(f"UPDATE books SET {', '.join(f'{f} = ?' for f in updates)} WHERE book_id = ?",
                     [*updates.values(), book_id])
        index.add_isbns(book_id, fills.get("isbn"), fills.get("isbn13"))
        corrections = [e for e in result["events"] if e["type"] == "correction" and e["field"] in fills]
        entry.update(outcome="updated",
                     reason=f"existing book updated: {', '.join(updates)}",
                     events=corrections + events + changes)
        return entry

    entry.update(outcome="skipped", reason=f"already exists: book_id {book_id} has no permitted change",
                 events=events)
    return entry


# ---------------------------------------------------------------------------
# Run log (R33)
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


def file_sha256(path):
    """R33: SHA-256 of the readable source bytes, or None if the file cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


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
    assert sum(counts.values()) == rows_read, "every row must have exactly one outcome"  # R32
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
    sha = file_sha256(source)
    run = {
        "run_number": len(runs) + 1,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_file": source.name,
        "source_path": str(source.resolve()),
        "source_sha256": sha if sha is not None else "unavailable (file could not be read)",
        "database": str(DB_PATH),
    }

    try:
        with open(source, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            if sorted(header) != sorted(COLUMNS):
                missing = [c for c in COLUMNS if c not in header]
                extra = [c for c in header if c not in COLUMNS]
                duplicated = sorted({c for c in header if header.count(c) > 1})
                raise ValueError(f"unexpected CSV columns (missing: {missing or 'none'}; "
                                 f"unexpected: {extra or 'none'}; duplicated: {duplicated or 'none'})")
            records = []
            start = 2
            for row in reader:
                records.append((start, row))
                start = reader.line_num + 1
    except (OSError, UnicodeDecodeError, ValueError, csv.Error) as exc:
        record_failure(runs, run, f"could not read {source}: {exc}")
        return 1

    try:
        conn = open_database()
    except (SchemaError, sqlite3.Error) as exc:
        record_failure(runs, run, f"database not usable: {exc}")
        return 1
    try:
        index = Index(conn)
        plan = plan_delivery(conn, index, records)
        seen_rows = {}
        entries = [process_row(conn, index, row, line, seen_rows, plan) for line, row in records]
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
