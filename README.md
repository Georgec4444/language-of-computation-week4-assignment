# Language of Computation — Week 4 Assignment

**Course:** MSAI-5523 — Language of Computation, Lipscomb University
**Module:** 4
**Student:** George (`Georgec4444`)
**Partner:** Asha Abdulkadir (`AshaAbdulkadir`)

## Project overview

This project is a Python pipeline that imports book records from supplier CSV files into a SQLite database, `books.db`, using deterministic, documented rules. Its central concern is the reliability of ISBN identifiers. Supplier data contains ISBNs with missing leading zeros, hyphens and spaces, values damaged by spreadsheet scientific notation, decimal artifacts such as `.0`, wrong check digits, and identifiers placed in the wrong column.

The pipeline follows one governing principle from the shared rules: **preserve identifiable books without accepting unreliable ISBN assignments, and reject records only when the identity of the book itself cannot be trusted.**

The program:

- **Validates ISBNs:** checks ISBN-10 and ISBN-13 checksums, and applies only narrowly defined, logged repairs.
- **Keeps raw and cleaned values separate:** raw `isbn` and `isbn13` text is preserved exactly as received, and cleaned values are stored in separate columns.
- **Does not guess:** it never infers missing digits or reassigns an ISBN that already belongs to another book.
- **Classifies every row:** each data row gets exactly one outcome, **loaded**, **updated**, **skipped** or **rejected**, and the four counts always add up to the rows read.
- **Keeps a history:** each run is appended to `ingestion-log.json` with a SHA-256 fingerprint of the source file, a timezone-aware timestamp, and row-level detail for review.
- **Is repeatable:** re-ingesting the same file creates no duplicates and no spurious updates.

The work also includes an automated pytest suite. Every test traces to a numbered rule or example in `rules.md`, and every test runs against an isolated temporary database.

## Rules and partner agreement

`rules.md` is the single source of truth for the program's behavior and for the tests.

- **Version 1.0 (November 6, 2026):** the specification jointly agreed with my partner, Asha Abdulkadir. It contains rules R01–R40 and 35 agreed ISBN and ingestion examples.
- **Version 1.1 (November 8, 2026):** the final shared version, agreed with Asha. Both repositories hold byte-identical copies (SHA-256 `6a9a6071…24e8b1`). Section 8 adds nine clarifications, C1–C9, and examples 36–48, which answer the questions in `rules-clarifications-for-asha.md`. Among them:
  - **C2:** R17's `.0` comparison applies the R12 repairs, including leading-zero padding. Example 40, book 8174, *Washington's Crossing*: `195170342.0` confirms `0195170342`, so `9780195170344` is derived.
  - **C3:** a `979` value with a bad checksum next to a valid ISBN-10 is a contradiction: the cleaned ISBN-13 is NULL and the row is flagged for review. A `9790` ISMN value is treated as a product code under R19, and a rounded `9790` value derives nothing.
  - **C6:** a later row in the same file that differs only by formatting is skipped as `normalized duplicate in input`.
  - **C8:** a new ISBN that contradicts a book's already accepted ISBN is rejected for review.

Under our working agreement, each partner develops their own tests and code independently from the shared rules. The two resulting databases are compared in `comparison.md`.

## Files and folders

| Path | Description |
|---|---|
| `ingest.py` | The ingestion program. It reads one CSV file given on the command line and writes to `books.db` and `ingestion-log.json` in its own folder, regardless of the terminal's current folder. It uses only the Python standard library. |
| `rules.md` | Shared ingestion rules, version 1.1: rules R01–R40, the 35 agreed examples, and clarifications C1–C9 with examples 36–48 |
| `rules-clarifications-for-asha.md` | The nine rule interpretations raised for discussion with my partner, each with a concrete example. They are now settled by `rules.md` section 8. |
| `comparison.md` | Part 5: comparison of my `books.db` with my partner's by `book_id`, including the section "Why our data differs" |
| `partner/books.db` | My partner's database, downloaded from her `assignment` branch for the comparison (read-only use) |
| `books.csv` | Original source data: 10,000 books. Preserved byte for byte; SHA-256 `4d21d0e0…acb76a` |
| `books-batch2.csv` | Second supplier delivery: 31 rows. Preserved byte for byte; SHA-256 `bf884855…b53df5` |
| `books.db` | SQLite database produced by the three ingestion runs. It holds the 23 supplier columns plus separate cleaned ISBN-10 and ISBN-13 columns. |
| `ingestion-log.json` | Append-only run log: one record per run, with counts, warnings, repairs, rejections, field changes, and rows needing review |
| `report.html` | Self-contained HTML report. Open it in a browser and select `ingestion-log.json` to see all runs, newest first. |
| `tests/conftest.py` | Shared fixtures and helpers. It redirects every test to its own temporary `test.db` and log, checks that the program cannot write to the production files, and cleans up after each test, whether it passed or failed. |
| `tests/test_rules_examples.py` | Tests for the 35 agreed examples in `rules.md`, section 7 |
| `tests/test_ingestion_runs.py` | Run-level tests: repeating a file creates no duplicates, repeat and fresh runs give identical results, the log only appends, and counts add up for the three-run sequence |
| `tests/test_rules_clarifications.py` | Tests for clarifications C1–C9 and examples 36–48 in `rules.md`, section 8 |
| `pytest.ini` | pytest configuration (test folder; cache disabled) |
| `test-results-before.txt` | Baseline test output before the program was updated: 35 failed, 16 passed of 51 tests |
| `test-results-after.txt` | Test output after the first update: 51 passed |
| `test-results-final.txt` | Superseded: test output under my earlier draft of version 1.1, 67 passed (UTF-16 encoded) |
| `test-results.txt` | **Final test results** under the shared version 1.1: all 70 tests passed (UTF-8) |
| `.gitattributes` | Stops Git from changing line endings in the CSV files, the log and the database |
| `.gitignore` | Excludes `.venv/`, `__pycache__/` and `.pytest_cache/` |
| `.venv/` | Local virtual environment with pytest 9.2.0 (not committed) |

## Requirements

- Python 3.13 (verified with Python 3.13.17 on Windows 11)
- pytest 9.2.0, installed in the local virtual environment `.venv`. pytest is needed only for the tests; `ingest.py` itself has no third-party dependencies.

## Running the program

Run these commands in PowerShell from the assignment folder:

```powershell
cd C:\Users\GC4Ne\workspace\language-of-computation\week4-assignment

# Run 1: load the original data
.venv\Scripts\python ingest.py books.csv

# Run 2: ingest the identical file again (repeatability check)
.venv\Scripts\python ingest.py books.csv

# Run 3: load the second delivery into the same database
.venv\Scripts\python ingest.py books-batch2.csv
```

Each run prints a one-line summary of rows read and the four outcome counts, and appends one record to `ingestion-log.json`.

> **Important: the database and log already exist.** `books.db` and `ingestion-log.json` in this folder hold the verified results of the three runs below. Running `ingest.py` again **adds to** these files. It would add a fourth run to the log, and its outcomes would be measured against the existing database, so the documented results would no longer describe the files.
>
> To reproduce the sequence from scratch, use a fresh copy of the project folder. Alternatively, first move the existing `books.db` and `ingestion-log.json` somewhere else, for example:
>
> ```powershell
> Rename-Item books.db books-verified.db
> Rename-Item ingestion-log.json ingestion-log-verified.json
> ```

## Running the tests

```powershell
cd C:\Users\GC4Ne\workspace\language-of-computation\week4-assignment
.venv\Scripts\python -m pytest -v
```

To save the output as UTF-8, as in `test-results.txt`, create the file first. Otherwise the session check below would see a new file appear in the project folder during the run.

```powershell
New-Item -ItemType File -Force test-results.txt | Out-Null
.venv\Scripts\python -m pytest -v | Out-File -Encoding utf8 test-results.txt
```

The tests are safe to run at any time, because they never touch the production data:

- **Isolated files:** each test uses its own temporary `test.db` and log, which are deleted afterwards whether the test passes or fails.
- **Safety stop:** a check runs before each test's ingestion and stops the test if any output path points outside its temporary folder.
- **Session check:** a final check confirms that `books.csv`, `books-batch2.csv`, `books.db` and `ingestion-log.json` are byte-for-byte unchanged after the session.

## Verified results

### Automated tests

From `test-results.txt` (Python 3.13.17, pytest 9.2.0):

```
collected 70 items
============================= 70 passed in 12.50s =============================
```

| Test file | Tests |
|---|---|
| `tests/test_rules_examples.py` | 45 |
| `tests/test_ingestion_runs.py` | 6 |
| `tests/test_rules_clarifications.py` | 19 |
| **Total** | **70 passed, 0 failed** |

### Ingestion runs

From `ingestion-log.json`. The three runs were made on November 8, 2026 between 17:23 and 17:25 local time (UTC−06:00), under the shared `rules.md` version 1.1:

| Run | Source file | Rows read | Loaded | Updated | Skipped | Rejected | Rows needing review | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | `books.csv` | 10,000 | 10,000 | 0 | 0 | 0 | 0 | completed |
| 2 | `books.csv` | 10,000 | 0 | 0 | 10,000 | 0 | 0 | completed |
| 3 | `books-batch2.csv` | 31 | 14 | 4 | 13 | 0 | 2 | completed |

**Final database:** `books.db` contains **10,014 books** (10,000 from run 1 plus 14 new books from run 3). SHA-256 `8dee9e99…7a43`. Book 8174, *Washington's Crossing*, has the cleaned ISBN-10 `0195170342` and ISBN-13 `9780195170344` (example 40).

The results demonstrate the required properties:

- **Complete accounting:** in every run, loaded + updated + skipped + rejected equals rows read (R32).
- **Repeatability:** run 2 ingested the identical file (same SHA-256 fingerprint as run 1) and inserted no duplicates and made no spurious updates; all 10,000 rows were skipped (R23, R39).
- **Safe updates:** run 3 added 14 new books, applied approved changes to 4 existing books with old and new values recorded, and skipped 13 rows with no permitted change (R25, R27, R29).
- **Unchanged source data:** the SHA-256 fingerprints of the two CSV files in the log match the original files (R01).

### Partner database comparison

From `comparison.md`. My `books.db` was compared with Asha's (`partner/books.db`) by `book_id`, across the 22 supplier columns other than `book_id` and both cleaned ISBNs:

| | Result |
|---|---|
| Books in each database | 10,014 and 10,014 |
| Books in only one database | None |
| Values compared | 240,336 |
| Values that differ | **None: the databases match** |

The one earlier difference was the cleaned ISBN-13 of book 8174, NULL in my earlier database. It came from different readings of R17 and was resolved by clarification C2 in the shared version 1.1. `comparison.md` explains it in its section "Why our data differs".

## Viewing the report

Open `report.html` in a web browser (no web server required) and select `ingestion-log.json`. The report shows each run's header information, totals, repairs, warnings, skipped and rejected rows, and field changes, newest run first.
