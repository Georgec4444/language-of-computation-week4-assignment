"""One test per clarification example in section 8 of rules.md (version 1.1 draft, C1-C9).

Version 1.1 is approved by George for his implementation; Asha has not yet confirmed it.
Each test name starts with the clarification number. Unless an example says otherwise, the
input is a single new book on CSV line 2 of a fresh test.db (rules.md, section 7 "How to read
the table", which section 8 reuses). All runs use the isolated workspace from conftest.py.
"""

import json

import pytest

from conftest import (
    FLAG, REPAIR, UPDATE, WARNING, assert_has_event, cleaned, events_for, find_events,
    make_row, outcomes, row_entry,
)

LINE = 2  # first data row of a CSV (R34)


def mentions(event, *values):
    """True if every value appears somewhere in the logged event (any key)."""
    text = json.dumps(event)
    return all(v in text for v in values)


def load_single(workspace, book_id, raw10, raw13):
    """Ingest one new book and return (run, stored book)."""
    row = make_row(book_id, isbn=raw10, isbn13=raw13)
    run = workspace.run(workspace.write_csv([row]))
    assert run["status"] == "completed"
    assert run["rows_read"] == 1
    return run, workspace.book(book_id)


def assert_not_derived_from_isbn10(run, line, derived13):
    """No repair may produce the 978 ISBN-13 derived from the row's ISBN-10."""
    derivations = [ev for ev in find_events(run, line, REPAIR) if mentions(ev, derived13)]
    assert derivations == [], f"ISBN-13 {derived13} must not be derived; logged: {derivations}"


def assert_c3_contradiction(run, book, raw13):
    """C3 / R19 safeguard 5: loaded (not rejected), valid ISBN-10 kept, ISBN-13 NULL, raw
    value preserved, explicit review flag, and no 978 derivation."""
    assert run["counts"] == outcomes(loaded=1), "C3: the row must not be rejected for this issue alone"
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == raw13
    assert_has_event(run, LINE, FLAG, "isbn13", what="explicit review flag for the 979/9790 contradiction")
    assert_not_derived_from_isbn10(run, LINE, "9780439139601")


# ---------------------------------------------------------------------------
# C1 - rounded versus exact scientific notation (R16, R19 safeguard 1)
# ---------------------------------------------------------------------------

def test_c1_rounded_non_isbn_scientific_notation_is_not_derived(workspace):
    """C1: 6.4557300301e+11 shows 11 of 12 digits, so it is rounded; R16 cannot pass, so no
    ISBN-13 is derived from the valid ISBN-10. Warn. (The exact form is example 17.)"""
    run, book = load_single(workspace, 5101, "189221301X", "6.4557300301e+11")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("189221301X", None)
    assert book["isbn13"] == "6.4557300301e+11"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the rounded value")
    assert_not_derived_from_isbn10(run, LINE, "9781892213013")


# ---------------------------------------------------------------------------
# C2 - unconfirmed decimal-looking isbn13 (R17, R19 safeguard 6)
# ---------------------------------------------------------------------------

def test_c2a_unconfirmed_dot_zero_with_different_isbn10_is_not_derived(workspace):
    """C2a: 0439139601.0 is not the same ISBN-10 as isbn; R17 does not confirm it, and R19
    must not derive an ISBN-13 instead. ISBN-10 kept; ISBN-13 NULL; warning."""
    run, book = load_single(workspace, 5201, "0439139600", "0439139601.0")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == "0439139601.0"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the unconfirmed decimal value")
    assert_not_derived_from_isbn10(run, LINE, "9780439139601")


def test_c2b_thirteen_digit_decimal_is_not_derived(workspace):
    """C2b: 9780439139601.0 is not ISBN-10-shaped, so it is invalid under R17; no ISBN-13 is
    derived. ISBN-10 kept; ISBN-13 NULL; warning."""
    run, book = load_single(workspace, 5202, "0439139600", "9780439139601.0")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == "9780439139601.0"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the decimal value")
    assert_not_derived_from_isbn10(run, LINE, "9780439139601")


# ---------------------------------------------------------------------------
# C3 - invalid 979 or 9790 beside a valid ISBN-10 (R19 safeguard 5, R11) - changed in 1.1
# ---------------------------------------------------------------------------

def test_c3a_bad_checksum_979_is_a_contradiction(workspace):
    """C3a: 9798351220410 (979, bad checksum) cannot agree with a 978 derivation."""
    run, book = load_single(workspace, 5301, "0439139600", "9798351220410")
    assert_c3_contradiction(run, book, "9798351220410")


def test_c3b_9790_ismn_is_a_contradiction(workspace):
    """C3b: 9790439139601 is an ISMN (R11), not an ISBN, and contradicts a 978 derivation."""
    run, book = load_single(workspace, 5302, "0439139600", "9790439139601")
    assert_c3_contradiction(run, book, "9790439139601")


@pytest.mark.parametrize("raw13", [
    pytest.param("9.790439139601e+12", id="c3c-exact-represents-9790"),
    pytest.param("9.7904391396e+12", id="c3d-rounded-begins-979"),
])
def test_c3_scientific_notation_does_not_bypass_the_safeguard(workspace, raw13):
    """C3c/C3d: exact scientific notation that unambiguously represents a 9790 value is treated
    like that value; rounded notation beginning 979 is ambiguous, so missing digits are not
    assumed and no replacement is derived. Either way: ISBN-13 NULL, review flag, not rejected."""
    run, book = load_single(workspace, 5303, "0439139600", raw13)
    assert_c3_contradiction(run, book, raw13)


# ---------------------------------------------------------------------------
# C4 - conflicting pair for an existing book with no accepted ISBNs (R21)
# ---------------------------------------------------------------------------

def test_c4_conflicting_pair_for_isbn_less_book_is_skipped_with_review_flag(workspace):
    """C4: nothing to replace, so not rejected; accept neither ISBN; skipped with a review flag
    because no approved field changes."""
    workspace.run(workspace.write_csv([make_row(5401)], "first.csv"))
    before = workspace.book(5401)
    assert cleaned(before) == (None, None)

    run = workspace.run(workspace.write_csv(
        [make_row(5401, isbn="1555550126", isbn13="9781555550134")], "second.csv"))

    assert run["counts"] == outcomes(skipped=1)
    assert cleaned(workspace.book(5401)) == (None, None)
    assert_has_event(run, LINE, FLAG, what="review flag for the conflicting pair")


def test_c4_conflicting_pair_with_metadata_change_is_updated_without_isbns(workspace):
    """C4 with an approved metadata change: updated (R27), but neither ISBN is accepted and the
    conflicting pair is still flagged."""
    workspace.run(workspace.write_csv([make_row(5402, ratings_count="100")], "first.csv"))

    run = workspace.run(workspace.write_csv(
        [make_row(5402, isbn="1555550126", isbn13="9781555550134", ratings_count="150")], "second.csv"))

    assert run["counts"] == outcomes(updated=1)
    after = workspace.book(5402)
    assert cleaned(after) == (None, None)
    assert after["ratings_count"] == "150"
    assert_has_event(run, LINE, FLAG, what="review flag for the conflicting pair")
    changes = [ev for ev in find_events(run, LINE, UPDATE, "ratings_count")
               if ev.get("old") == "100" and ev.get("new") == "150"]
    assert changes, f"expected ratings_count change 100 -> 150 in the log; got {events_for(run, LINE)}"


# ---------------------------------------------------------------------------
# C5 - blank raw ISBN storage (R03)
# ---------------------------------------------------------------------------

def test_c5_blank_raw_isbn_is_preserved_as_empty_text(workspace):
    """C5: a blank raw isbn is stored as empty text, not NULL; raw isbn13 stored exactly; the
    blank cell supplies no cleaned ISBN (a 979 ISBN-13 has no ISBN-10 counterpart)."""
    run, book = load_single(workspace, 5501, "", "9798351220413")

    assert run["counts"] == outcomes(loaded=1)
    assert book["isbn"] is not None, "blank raw isbn must be empty text, not NULL"
    assert book["isbn"] == ""
    assert book["isbn13"] == "9798351220413"
    assert cleaned(book) == (None, "9798351220413")


# ---------------------------------------------------------------------------
# C6 - formatting-only repeat in one delivery (R23, R24)
# ---------------------------------------------------------------------------

def test_c6_formatting_only_repeat_in_one_file_is_skipped_as_already_exists(workspace):
    """C6: the second row differs only by formatting, so it is not an exact duplicate; it is
    compared with the record accepted from line 2 and skipped as 'already exists'."""
    first = make_row(5601, isbn="0-439-13960-0")
    second = make_row(5601, isbn="0439139600")

    run = workspace.run(workspace.write_csv([first, second]))

    assert run["rows_read"] == 2
    assert run["counts"] == outcomes(loaded=1, skipped=1)
    assert workspace.book_count() == 1
    assert cleaned(workspace.book(5601)) == ("0439139600", "9780439139601")
    repeat = row_entry(run, 3)
    assert repeat is not None and repeat["outcome"] == "skipped"
    assert "already exists" in repeat["reason"]
    assert "exact duplicate in input" not in repeat["reason"]


# ---------------------------------------------------------------------------
# C7 - exact copies of a rejected row (R23)
# ---------------------------------------------------------------------------

def test_c7_exact_copy_of_rejected_row_is_skipped_as_exact_duplicate(workspace):
    """C7: the first copy is rejected (R26: different validated ISBN); the identical second
    copy is skipped as 'exact duplicate in input'. The existing book is unchanged."""
    workspace.run(workspace.write_csv([make_row(5701, isbn="0439139600")], "first.csv"))
    before = workspace.book(5701)

    conflicting = make_row(5701, isbn="1620014750")
    run = workspace.run(workspace.write_csv([conflicting, dict(conflicting)], "second.csv"))

    assert run["rows_read"] == 2
    assert run["counts"] == outcomes(rejected=1, skipped=1)
    assert workspace.book(5701) == before
    first, second = row_entry(run, 2), row_entry(run, 3)
    assert first is not None and first["outcome"] == "rejected"
    assert second is not None and second["outcome"] == "skipped"
    assert "exact duplicate in input" in second["reason"]


def test_c7_structural_and_required_identity_failures_are_rejected_every_time(workspace):
    """C7 exception: identical rows failing R05 (blank title) and identical rows failing R38
    (wrong field count) are rejected each time they appear, not skipped as duplicates."""
    blank_title = make_row(5702, title="")
    malformed = "5703,905703,905703"  # 3 fields, expected 23

    run = workspace.run(workspace.write_csv([blank_title, dict(blank_title)], raw_lines=[malformed, malformed]))

    assert run["rows_read"] == 4
    assert run["counts"] == outcomes(rejected=4)
    assert workspace.book_count() == 0
    for line in (2, 3, 4, 5):
        entry = row_entry(run, line)
        assert entry is not None and entry["outcome"] == "rejected", f"line {line} must be rejected"


# ---------------------------------------------------------------------------
# C8 - new ISBN contradicting an already accepted ISBN (R25, R26) - changed in 1.1
# ---------------------------------------------------------------------------

def _load_book_with_isbn13_only(workspace, book_id):
    """Stored cleaned ISBN-10 NULL (bad check digit, R19 safeguard 4) and ISBN-13 9780062398123."""
    workspace.run(workspace.write_csv(
        [make_row(book_id, isbn="1854433017", isbn13="9780062398123", ratings_count="100")], "first.csv"))
    before = workspace.book(book_id)
    assert cleaned(before) == (None, "9780062398123")
    return before


def _assert_contradiction_logged(run):
    flags = [ev for ev in find_events(run, LINE, FLAG) if mentions(ev, "0439139600", "9780062398123")]
    assert flags, ("expected a logged conflict naming the incoming 0439139600 and the stored "
                   f"9780062398123; got {events_for(run, LINE)}")


def test_c8_contradicting_fill_is_rejected_for_review(workspace):
    """C8: 0439139600 would fill the empty ISBN-10 but contradicts the stored ISBN-13; reject for
    review, keep the book unchanged, and log the conflict with incoming and stored values."""
    before = _load_book_with_isbn13_only(workspace, 5801)

    run = workspace.run(workspace.write_csv(
        [make_row(5801, isbn="0439139600", isbn13="195170342.0", ratings_count="100")], "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    assert workspace.book(5801) == before
    entry = row_entry(run, LINE)
    assert entry is not None and entry["outcome"] == "rejected"
    _assert_contradiction_logged(run)


def test_c8_contradicting_fill_updates_no_metadata(workspace):
    """C8: the rejected row's approved metadata change (ratings_count) is not applied either."""
    before = _load_book_with_isbn13_only(workspace, 5802)

    run = workspace.run(workspace.write_csv(
        [make_row(5802, isbn="0439139600", isbn13="195170342.0", ratings_count="999")], "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    after = workspace.book(5802)
    assert after == before
    assert after["ratings_count"] == "100"
    assert find_events(run, LINE, UPDATE) == [], "no field may be updated from a rejected row"
    _assert_contradiction_logged(run)


# ---------------------------------------------------------------------------
# C9 - valid ISBN-10 in isbn13 beside an invalid isbn (R13, R19 safeguard 4)
# ---------------------------------------------------------------------------

def test_c9_valid_isbn10_in_isbn13_is_moved_beside_invalid_isbn(workspace):
    """C9: move the valid ISBN-10 from isbn13 (R13) and derive its 978 ISBN-13; warn about the
    invalid isbn; log the move and the derivation; keep both raw values."""
    run, book = load_single(workspace, 5901, "1854433017", "0439139600")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", "9780439139601")
    assert book["isbn"] == "1854433017"
    assert book["isbn13"] == "0439139600"
    assert_has_event(run, LINE, WARNING, "isbn", what="warning for the invalid isbn value")
    repairs = find_events(run, LINE, REPAIR)
    assert len(repairs) >= 2, f"expected the move and the derivation to be logged; got {repairs}"
    assert any(mentions(ev, "9780439139601") for ev in repairs), \
        f"expected the derived ISBN-13 9780439139601 in a logged repair; got {repairs}"
