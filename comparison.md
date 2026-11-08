# Module 4 Database Comparison

**The two databases hold the same data.** Every compared value of every book is identical.

| Database | Books |
|---|---|
| Mine: `books.db` (George) | 10,014 |
| Partner's: `partner/books.db` (Asha) | 10,014 |

## Differences

### Books only in mine

None.

### Books only in my partner's

None.

### Books in both with a different value

None. All 10,014 books match in all 24 compared columns (240,336 values).

## How the comparison was made

**Same book.** Rows were matched on `book_id`, the primary book key in `rules.md` (R05).

**Columns compared.** The 22 supplier columns other than `book_id`, plus the two cleaned ISBNs. This mapping was chosen before the comparison was run:

| Mine | Partner's | Compared as |
|---|---|---|
| `goodreads_book_id`, `best_book_id`, `work_id`, `books_count`, `isbn`, `isbn13`, `authors`, `original_publication_year`, `original_title`, `title`, `language_code`, `average_rating`, `ratings_count`, `work_ratings_count`, `work_text_reviews_count`, `ratings_1`–`ratings_5`, `image_url`, `small_image_url` | Same names | Each with its same-named column |
| `isbn10_clean` | `isbn_10_clean` | Cleaned ISBN-10 |
| `isbn13_clean` | `isbn_13_clean` | Cleaned ISBN-13 |

**Left out.** These are layout differences that R30 allows:
- `goodreads_id_clean`, which exists only in my partner's database. It is derived from `goodreads_book_id`, and the raw `goodreads_book_id` is compared instead.
- My partner's three unique indexes.
- Neither database stores run metadata such as load times or run numbers; that information is only in each `ingestion-log.json`.

**Values** were compared exactly, so NULL and empty text count as different, and so do the number `5` and the text `"5"`. As a further check, both databases hold the same mix of stored value types: 10,014 integers, 237,139 text values and 3,197 NULLs. Both have 730 NULL cleaned ISBN-10s and 744 NULL cleaned ISBN-13s. Both databases were opened read-only, so neither was changed.

**Sources.**
- My `books.db` was produced on November 8, 2026 by three runs of my `ingest.py` under the shared `rules.md` version 1.1, as recorded in `ingestion-log.json`:

  | Run | File | Rows read | Loaded | Updated | Skipped | Rejected |
  |---|---|---|---|---|---|---|
  | 1 | `books.csv` | 10,000 | 10,000 | 0 | 0 | 0 |
  | 2 | `books.csv` | 10,000 | 0 | 0 | 10,000 | 0 |
  | 3 | `books-batch2.csv` | 31 | 14 | 4 | 13 | 0 |

  SHA-256: `8dee9e99d8b33913949b2aa72f6491940a2c09f589d595fbe6265dfabd0b7a43`.
- `partner/books.db` was downloaded from `https://github.com/AshaAbdulkadir/language-of-computation-week4-assignment/raw/assignment/books.db` (branch `assignment`, commit `57ff921`). SHA-256: `ba9b0a4d0c0bb9b05f7dbd64508639284cfc88071d60e1a326571a24c7e962e1`, the same value my partner's `comparison.md` gives for her database.
- Both programs ingested byte-identical source files, in the same three-run order (R39):
  - `books.csv`: SHA-256 `4d21d0e0…acb76a`;
  - `books-batch2.csv`: SHA-256 `bf884855…b53df5`.
- Both of us now hold byte-identical copies of `rules.md` version 1.1 (SHA-256 `6a9a6071…24e8b1`).

## Why our data differs

Our data no longer differs. Before the final version of the rules, the two databases differed in exactly one value: the cleaned ISBN-13 of book 8174, *Washington's Crossing*. That difference has been resolved.

| Book 8174 | Value |
|---|---|
| Raw `isbn` / `isbn13` (both databases) | `195170342` / `195170342.0` |
| Cleaned ISBN-10 (both databases) | `0195170342`: leading zero restored under R12 |
| Cleaned ISBN-13, my earlier database | NULL |
| Cleaned ISBN-13, my partner's database and my current database | `9780195170344` |

**Which rule it came from.** The difference came from R17, the narrow `.0` exception, and how it works together with R12. R17 confirms a `.0` value in `isbn13` only when removing `.0` gives "exactly the same valid ISBN-10 already present in that row's `isbn` field (after permitted simple formatting)". Removing `.0` from `195170342.0` leaves nine digits. Those only match the row's ISBN-10 `0195170342` once the leading zero is restored, which R12 treats as an approved repair.
- **My earlier program** confirmed only values that were already ten characters before `.0`. It stored the ISBN-13 as NULL with the warning "decimal-looking value is not an ISBN; not stripped; stored NULL".
- **My partner's program** applied the leading-zero padding first, confirmed the value, and derived `9780195170344`.

**Did the rule let it through?** Yes. Version 1.0 of R17 did not say whether leading-zero padding counts as "permitted simple formatting", and both readings fit its wording. Our first separate drafts of version 1.1 still disagreed: my partner's clarification C2 said padding counts, and mine did not address it.

**Did the tests let it through?** Yes. None of the 35 shared examples has a `.0` value that needs padding:
- example 18 uses a full ten-digit `0439139600.0`;
- example 19 has a blank `isbn`.

My clarification tests checked unconfirmed decimal values but not this case, so each test suite passed with its own reading.

**Did the code let it through?** No. Each program did what its own reading of the rule said, and neither had a bug.

**How it was resolved.** We adopted one shared version 1.1 of `rules.md`, now byte-identical in both repositories. Its clarification C2 states that R17's comparison applies the R12 repairs, including leading-zero padding, to the digits before `.0`. Example 40 records the case of book 8174.
- **Code:** I changed my `ingest.py` to apply the R12 repairs before the R17 comparison.
- **Tests:** I added a test for example 40.
- **Database:** I regenerated my database with a fresh three-run ingestion, and my log now records "confirmed: removing .0 gives (after the R12 repairs) the same validated ISBN-10 0195170342 as the isbn field; derived ISBN-13".

**Other rule differences between our earlier drafts.**
- **C3 (`9790` values):** the shared version treats a `9790` ISMN as a product code under R19. In the data, this affects book 1106, *The Great Divorce* (`9.79000767239e+12`). That value is rounded, so both programs store a NULL cleaned ISBN-13 under either draft. My earlier program also flagged the row for review, which only changed the log.
- **C6 (the skip reason for a formatting-only repeat in one file):** the shared version uses the reason `normalized duplicate in input`. Neither data file contains such a row; the repeated book 10001 in `books-batch2.csv` is an exact duplicate.

Neither of these changed either database.

All 70 tests in my suite pass against my final program (`test-results.txt`).
