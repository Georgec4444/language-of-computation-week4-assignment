"""Whole-run behavior required by Professor Díaz, traced to rules.md:

* the same file twice creates no duplicate books (R23, R39)
* every run appends to the log without overwriting earlier entries (R33)
* loaded + updated + skipped + rejected = rows_read (R32)
* repeating ingestion produces the same database contents (R39)

The real books.csv and books-batch2.csv are only read; all outputs go to test.db and a
temporary log (R40).
"""

import copy
import csv

import pytest

from conftest import REAL_BATCH2, REAL_BOOKS, make_row, outcomes

THREE_RUN_SEQUENCE = (REAL_BOOKS, REAL_BOOKS, REAL_BATCH2)  # R39


def data_rows_in(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return sum(1 for record in csv.reader(f) if record) - 1  # minus the header


def assert_counts_add_up(run):
    counts = run["counts"]
    assert set(counts) == {"loaded", "updated", "skipped", "rejected"}
    assert sum(counts.values()) == run["rows_read"], f"{counts} do not add up to rows_read {run['rows_read']}"


# ---------------------------------------------------------------------------
# Same file twice: no duplicates (R23, R39)
# ---------------------------------------------------------------------------

def test_same_file_twice_creates_no_duplicate_books(workspace):
    first = workspace.run(REAL_BOOKS)
    books_after_first = workspace.book_count()
    assert books_after_first == first["counts"]["loaded"]

    second = workspace.run(REAL_BOOKS)

    assert workspace.book_count() == books_after_first
    assert second["counts"]["loaded"] == 0, "second run of the same file inserted books"
    assert second["counts"]["updated"] == 0, "second run of the same file made spurious updates"
    ids = [b["book_id"] for b in workspace.all_books()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Repeatability: same database contents (R39)
# ---------------------------------------------------------------------------

def test_repeating_the_same_file_leaves_database_contents_identical(workspace):
    workspace.run(REAL_BOOKS)
    after_first = workspace.all_books()

    workspace.run(REAL_BOOKS)

    assert workspace.all_books() == after_first


def test_fresh_starts_with_same_inputs_produce_identical_databases(make_workspace):
    """R39: an identical fresh start with the same input and rules gives the same logical database."""
    results = []
    for _ in range(2):
        ws = make_workspace()
        for path in THREE_RUN_SEQUENCE:
            ws.run(path)
        results.append(ws.all_books())

    assert results[0], "the three-run sequence stored no books"
    assert results[0] == results[1]


# ---------------------------------------------------------------------------
# Append-only log (R33)
# ---------------------------------------------------------------------------

def test_every_run_appends_to_the_log_without_overwriting(workspace):
    """Successful runs and a failed file-level run (R33, R37) each append exactly one record,
    and every earlier record stays exactly as it was."""
    good = workspace.write_csv([make_row(1, isbn="0439139600"), make_row(2)], "good.csv")
    missing = workspace.folder / "does-not-exist.csv"

    history = []
    for path in (good, good, missing, good):
        workspace.run(path)
        runs = workspace.runs()
        assert len(runs) == len(history) + 1, "a run did not append exactly one record"
        assert runs[:len(history)] == history, "an earlier log record was changed or removed"
        history = copy.deepcopy(runs)

    assert [r["status"] for r in history] == ["completed", "completed", "failed", "completed"]


# ---------------------------------------------------------------------------
# Counts add up (R32)
# ---------------------------------------------------------------------------

def test_counts_add_up_to_rows_read_for_the_three_run_sequence(workspace):
    for path in THREE_RUN_SEQUENCE:
        run = workspace.run(path)
        assert run["status"] == "completed"
        assert run["rows_read"] == data_rows_in(path)
        assert_counts_add_up(run)


def test_counts_add_up_when_all_four_outcomes_occur(workspace):
    """One delivery producing every outcome: loaded (new book, R32), updated (approved metadata,
    R27), skipped (unchanged repeat and exact duplicate, R23/R29), rejected (blank title, R05;
    wrong field count, R38)."""
    existing_a = make_row(1, isbn="0439139600")
    existing_b = make_row(2, isbn="1620014750")
    workspace.run(workspace.write_csv([existing_a, existing_b], "first.csv"))

    new_book = make_row(3, isbn="1528746309")
    rows = [
        new_book,                                   # loaded
        make_row(1, isbn="0439139600", ratings_count="150"),  # updated
        existing_b,                                 # skipped: unchanged
        dict(new_book),                             # skipped: exact duplicate in input
        make_row(4, title=""),                      # rejected: blank title
    ]
    malformed = "5,905,905"                         # rejected: 3 fields, expected 23
    run = workspace.run(workspace.write_csv(rows, "second.csv", raw_lines=[malformed]))

    assert run["status"] == "completed"
    assert run["rows_read"] == 6
    assert run["counts"] == outcomes(loaded=1, updated=1, skipped=2, rejected=2)
    assert_counts_add_up(run)
