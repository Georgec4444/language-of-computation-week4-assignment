"""Tests for section 8 of rules.md, version 1.1: clarifications C1-C9 and examples 36-48.

Version 1.1 is the shared file agreed by George and Asha (byte-identical copies). Each test
name starts with the example number it checks; tests named only by a clarification (c2, c3,
c4, c7, c8) check a sentence of that clarification that no numbered example covers. Unless
an example says otherwise, the input is a single new book on CSV line 2 of a fresh test.db
(rules.md, section 7 "How to read the table", which section 8 reuses). All runs use the
isolated workspace from conftest.py.
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


def load_single(workspace, book_id, raw10, raw13, **overrides):
    """Ingest one new book and return (run, stored book)."""
    row = make_row(book_id, isbn=raw10, isbn13=raw13, **overrides)
    run = workspace.run(workspace.write_csv([row]))
    assert run["status"] == "completed"
    assert run["rows_read"] == 1
    return run, workspace.book(book_id)


def assert_not_derived(run, line, derived13):
    """No repair may produce this ISBN-13."""
    derivations = [ev for ev in find_events(run, line, REPAIR) if mentions(ev, derived13)]
    assert derivations == [], f"ISBN-13 {derived13} must not be derived; logged: {derivations}"


def assert_derived(run, line, derived13):
    derivations = [ev for ev in find_events(run, line, REPAIR) if mentions(ev, derived13)]
    assert derivations, f"expected a logged derivation of {derived13}; got {events_for(run, line)}"


# ---------------------------------------------------------------------------
# C1 - rounded versus exact scientific notation (R16, R19 safeguard 1)
# ---------------------------------------------------------------------------

def test_ex36_rounded_product_code_is_not_derived(workspace):
    """Example 36 / C1: 6.4557300301e+11 shows 11 of 12 digits, so it is rounded; no ISBN-13 is
    derived from the valid ISBN-10. Warn. (The exact form is example 17.)"""
    run, book = load_single(workspace, 3601, "189221301X", "6.4557300301e+11")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("189221301X", None)
    assert book["isbn13"] == "6.4557300301e+11"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the rounded value")
    assert_not_derived(run, LINE, "9781892213013")


def test_ex37_rounded_9790_value_is_not_derived(workspace):
    """Example 37 / C1, C3 (The Great Divorce): pad the ISBN-10; the rounded 9790 value follows
    C1, so no ISBN-13 is derived. Warn."""
    run, book = load_single(workspace, 3701, "6280560", "9.79000767239e+12", title="The Great Divorce")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0006280560", None)
    assert book["isbn"] == "6280560"
    assert book["isbn13"] == "9.79000767239e+12"
    assert_has_event(run, LINE, REPAIR, "isbn", what="logged leading-zero repair")
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the rounded 9790 value")
    assert_not_derived(run, LINE, "9780006280569")


# ---------------------------------------------------------------------------
# C2 - decimal-looking isbn13 values (R17, R19)
# ---------------------------------------------------------------------------

def test_ex38_unconfirmed_dot_zero_with_different_isbn10_is_not_derived(workspace):
    """Example 38 / C2: 0439139601.0 is not the row's ISBN-10; R17 does not confirm it, and R19
    must not derive an ISBN-13 instead. ISBN-10 kept; ISBN-13 NULL; warning."""
    run, book = load_single(workspace, 3801, "0439139600", "0439139601.0")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == "0439139601.0"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the unconfirmed decimal value")
    assert_not_derived(run, LINE, "9780439139601")


def test_ex39_unconfirmed_decimal_product_code_is_not_derived(workspace):
    """Example 39 / C2: 76783609419.0 is a decimal product code, not confirmed by R17; the
    padded ISBN-10 is kept and no ISBN-13 is derived. Warn."""
    run, book = load_single(workspace, 3901, "553609416", "76783609419.0")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0553609416", None)
    assert book["isbn13"] == "76783609419.0"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the unconfirmed decimal value")
    assert_not_derived(run, LINE, "9780553609417")


def test_ex40_dot_zero_confirmed_after_leading_zero_padding(workspace):
    """Example 40 / C2 (Washington's Crossing): R17's comparison applies the R12 repairs,
    including leading-zero padding, so 195170342.0 confirms the row's ISBN-10 0195170342 and
    the ISBN-13 is derived and logged. Raw values are preserved."""
    run, book = load_single(workspace, 4001, "195170342", "195170342.0", title="Washington's Crossing")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0195170342", "9780195170344")
    assert book["isbn"] == "195170342"
    assert book["isbn13"] == "195170342.0"
    assert_derived(run, LINE, "9780195170344")


def test_c2_thirteen_digit_decimal_is_not_derived(workspace):
    """C2: 9780439139601.0 is not an ISBN-10 even after the R12 repairs, so R17 does not confirm
    it and no ISBN-13 is derived. ISBN-10 kept; ISBN-13 NULL; warning."""
    run, book = load_single(workspace, 4002, "0439139600", "9780439139601.0")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == "9780439139601.0"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the decimal value")
    assert_not_derived(run, LINE, "9780439139601")


# ---------------------------------------------------------------------------
# C3 - invalid 979 versus 9790 values (R11, R19)
# ---------------------------------------------------------------------------

def test_ex41_invalid_979_value_is_a_contradiction(workspace):
    """Example 41 / C3: 9798351220410 (979, bad checksum) cannot agree with a 978 derivation.
    Keep the ISBN-10, leave ISBN-13 NULL, warn and flag for review; the row is loaded."""
    run, book = load_single(workspace, 4101, "0439139600", "9798351220410")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", None)
    assert book["isbn13"] == "9798351220410"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the invalid 979 value")
    assert_has_event(run, LINE, FLAG, "isbn13", what="review flag for the 979 contradiction")
    assert_not_derived(run, LINE, "9780439139601")


def test_ex42_9790_ismn_is_treated_as_a_product_code(workspace):
    """Example 42 / C3: a 9790 ISMN is not an ISBN (R11) and is handled like a product code under
    R19, as in example 17: derive the 978 ISBN-13 from the valid ISBN-10; warn and log."""
    run, book = load_single(workspace, 4201, "0439139600", "9790439139601")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", "9780439139601")
    assert book["isbn13"] == "9790439139601"
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the ISMN value")
    assert_derived(run, LINE, "9780439139601")


@pytest.mark.parametrize("raw13, exp13", [
    pytest.param("9.790439139601e+12", "9780439139601", id="exact-9790-is-a-product-code"),
    pytest.param("9.7904391396e+12", None, id="rounded-9790-follows-c1"),
])
def test_c3_9790_in_scientific_notation(workspace, raw13, exp13):
    """C1, C3: a 9790 value written as exact scientific notation shows all of its digits, so it
    is a product code under R19 and the 978 ISBN-13 is derived (as in example 42). A rounded
    9790 value follows C1: no ISBN-13 is derived, with a warning (as in example 37)."""
    run, book = load_single(workspace, 4202, "0439139600", raw13)

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", exp13)
    assert book["isbn13"] == raw13
    assert_has_event(run, LINE, WARNING, "isbn13", what="warning for the 9790 value")
    if exp13 is None:
        assert_not_derived(run, LINE, "9780439139601")
    else:
        assert_derived(run, LINE, exp13)


# ---------------------------------------------------------------------------
# C4 - conflicting pair for an existing book with no ISBNs (R21, R29)
# ---------------------------------------------------------------------------

def test_ex43_conflicting_pair_for_isbn_less_book_is_skipped_with_flag(workspace):
    """Example 43 / C4: no ISBN is accepted and the row is flagged; with no approved field
    change, the outcome is skipped (R29)."""
    workspace.run(workspace.write_csv([make_row(4301)], "first.csv"))
    assert cleaned(workspace.book(4301)) == (None, None)

    run = workspace.run(workspace.write_csv(
        [make_row(4301, isbn="1555550126", isbn13="9781555550134")], "second.csv"))

    assert run["counts"] == outcomes(skipped=1)
    assert cleaned(workspace.book(4301)) == (None, None)
    assert_has_event(run, LINE, FLAG, what="review flag for the conflicting pair")


def test_c4_conflicting_pair_with_metadata_change_is_updated_without_isbns(workspace):
    """C4: "updated if an approved field changes": the ratings_count change is applied (R27),
    but neither ISBN is accepted and the conflicting pair is still flagged."""
    workspace.run(workspace.write_csv([make_row(4302, ratings_count="100")], "first.csv"))

    run = workspace.run(workspace.write_csv(
        [make_row(4302, isbn="1555550126", isbn13="9781555550134", ratings_count="150")], "second.csv"))

    assert run["counts"] == outcomes(updated=1)
    after = workspace.book(4302)
    assert cleaned(after) == (None, None)
    assert after["ratings_count"] == "150"
    assert_has_event(run, LINE, FLAG, what="review flag for the conflicting pair")
    changes = [ev for ev in find_events(run, LINE, UPDATE, "ratings_count")
               if ev.get("old") == "100" and ev.get("new") == "150"]
    assert changes, f"expected ratings_count change 100 -> 150 in the log; got {events_for(run, LINE)}"


# ---------------------------------------------------------------------------
# C5 - blank raw ISBN cells (R03)
# ---------------------------------------------------------------------------

def test_ex44_blank_raw_isbn_is_stored_as_empty_text(workspace):
    """Example 44 / C5: a blank raw isbn is stored as empty text, not NULL; raw isbn13 stored
    exactly; the absent cleaned ISBN-10 is NULL (a 979 ISBN-13 has no ISBN-10)."""
    run, book = load_single(workspace, 4401, "", "9798351220413")

    assert run["counts"] == outcomes(loaded=1)
    assert book["isbn"] is not None, "blank raw isbn must be empty text, not NULL"
    assert book["isbn"] == ""
    assert book["isbn13"] == "9798351220413"
    assert cleaned(book) == (None, "9798351220413")


# ---------------------------------------------------------------------------
# C6 - formatting-only duplicates in one file (R23, R24, R34)
# ---------------------------------------------------------------------------

def test_ex45_formatting_only_repeat_is_a_normalized_duplicate(workspace):
    """Example 45 / C6: the earliest row is processed (loaded); the later row differs only by
    formatting and is skipped as 'normalized duplicate in input', a reason separate from
    'exact duplicate in input' and 'already exists'."""
    first = make_row(4501, isbn="0-439-13960-0")
    second = make_row(4501, isbn="0439139600")

    run = workspace.run(workspace.write_csv([first, second]))

    assert run["rows_read"] == 2
    assert run["counts"] == outcomes(loaded=1, skipped=1)
    assert workspace.book_count() == 1
    assert cleaned(workspace.book(4501)) == ("0439139600", "9780439139601")
    assert row_entry(run, 2)["outcome"] == "loaded"
    repeat = row_entry(run, 3)
    assert repeat is not None and repeat["outcome"] == "skipped"
    assert "normalized duplicate in input" in repeat["reason"]
    assert "exact duplicate in input" not in repeat["reason"]
    assert "already exists" not in repeat["reason"]


# ---------------------------------------------------------------------------
# C7 - copies of a rejected row (R23)
# ---------------------------------------------------------------------------

def test_ex46_exact_copy_of_rejected_row_is_skipped_as_exact_duplicate(workspace):
    """Example 46 / C7: the first copy is rejected (R26: different validated ISBN); the identical
    second copy is skipped as 'exact duplicate in input'. The existing book is unchanged."""
    workspace.run(workspace.write_csv([make_row(4601, isbn="0439139600")], "first.csv"))
    before = workspace.book(4601)

    conflicting = make_row(4601, isbn="1620014750")
    run = workspace.run(workspace.write_csv([conflicting, dict(conflicting)], "second.csv"))

    assert run["rows_read"] == 2
    assert run["counts"] == outcomes(rejected=1, skipped=1)
    assert workspace.book(4601) == before
    first, second = row_entry(run, 2), row_entry(run, 3)
    assert first is not None and first["outcome"] == "rejected"
    assert second is not None and second["outcome"] == "skipped"
    assert "exact duplicate in input" in second["reason"]


def test_c7_structural_and_required_identity_failures_are_rejected_every_time(workspace):
    """C7: "Rows that fail R38 or R05 are rejected every time": identical rows with a blank
    title (R05) and identical rows with the wrong field count (R38) are all rejected."""
    blank_title = make_row(4602, title="")
    malformed = "4603,904603,904603"  # 3 fields, expected 23

    run = workspace.run(workspace.write_csv([blank_title, dict(blank_title)], raw_lines=[malformed, malformed]))

    assert run["rows_read"] == 4
    assert run["counts"] == outcomes(rejected=4)
    assert workspace.book_count() == 0
    for line in (2, 3, 4, 5):
        entry = row_entry(run, line)
        assert entry is not None and entry["outcome"] == "rejected", f"line {line} must be rejected"


# ---------------------------------------------------------------------------
# C8 - a fill that contradicts the other stored ISBN (R25, R26)
# ---------------------------------------------------------------------------

def _load_book_with_isbn13_only(workspace, book_id):
    """Stored cleaned ISBN-10 NULL (raw 1854433017, bad check digit) and ISBN-13 9780062398123."""
    workspace.run(workspace.write_csv(
        [make_row(book_id, isbn="1854433017", isbn13="9780062398123", ratings_count="100")], "first.csv"))
    before = workspace.book(book_id)
    assert cleaned(before) == (None, "9780062398123")
    return before


def _assert_contradiction_logged(run):
    flags = [ev for ev in find_events(run, LINE, FLAG) if mentions(ev, "0439139600", "9780062398123")]
    assert flags, ("expected a logged conflict naming the incoming 0439139600 and the stored "
                   f"9780062398123; got {events_for(run, LINE)}")


def test_ex47_contradicting_fill_is_rejected_for_review(workspace):
    """Example 47 / C8: 0439139600 would fill the empty ISBN-10 but contradicts the stored
    ISBN-13; reject for review, keep the stored record unchanged, and log the conflict."""
    before = _load_book_with_isbn13_only(workspace, 4701)

    run = workspace.run(workspace.write_csv(
        [make_row(4701, isbn="0439139600", isbn13="195170342.0", ratings_count="100")], "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    assert workspace.book(4701) == before
    entry = row_entry(run, LINE)
    assert entry is not None and entry["outcome"] == "rejected"
    _assert_contradiction_logged(run)


def test_c8_contradicting_fill_updates_no_other_fields(workspace):
    """C8: "do not update other fields from that row, as in R26": the rejected row's approved
    metadata change (ratings_count) is not applied either."""
    before = _load_book_with_isbn13_only(workspace, 4702)

    run = workspace.run(workspace.write_csv(
        [make_row(4702, isbn="0439139600", isbn13="195170342.0", ratings_count="999")], "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    after = workspace.book(4702)
    assert after == before
    assert after["ratings_count"] == "100"
    assert find_events(run, LINE, UPDATE) == [], "no field may be updated from a rejected row"
    _assert_contradiction_logged(run)


# ---------------------------------------------------------------------------
# C9 - invalid isbn with a valid ISBN-10 in isbn13 (R13, R19 safeguard 4)
# ---------------------------------------------------------------------------

def test_ex48_valid_isbn10_in_isbn13_is_moved_beside_invalid_isbn(workspace):
    """Example 48 / C9: move the valid ISBN-10 from isbn13 (R13) and derive its 978 ISBN-13;
    warn about the invalid isbn; log the move and the derivation; keep both raw values."""
    run, book = load_single(workspace, 4801, "1854433017", "0439139600")

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(book) == ("0439139600", "9780439139601")
    assert book["isbn"] == "1854433017"
    assert book["isbn13"] == "0439139600"
    assert_has_event(run, LINE, WARNING, "isbn", what="warning for the invalid isbn value")
    repairs = find_events(run, LINE, REPAIR)
    assert len(repairs) >= 2, f"expected the move and the derivation to be logged; got {repairs}"
    assert_derived(run, LINE, "9780439139601")
