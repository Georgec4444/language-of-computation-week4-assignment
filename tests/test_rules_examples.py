"""One test per example in section 7 of rules.md ("Agreed ISBN and ingestion examples").

Each test name starts with the example number. Unless an example says otherwise, the input is
a single new book on CSV line 2 of a fresh test.db (see "How to read the table" in rules.md).
Each test checks the table's expected cleaned ISBN-10, cleaned ISBN-13, and primary outcome,
plus the logging the "Required action" column asks for.
"""

import pytest

from conftest import (
    FLAG, REPAIR, UPDATE, WARNING, assert_has_event, cleaned, find_events, make_row,
    outcomes, row_entry,
)

LINE = 2  # first data row of a single-row CSV (R34)

# Expected-logging checks for single-row examples: (kind, field). field None = any field.
NO_REPAIR = ("no-repair", None)
NO_MISSING_ISBN10_WARNING = ("no-missing-isbn10-warning", None)


def repair(field=None):
    return ("repair", field)


def warn(field=None):
    return ("warn", field)


def flag():
    return ("flag", None)


# (raw isbn, raw isbn13, title or None, expected cleaned ISBN-10, expected cleaned ISBN-13, checks)
SINGLE_NEW_ROW_EXAMPLES = [
    pytest.param("0439139600", "9780439139601", None, "0439139600", "9780439139601",
                 [NO_REPAIR], id="ex01-both-valid-no-repair"),
    pytest.param("439023483", "9.78043902348e+12", "The Hunger Games", "0439023483", "9780439023481",
                 [repair("isbn"), repair("isbn13")], id="ex02-pad-and-sci-half-even-rebuild"),
    pytest.param("61120081", "", None, "0061120081", "9780061120084",
                 [repair("isbn"), repair("isbn13")], id="ex03-pad-8-digits-derive-isbn13"),
    pytest.param("0-450-19827-8", "9780450198274", "Salt and Ember", "0450198278", "9780450198274",
                 [repair("isbn")], id="ex04-remove-hyphens"),
    pytest.param("162 001 4750", "9781620014752", None, "1620014750", "9781620014752",
                 [repair("isbn")], id="ex05-remove-spaces"),
    pytest.param("031200009x", "9780312000097", "Winter Orchard", "031200009X", "9780312000097",
                 [repair("isbn")], id="ex06-uppercase-final-x"),
    pytest.param("9781982147600", "", "An Atlas of Small Kindnesses", "1982147601", "9781982147600",
                 [repair()], id="ex07-isbn13-in-isbn-column-moved"),
    pytest.param("", "9780062398123", None, "0062398121", "9780062398123",
                 [repair("isbn")], id="ex08-derive-isbn10-from-978"),
    pytest.param("", "9798351220413", None, None, "9798351220413",
                 [NO_MISSING_ISBN10_WARNING], id="ex09-979-no-isbn10-no-warning"),
    pytest.param("1416524797", "9.78141652479e+12", "Angels & Demons", "1416524797", "9781416524793",
                 [repair("isbn13")], id="ex10-sci-confirmed-by-isbn10"),
    pytest.param("", "9.78067172365e+12", "How to Win Friends and Influence People", None, None,
                 [warn()], id="ex11-sci-without-companion"),
    pytest.param("", "9.7804251888e+12", "Watchers", None, None,
                 [warn()], id="ex12-sci-ambiguous"),
    pytest.param("1854433017", "", "Small Hours", None, None,
                 [warn()], id="ex13-wrong-check-digit"),
    pytest.param("04390A3483", "", "The Glass Aviary", None, None,
                 [warn()], id="ex14-illegal-interior-letter"),
    pytest.param("15287463097", "", "Borrowed Light", None, None,
                 [warn()], id="ex15-extra-digit"),
    pytest.param("", "2940016231920", None, None, None,
                 [warn()], id="ex16-2940-product-code"),
    pytest.param("189221301X", "6.45573003012e+11", "Sailor Moon #1", "189221301X", "9781892213013",
                 [warn("isbn13"), repair("isbn13")], id="ex17-product-code-replaced-by-derivation"),
    pytest.param("0439139600", "0439139600.0", None, "0439139600", "9780439139601",
                 [repair("isbn13")], id="ex18-narrow-dot-zero-exception"),
    pytest.param("", "195170342.0", None, None, None,
                 [warn()], id="ex19-unconfirmed-decimal"),
    pytest.param("", "", "Bossypants", None, None,
                 [warn()], id="ex20-both-blank-bossypants"),
    pytest.param("", "", "Untitled Manuscript", None, None,
                 [warn()], id="ex20-both-blank-untitled-manuscript"),
    pytest.param("1555550126", "9781555550134", "Tides of Marrow Bay", None, None,
                 [flag()], id="ex21-valid-but-mismatched-pair"),
    pytest.param("0446612707", "9780553813951", "Nights in Rodanthe", None, None,
                 [flag()], id="ex23-two-valid-mismatched-isbns"),
    pytest.param("812971060", "9.78081297106e+12", "Reading Lolita in Tehran", None, None,
                 [warn()], id="ex35-no-0-to-X-change"),
]


def check_logging(run, line, checks):
    for kind, field in checks:
        if kind == "repair":
            assert_has_event(run, line, REPAIR, field, what="logged repair")
        elif kind == "warn":
            assert_has_event(run, line, WARNING, field, what="warning")
        elif kind == "flag":
            assert_has_event(run, line, FLAG, what="conflict/review flag")
        elif kind == "no-repair":
            assert find_events(run, line, REPAIR) == [], "example says no repair, but a repair was logged"
        elif kind == "no-missing-isbn10-warning":
            isbn10_warnings = [ev for ev in find_events(run, line, WARNING)
                               if ev.get("field") in ("isbn", "isbn/isbn13")]
            assert isbn10_warnings == [], "a valid 979 ISBN-13 must not produce a missing-ISBN-10 warning"
        else:
            raise AssertionError(f"unknown check {kind}")


@pytest.mark.parametrize("raw10, raw13, title, exp10, exp13, checks", SINGLE_NEW_ROW_EXAMPLES)
def test_single_new_row_examples(workspace, raw10, raw13, title, exp10, exp13, checks):
    """Examples 1-21, 23 and 35: one new book; expected cleaned ISBNs; outcome Loaded."""
    extra = {"title": title} if title else {}
    row = make_row(1001, isbn=raw10, isbn13=raw13, **extra)

    run = workspace.run(workspace.write_csv([row]))

    assert run["status"] == "completed"
    assert run["rows_read"] == 1
    assert run["counts"] == outcomes(loaded=1)
    book = workspace.book(1001)
    assert cleaned(book) == (exp10, exp13)
    # "Raw input strings remain preserved" (table note, R03): raw supplier text kept exactly.
    if raw10:
        assert book["isbn"] == raw10
    if raw13:
        assert book["isbn13"] == raw13
    check_logging(run, LINE, checks)


def test_ex22_isbn_already_owned_by_another_book(workspace):
    """Example 22 / R22: Angels & Demons was accepted in an earlier run and owns 1416524797.
    A new book claiming it is loaded without that ISBN or anything derived from it, and flagged."""
    angels = make_row(2201, title="Angels & Demons", isbn="1416524797", isbn13="9.78141652479e+12")
    workspace.run(workspace.write_csv([angels], "first.csv"))
    angels_before = workspace.book(2201)
    assert cleaned(angels_before) == ("1416524797", "9781416524793")

    paper_moons = make_row(2202, title="Paper Moons", isbn="1416524797", isbn13="")
    run = workspace.run(workspace.write_csv([paper_moons], "second.csv"))

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(workspace.book(2202)) == (None, None)
    assert workspace.book(2201) == angels_before
    assert_has_event(run, LINE, FLAG, what="flag for the already-owned ISBN")


def test_ex24_exact_duplicate_row_in_one_file(workspace):
    """Example 24 / R23: first occurrence loaded, second skipped as an exact duplicate in input;
    both source rows counted."""
    row = make_row(2401, isbn="1528746309", isbn13="9781528746304")

    run = workspace.run(workspace.write_csv([row, dict(row)]))

    assert run["rows_read"] == 2
    assert run["counts"] == outcomes(loaded=1, skipped=1)
    assert workspace.book_count() == 1
    assert cleaned(workspace.book(2401)) == ("1528746309", "9781528746304")
    second = row_entry(run, 3)
    assert second is not None and second["outcome"] == "skipped"
    assert "exact duplicate in input" in second["reason"]  # R23 / R34 wording


def test_ex25_previously_accepted_book_repeats_with_clean_formatting(workspace):
    """Example 25 / R23: book_id 24 was accepted from books.csv's raw values; it returns with
    clean-format ISBNs that normalize to the same values. Skipped as already existing; no change."""
    original = make_row(24, title="Harry Potter and the Goblet of Fire", isbn="439139600",
                        isbn13="9.7804391396e+12")
    workspace.run(workspace.write_csv([original], "first.csv"))
    before = workspace.book(24)
    assert cleaned(before) == ("0439139600", "9780439139601")

    repeat = make_row(24, title="Harry Potter and the Goblet of Fire", isbn="0439139600",
                      isbn13="9780439139601")
    run = workspace.run(workspace.write_csv([repeat], "second.csv"))

    assert run["counts"] == outcomes(skipped=1)
    assert workspace.book_count() == 1
    assert workspace.book(24) == before
    entry = row_entry(run, LINE)
    assert entry is not None and entry["outcome"] == "skipped"
    assert "already exists" in entry["reason"]  # R34: distinct from "exact duplicate in input"


def test_ex26_blank_isbns_never_erase_accepted_isbns(workspace):
    """Example 26 / R25, R29: book_id 25 has valid 0545010225; a later row with both ISBNs blank
    is skipped and the stored ISBNs are preserved."""
    original = make_row(25, title="Harry Potter and the Deathly Hallows", isbn="545010225",
                        isbn13="9.78054501022e+12")
    workspace.run(workspace.write_csv([original], "first.csv"))
    before = workspace.book(25)
    assert cleaned(before)[0] == "0545010225"

    blanked = make_row(25, title="Harry Potter and the Deathly Hallows", isbn="", isbn13="")
    run = workspace.run(workspace.write_csv([blanked], "second.csv"))

    assert run["counts"] == outcomes(skipped=1)
    assert workspace.book(25) == before


def test_ex27_approved_metadata_change_is_updated(workspace):
    """Example 27 / R27: book_id 132, same identity and ISBNs, ratings_count 449501 -> 451425.
    Updated; ISBNs preserved; old and new values recorded."""
    common = dict(title="The Five People You Meet in Heaven", authors="Mitch Albom",
                  isbn="1401308589", isbn13="9.78140130858e+12")
    workspace.run(workspace.write_csv([make_row(132, ratings_count="449501", **common)], "first.csv"))
    before = workspace.book(132)

    run = workspace.run(workspace.write_csv([make_row(132, ratings_count="451425", **common)], "second.csv"))

    assert run["counts"] == outcomes(updated=1)
    after = workspace.book(132)
    assert after["ratings_count"] == "451425"
    assert cleaned(after) == cleaned(before)
    changes = [ev for ev in find_events(run, LINE, UPDATE, "ratings_count")
               if ev.get("old") == "449501" and ev.get("new") == "451425"]
    assert changes, f"expected ratings_count change 449501 -> 451425 in the log; got {run.get('rows')}"


@pytest.mark.parametrize("field, new_value", [("title", "A Different Title"),
                                              ("authors", "A Different Author")])
def test_ex28_same_book_id_with_different_identity_is_rejected(workspace, field, new_value):
    """Example 28 / R07: same book_id with a different non-whitespace title or authors is
    rejected and the existing record is left unchanged."""
    workspace.run(workspace.write_csv([make_row(2801, isbn="0439139600")], "first.csv"))
    before = workspace.book(2801)

    run = workspace.run(workspace.write_csv([make_row(2801, isbn="0439139600", **{field: new_value})],
                                            "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    assert workspace.book(2801) == before


def test_ex29_two_new_rows_share_a_goodreads_id(workspace):
    """Example 29 / R08: two new rows with different book_ids but the same valid Goodreads ID
    are both rejected; neither is chosen by row order."""
    first = make_row(2901, goodreads_book_id="555555")
    second = make_row(2902, goodreads_book_id="555555")

    run = workspace.run(workspace.write_csv([first, second]))

    assert run["counts"] == outcomes(rejected=2)
    assert workspace.book(2901) is None
    assert workspace.book(2902) is None
    for line in (2, 3):
        entry = row_entry(run, line)
        assert entry is not None and entry["outcome"] == "rejected"


def test_ex30_no_goodreads_id_and_no_isbn_still_loads(workspace):
    """Example 30 / R06, R15: primary identity is sufficient; warn about the missing Goodreads ID
    and the missing ISBNs."""
    row = make_row(3001, goodreads_book_id="", isbn="", isbn13="")

    run = workspace.run(workspace.write_csv([row]))

    assert run["counts"] == outcomes(loaded=1)
    assert cleaned(workspace.book(3001)) == (None, None)
    assert_has_event(run, LINE, WARNING, "goodreads_book_id", what="missing Goodreads ID warning")
    isbn_warnings = [ev for ev in find_events(run, LINE, WARNING) if "isbn" in (ev.get("field") or "")]
    assert isbn_warnings, f"expected a missing-ISBN warning; got {run.get('rows')}"


def test_ex31_two_new_books_claim_the_same_isbn(workspace):
    """Example 31 / R22: two new, distinct books in one delivery claim the same valid ISBN.
    Both loaded, neither gets the disputed ISBN (nor a counterpart derived from it), both flagged."""
    first = make_row(3101, isbn="1528746309")
    second = make_row(3102, isbn="1528746309")

    run = workspace.run(workspace.write_csv([first, second]))

    assert run["counts"] == outcomes(loaded=2)
    assert cleaned(workspace.book(3101)) == (None, None)
    assert cleaned(workspace.book(3102)) == (None, None)
    for line in (2, 3):
        assert_has_event(run, line, FLAG, what="flag for the disputed ISBN")


def test_ex32_different_validated_isbn_on_existing_book_is_rejected(workspace):
    """Example 32 / R26: the existing book keeps its validated ISBN, and no other field is
    updated from the conflicting row (its ratings_count change is ignored)."""
    workspace.run(workspace.write_csv(
        [make_row(3201, isbn="0439139600", isbn13="9780439139601")], "first.csv"))
    before = workspace.book(3201)

    conflicting = make_row(3201, isbn="1620014750", isbn13="9781620014752", ratings_count="999")
    run = workspace.run(workspace.write_csv([conflicting], "second.csv"))

    assert run["counts"] == outcomes(rejected=1)
    assert workspace.book(3201) == before


@pytest.mark.parametrize("overrides", [
    {"book_id": ""},
    {"book_id": "0"},
    {"book_id": "-5"},
    {"book_id": "3.5"},
    {"book_id": "abc"},
    {"title": ""},
    {"title": "   "},
    {"authors": ""},
    {"authors": "   "},
], ids=["blank-id", "zero-id", "negative-id", "fractional-id", "nonnumeric-id",
        "blank-title", "whitespace-title", "blank-authors", "whitespace-authors"])
def test_ex33_missing_required_identity_is_rejected(workspace, overrides):
    """Example 33 / R05: blank/invalid book_id or blank title/authors is rejected; no book inserted."""
    row = make_row(3301, isbn="0439139600")
    row.update(overrides)

    run = workspace.run(workspace.write_csv([row]))

    assert run["counts"] == outcomes(rejected=1)
    assert workspace.book_count() == 0


def test_ex34_safe_isbn_improvement_is_updated(workspace):
    """Example 34 / R25: an existing book with no cleaned ISBN later supplies a valid,
    nonconflicting ISBN-10; it is filled, its ISBN-13 counterpart derived, and old/new logged."""
    workspace.run(workspace.write_csv([make_row(3401, isbn="", isbn13="")], "first.csv"))
    assert cleaned(workspace.book(3401)) == (None, None)

    run = workspace.run(workspace.write_csv([make_row(3401, isbn="0439139600", isbn13="")], "second.csv"))

    assert run["counts"] == outcomes(updated=1)
    assert cleaned(workspace.book(3401)) == ("0439139600", "9780439139601")
    filled = [ev for ev in find_events(run, LINE, UPDATE)
              if "old" in ev and ev.get("old") is None and ev.get("new") == "0439139600"]
    assert filled, f"expected the old (blank) and new ISBN-10 values in the log; got {run.get('rows')}"
