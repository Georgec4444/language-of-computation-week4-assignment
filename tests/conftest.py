"""Shared fixtures and helpers for the ingest.py test suite.

Every test runs ingest.py against its own temporary folder that holds `test.db` and a
temporary ingestion log, never the production `books.db` or `ingestion-log.json` (R40).
Each folder is deleted when its test finishes, whether the test passed or failed (R40).

Implementation choices the rules leave to each partner (R30, R33), agreed by George:
  * books table keeps the 23 supplier columns as received (raw `isbn` / `isbn13`), and the
    cleaned ISBNs live in separate columns named by CLEAN10 / CLEAN13 below.
  * each run in the log has `status`, `rows_read`, `counts` (loaded/updated/skipped/rejected)
    and `rows`; each row entry has `line`, `outcome`, `reason`, and `events`, where an event
    has `type` (warning / correction / conflict / review / update), `field`, and for updates
    `old` / `new`.
If either layout changes, update the constants and helpers in this file only.
"""

import csv
import functools
import gc
import hashlib
import inspect
import json
import shutil
import sqlite3
import sys
import tempfile
import types
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ingest  # noqa: E402  (must come after the sys.path change)

# R02: the 23 required supplier columns, taken from rules.md (not from ingest.py).
COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count", "work_ratings_count",
    "work_text_reviews_count", "ratings_1", "ratings_2", "ratings_3", "ratings_4",
    "ratings_5", "image_url", "small_image_url",
]

# R30: cleaned ISBNs are stored separately from the raw supplier text.
CLEAN10 = "isbn10_clean"
CLEAN13 = "isbn13_clean"

# Log event types, grouped by what the rules.md examples ask for.
WARNING = {"warning"}
REPAIR = {"correction"}
FLAG = {"conflict", "review"}
UPDATE = {"update"}

REAL_BOOKS = ROOT / "books.csv"
REAL_BATCH2 = ROOT / "books-batch2.csv"

# Files in the project folder that tests must never change (R01, R40).
PRODUCTION_FILES = ("books.db", "ingestion-log.json", "books.csv", "books-batch2.csv")


# ---------------------------------------------------------------------------
# Building input rows
# ---------------------------------------------------------------------------

def make_row(book_id, **overrides):
    """One supplier row. Per the note above the rules.md examples table, defaults describe a
    new book with a valid positive book_id, nonblank title/authors, and its own unique
    Goodreads ID. ISBN fields default to blank."""
    n = int(book_id) if str(book_id).isdigit() else 0
    row = {
        "book_id": str(book_id),
        "goodreads_book_id": str(900000 + n),
        "best_book_id": str(900000 + n),
        "work_id": str(800000 + n),
        "books_count": "1",
        "isbn": "",
        "isbn13": "",
        "authors": f"Test Author {book_id}",
        "original_publication_year": "2000",
        "original_title": f"Test Book {book_id}",
        "title": f"Test Book {book_id}",
        "language_code": "eng",
        "average_rating": "4.00",
        "ratings_count": "100",
        "work_ratings_count": "100",
        "work_text_reviews_count": "10",
        "ratings_1": "20",
        "ratings_2": "20",
        "ratings_3": "20",
        "ratings_4": "20",
        "ratings_5": "20",
        "image_url": f"https://example.com/{book_id}.jpg",
        "small_image_url": f"https://example.com/{book_id}-small.jpg",
    }
    unknown = set(overrides) - set(row)
    if unknown:
        raise ValueError(f"not supplier columns: {sorted(unknown)}")
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Fail-fast safety checks (R40): run before every ingestion
# ---------------------------------------------------------------------------

TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
PATH_SETTINGS = ("HERE", "DB_PATH", "LOG_PATH")


def _is_inside(path, folder):
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents


def _ingest_code_objects():
    """Every function and method body defined in ingest.py, including nested ones."""
    stack = []
    for obj in vars(ingest).values():
        if inspect.isfunction(obj) and obj.__module__ == ingest.__name__:
            stack.append(obj.__code__)
        elif inspect.isclass(obj) and obj.__module__ == ingest.__name__:
            stack.extend(v.__code__ for v in vars(obj).values() if inspect.isfunction(v))
    while stack:
        code = stack.pop()
        yield code
        stack.extend(c for c in code.co_consts if isinstance(c, types.CodeType))


@functools.lru_cache(maxsize=None)
def ingest_redirection_problems():
    """Check, without running anything, that ingest.py finds its output files only through the
    module-level HERE / DB_PATH / LOG_PATH settings, so the redirection in Workspace.run takes
    effect. Returns a list of problems (empty when safe)."""
    problems = [f"ingest.py has no module-level {name} setting" for name in PATH_SETTINGS
                if not hasattr(ingest, name)]
    names, strings = set(), set()
    for code in _ingest_code_objects():
        names.update(code.co_names)
        strings.update(c for c in code.co_consts if isinstance(c, str))
    if "DB_PATH" not in names:
        problems.append("no function in ingest.py reads DB_PATH, so the database location cannot be redirected")
    if "LOG_PATH" not in names:
        problems.append("no function in ingest.py reads LOG_PATH, so the log location cannot be redirected")
    if "__file__" in names:
        problems.append("a function in ingest.py builds a path from __file__ instead of HERE")
    if names & {"getcwd", "cwd", "chdir"}:
        problems.append("a function in ingest.py uses the current working directory for paths")
    for literal in sorted(strings & {"books.db", "ingestion-log.json"}):
        problems.append(f"a function in ingest.py hard-codes the production file name {literal!r}")
    return problems


def assert_isolated(folder):
    """Stop the test before ingestion unless every output path points into this test's own
    temporary folder, which must be under the system temp directory and outside the project."""
    problems = list(ingest_redirection_problems())
    folder = Path(folder).resolve()
    if not _is_inside(folder, TEMP_ROOT) or folder == TEMP_ROOT:
        problems.append(f"test folder {folder} is not a subfolder of the system temp directory {TEMP_ROOT}")
    if _is_inside(folder, ROOT) or _is_inside(ROOT, folder):
        problems.append(f"test folder {folder} overlaps the project folder {ROOT}")
    expected = {"HERE": folder, "DB_PATH": folder / "test.db", "LOG_PATH": folder / "test-ingestion-log.json"}
    for name, want in expected.items():
        actual = getattr(ingest, name, None)
        if actual is None or Path(actual).resolve() != want:
            problems.append(f"ingest.{name} is {actual}, expected {want}")
    for name in PRODUCTION_FILES:
        for setting in ("DB_PATH", "LOG_PATH"):
            actual = getattr(ingest, setting, None)
            if actual is not None and Path(actual).resolve() == (ROOT / name).resolve():
                problems.append(f"ingest.{setting} points at the production file {name}")
    if problems:
        pytest.fail("SAFETY CHECK FAILED; ingestion was not run:\n  - " + "\n  - ".join(problems),
                    pytrace=False)


# ---------------------------------------------------------------------------
# A temporary workspace: test.db + temporary log + temporary CSV inputs
# ---------------------------------------------------------------------------

class Workspace:
    def __init__(self, folder, monkeypatch):
        self.folder = folder
        self.db = folder / "test.db"
        self.log = folder / "test-ingestion-log.json"
        self._monkeypatch = monkeypatch

    def write_csv(self, rows, name="input.csv", raw_lines=()):
        """Write rows (dicts) under the 23-column header; raw_lines are appended verbatim."""
        path = self.folder / name
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            for line in raw_lines:
                f.write(line + "\r\n")
        return path

    def run(self, csv_path):
        """Run ingest.py's main() on csv_path with test.db and the temporary log, and return
        the run record it appended. monkeypatch raises if these names ever disappear from
        ingest.py, and assert_isolated() stops the test before ingestion unless every output
        path is inside this workspace's temporary folder."""
        self._monkeypatch.setattr(ingest, "HERE", self.folder)
        self._monkeypatch.setattr(ingest, "DB_PATH", self.db)
        self._monkeypatch.setattr(ingest, "LOG_PATH", self.log)
        assert_isolated(self.folder)
        ingest.main(["ingest.py", str(csv_path)])
        runs = self.runs()
        assert runs, "ingest.py did not append a run record to the log"
        return runs[-1]

    def runs(self):
        if not self.log.exists():
            return []
        return json.loads(self.log.read_text(encoding="utf-8"))

    def _query(self, sql, params=()):
        if not self.db.exists():
            return [], []
        with closing(sqlite3.connect(self.db)) as conn:
            cur = conn.execute(sql, params)
            names = [d[0] for d in cur.description]
            return names, cur.fetchall()

    def book(self, book_id):
        """The stored book as a dict, or None if it is not in test.db."""
        names, rows = self._query("SELECT * FROM books WHERE book_id = ?", (book_id,))
        return dict(zip(names, rows[0])) if rows else None

    def book_count(self):
        return self._query("SELECT COUNT(*) FROM books")[1][0][0] if self.db.exists() else 0

    def all_books(self):
        """Every stored book, ordered by book_id: the logical database content (R39)."""
        names, rows = self._query("SELECT * FROM books ORDER BY book_id")
        return [dict(zip(names, r)) for r in rows]


@pytest.fixture
def make_workspace(monkeypatch):
    """Factory for isolated workspaces. Every folder it creates is removed afterwards,
    even when the test fails (R40)."""
    folders = []

    def factory():
        folder = Path(tempfile.mkdtemp(prefix="ingest-test-"))
        folders.append(folder)
        return Workspace(folder, monkeypatch)

    try:
        yield factory
    finally:
        monkeypatch.undo()
        gc.collect()  # release any SQLite handles so Windows can delete test.db
        errors = []
        for folder in folders:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                errors.append(f"{folder}: {exc}")
        assert not errors, "could not clean up temporary test files: " + "; ".join(errors)


@pytest.fixture
def workspace(make_workspace):
    return make_workspace()


@pytest.fixture(scope="session", autouse=True)
def production_files_untouched():
    """R01/R40: the source CSVs, production books.db, and production ingestion-log.json must be
    byte-for-byte unchanged (or still absent) after the whole test session, and no stray files
    may be left in the project folder."""
    def snapshot():
        state = {}
        for name in PRODUCTION_FILES:
            path = ROOT / name
            state[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"
        state["project folder entries"] = sorted(
            p.name for p in ROOT.iterdir() if p.name not in ("__pycache__", ".pytest_cache"))
        return state

    problems = ingest_redirection_problems()
    if problems:
        pytest.exit("SAFETY CHECK FAILED before any test ran; ingest.py output cannot be safely "
                    "redirected:\n  - " + "\n  - ".join(problems), returncode=3)
    before = snapshot()
    yield
    after = snapshot()
    assert after == before, "the test session changed production files or left files in the project folder"


# ---------------------------------------------------------------------------
# Reading results
# ---------------------------------------------------------------------------

def outcomes(loaded=0, updated=0, skipped=0, rejected=0):
    return {"loaded": loaded, "updated": updated, "skipped": skipped, "rejected": rejected}


def cleaned(book):
    """(cleaned ISBN-10, cleaned ISBN-13) for a stored book."""
    assert book is not None, "expected the book to be stored in test.db"
    missing = [c for c in (CLEAN10, CLEAN13) if c not in book]
    if missing:
        pytest.fail(f"books table has no {missing} column(s); R30 requires cleaned ISBNs stored "
                    "separately from the raw supplier isbn/isbn13 text")
    return book[CLEAN10], book[CLEAN13]


def row_entry(run, line):
    """The log entry for a CSV line number (first data row is line 2, R34), or None."""
    for entry in run.get("rows", []):
        if entry.get("line") == line:
            return entry
    return None


def events_for(run, line):
    entry = row_entry(run, line)
    return entry.get("events", []) if entry else []


def find_events(run, line, kinds, field=None):
    return [ev for ev in events_for(run, line)
            if ev.get("type") in kinds and (field is None or ev.get("field") == field)]


def assert_has_event(run, line, kinds, field=None, what="event"):
    found = find_events(run, line, kinds, field)
    assert found, (f"expected a {what} (type {sorted(kinds)}"
                   f"{'' if field is None else f', field {field!r}'}) for CSV line {line}; "
                   f"logged events: {events_for(run, line)}")
