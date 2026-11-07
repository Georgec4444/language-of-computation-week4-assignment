# Module 4 — Shared Rules for Book Ingestion

**Course:** MSAI-5523 — Language of Computation  
**Partners:** George (`Georgec4444`) and Asha (`AshaAbdulkadir`)  
**Agreed version:** 1.0 — November 6, 2026  
**Status:** One shared specification for both students' independent Module 4 implementations and pytest suites.

## Purpose and governing principle

Import the supplied book CSV files into `books.db` using deterministic, repeatable rules. **Preserve identifiable books without accepting unreliable ISBN assignments; reject records when the identity of the book itself cannot be trusted.** Do not guess missing digits, silently change identity, use an AI model to decide individual rows, or contact an external service during ingestion. Never modify `books.csv` or `books-batch2.csv`.

These are the **complete shared rules**, replacing both partners' separate Week 3 rules **for Module 4 only**. Both partners must use identical copies named `rules.md`. After agreeing on this file, each partner independently develops their own tests and code; they do not exchange prompts, tests, or code.

### How the two original sets were consolidated

| Origin | Decision carried into these shared rules |
|---|---|
| **Both original sets** | Validate ISBN checksums; remove simple formatting; restore justified leading zeros; reject guessing; preserve source data; skip identical duplicates; never overwrite good values with blanks; account for every row; report repairs, warnings, and rejections. |
| **George's original rules retained** | `book_id` is the primary key; `title` and `authors` are mandatory; keep otherwise identifiable books when ISBNs fail; do not merge editions; use exact, case-sensitive title/author comparisons after trimming; preserve existing identity; append run logs; provide a standalone HTML report; exclude `9790` ISMN identifiers. |
| **Asha's original rules retained** | Validate `goodreads_book_id` as a secondary identity check; reject genuinely conflicting book identities; record SHA-256 hashes and timezone-aware run times; maintain review and change history; fail safely on missing/bad input; reject malformed data rows without stopping the whole file; update approved non-identity fields; apply a narrow, independently confirmed `.0` repair. |
| **Agreed changes to reconcile differences** | A valid `book_id` can identify an ISBN-less book even when Goodreads ID is absent; conflicting ISBNs do not automatically erase an otherwise identifiable new book; ISBNs already owned by another book are not reassigned; approved metadata may be updated with history; a valid ISBN-10 may justify a derived ISBN-13 after a bad product-code/invalid ISBN-13, but only under the safeguards below. |
| **New clarifications for testing** | Define deterministic handling of two new rows competing for the same ISBN, an explicit list of updateable fields, the outcome of a conflicting update, precise row counts, and test-file isolation. These clarifications are part of the shared agreement. |

---

## 1. Source files, required columns, and preservation

**R01 — Files are immutable.** Read `books.csv` and `books-batch2.csv` as UTF-8 CSV input. Do not open, rewrite, normalize, sort, or save them through Excel/Numbers. The two students must ingest byte-for-byte identical source files. Treat ISBNs as strings, never floating-point numbers.

**R02 — The 23 supplier columns are required.** The input header must contain each of these names exactly once, with no missing, extra, or duplicate columns:

`book_id`, `goodreads_book_id`, `best_book_id`, `work_id`, `books_count`, `isbn`, `isbn13`, `authors`, `original_publication_year`, `original_title`, `title`, `language_code`, `average_rating`, `ratings_count`, `work_ratings_count`, `work_text_reviews_count`, `ratings_1`, `ratings_2`, `ratings_3`, `ratings_4`, `ratings_5`, `image_url`, `small_image_url`.

Header order need not affect meaning: identify fields by column name. A header with missing, extra, or duplicate names causes a **failed run** before any book is changed.

**R03 — Preserve source information.** Keep all 23 source fields for each accepted book, including the original raw `isbn` and `isbn13` text. Store `book_id` as a validated positive integer. Preserve the original `isbn` and `isbn13` source text exactly as received (including original formatting), and store cleaned ISBNs separately; for other supplier fields, whitespace-only cells are treated as missing/NULL for normalized storage. Preserve every incoming raw value in the log even when the database stores NULL or a normalized value. A later delivery's raw values and any changes must remain available in history.

**R04 — Never infer missing bibliographic facts.** Do not fabricate titles, authors, publication years, ISBNs, or identity mappings. A checksum confirms mathematical validity, **not** that an ISBN belongs to the book named in the row.

## 2. Book identity and required fields

**R05 — Primary identity.** The supplier `book_id` is the primary book key. It must consist of a positive whole number (digits only, value greater than zero). Blank, negative, fractional, or nonnumeric IDs are invalid. `title` and `authors` must each contain non-whitespace text. If any of these three required fields is invalid, **reject the incoming row** without inserting or changing a book.

**R06 — Secondary Goodreads identity.** A usable `goodreads_book_id` contains digits only and is greater than zero. When present and valid, use it to cross-check `book_id` and detect competing identities. An absent or malformed Goodreads ID is **not by itself grounds to reject** an otherwise valid book under R05; preserve the raw value, leave the cleaned secondary identity unavailable, and record a warning. Never use a Goodreads ID to override a conflicting `book_id` automatically.

**R07 — Reused primary identity.** If an existing `book_id` returns with a different `title` or `authors` after trimming leading/trailing whitespace, **reject the incoming row** and preserve the existing book. Compare trimmed strings exactly and case-sensitively; whitespace-only differences are not identity changes. If the existing record has a different valid Goodreads ID from the incoming record, also reject and flag the identity conflict. Do not silently replace titles, authors, or identifiers.

**R08 — Goodreads identity collision.** If a valid `goodreads_book_id` already identifies a different `book_id`, reject the incoming conflicting row; preserve any previously accepted book. If two **new, nonidentical** rows in the same file claim the same valid Goodreads ID, reject both conflicting rows rather than accepting whichever appears first. An exact repeated row is governed by R12 instead.

**R09 — Different editions are not automatic duplicates.** Different `book_id` values remain distinct even when they share a `work_id`, title, author, or publication year. A shared `work_id` can indicate different editions. Matching title and author across different IDs may generate a potential-duplicate warning but must never trigger an automatic merge. Do not make automatic matches using title, author, and year alone.

## 3. ISBN validation and approved repairs

**R10 — ISBN-10.** After approved formatting cleanup, a valid ISBN-10 contains exactly nine digits followed by a digit or uppercase `X`, and passes the modulus-11 ISBN-10 checksum. Preserve leading zeros.

**R11 — ISBN-13.** A valid ISBN-13 contains exactly 13 digits, starts with `978` or `979`, and passes the ISBN-13 modulus-10 checksum. Values beginning `9790` are treated as ISMN/non-ISBN values and are not accepted as ISBNs.

**R12 — Simple formatting and short ISBN-10s.** Remove spaces and hyphens from ISBN candidates; uppercase a final lowercase `x`. A **7-, 8-, or 9-digit** digit-only ISBN-10 candidate may be padded on the left with zeros to ten characters **only if** the resulting ISBN-10 passes its checksum. Do not pad 1–6-digit strings, remove arbitrary extra digits, or delete unexpected letters. Log every successful repair with raw and cleaned values.

**R13 — ISBN in the wrong column.** If a valid ISBN-13 appears in `isbn` or a valid ISBN-10 appears in `isbn13`, move its cleaned value to the appropriate logical field, provided this creates no conflict with a separate valid identifier. Preserve the raw field text and log the move. For a valid `978` ISBN-13, derive its ISBN-10 counterpart when allowed; for a valid ISBN-10, derive its `978` ISBN-13 counterpart when allowed.

**R14 — Conversion.** A valid ISBN-10 may be converted deterministically to its `978` ISBN-13 equivalent. A valid `978` ISBN-13 may be converted to ISBN-10. A valid `979` ISBN-13 has **no ISBN-10 equivalent**; leave ISBN-10 blank without a missing-ISBN warning. Conversion is a documented derivation, not a guess.

**R15 — Truly missing ISBN field.** When one raw ISBN field is genuinely blank and the other contains a validated, nonconflicting convertible ISBN, derive the missing counterpart and log it. When both ISBN fields are blank, retain an identifiable book with both cleaned ISBNs NULL and a missing-ISBN warning.

**R16 — Scientific notation.** Do **not** expand scientific notation and assume its zero-filled digits are the original ISBN. Derive a candidate ISBN-13 only from a valid ISBN-10 in the **same incoming row**. Using decimal arithmetic, round the derived 13-digit value to the number of significant digits explicitly represented by the original scientific-notation text, using **round half to even**. Accept the derivation only if the rounded value exactly equals the supplied scientific-notation value. No floating-point tolerance, guessing, or alternative candidate search is permitted. If no valid supporting ISBN-10 exists, or the rounding comparison fails, leave the cleaned ISBN-13 NULL and warn.

**R17 — Narrow `.0` exception.** If the raw `isbn13` field contains an ISBN-10-shaped value followed by exactly `.0`, it may be recognized as a misplaced ISBN-10 **only when** removing `.0` yields exactly the same valid ISBN-10 already present in that row's `isbn` field (after permitted simple formatting). Record the confirmation and derive its `978` ISBN-13. All other decimal-looking ISBN values are invalid; do not strip `.0` by default.

**R18 — Incorrect or unrecoverable values.** Never alter a check digit just to make it valid. A wrong check digit, illegal letter, extra digit, truncated number, unrecoverable rounded value, or unrelated barcode/product code (including `2940` bookstore codes, UPC codes, and non-ISBN `622`/`123` codes) is not a valid ISBN. Record the raw value and a warning; do not assign it as a cleaned ISBN. In particular, do not change a trailing `0` to `X` merely because that would pass a checksum.

**R19 — Valid companion ISBN when the other field is bad.** If the raw `isbn13` is an invalid product code or another unusable value, a valid ISBN-10 in the same row **may** supply its derived `978` ISBN-13, with both the invalid source and the derivation recorded. Safeguards:

1. If the original ISBN-13 is rounded scientific notation, R16 must pass; otherwise **do not** derive it.
2. If the original ISBN-13 is a 13-digit `978` candidate with a bad checksum, its first 12 digits must agree with the ISBN-13 derived from the valid ISBN-10; otherwise leave ISBN-13 NULL and flag the contradiction.
3. If the original ISBN-13 is independently valid but disagrees with the valid ISBN-10, use the conflict rule R21, not this repair rule.
4. An **invalid raw ISBN-10** is left NULL with a warning even if an independent valid ISBN-13 exists; do not silently replace the bad ISBN-10 by calculating a new check digit. Derive an ISBN-10 from ISBN-13 only when the raw ISBN-10 is genuinely blank or contains a valid ISBN-13 in the wrong column under R13.

**R20 — No usable ISBN.** A row may still be **loaded** if R05–R08 establish reliable book identity. Store cleaned ISBNs as NULL and warn. An invalid ISBN is a field-level problem, not automatically a reason to reject the entire book.

## 4. Conflicts, duplicates, and safe updates

**R21 — Two valid but inconsistent ISBNs in one incoming row.** When valid ISBN-10 and ISBN-13 values do not represent the same `978` identifier (including a valid ISBN-10 paired with a `979` ISBN-13), do not select one. For a **new** book with trustworthy identity, load it with **both cleaned ISBNs NULL** and a conflict/review warning. For an **existing** book, preserve its previously accepted ISBNs; reject an incoming row that attempts to replace them with a conflicting pair. Preserve the raw values and explain the conflict.

**R22 — ISBN already assigned to another book.** Do not reassign an accepted book's ISBN to a different `book_id`. For a new otherwise identifiable book, load it but leave each conflicting cleaned ISBN NULL, warn, and retain any independent nonconflicting ISBN. Do not derive a counterpart from a conflicting ISBN. If two new books in the **same input file** claim the same valid ISBN and neither has an accepted prior claim, load both books without that disputed ISBN and flag both; this avoids choosing an owner based only on file order.

**R23 — Exact duplicates.** Within a delivery, insert an exact repeated row at most once; mark subsequent identical copies **skipped** with an `exact duplicate in input` reason. When an identical already-accepted book is received on a later run, **skip** it. Compare cleaned ISBN values so harmless differences in formatting do not create an update. No duplicate book may be inserted for an existing `book_id`.

**R24 — Competing nonidentical rows in one delivery.** Two incoming rows that claim the same `book_id` or valid Goodreads ID but have materially different identity, ISBN assignments, or other supplier values must be flagged. If neither has a previously accepted database record and they conflict materially, **reject both** instead of choosing one by order. Preserve an already accepted record and reject any row that conflicts with its identity. An exact duplicate follows R23. A mere formatting change that normalizes to the same value is not a material conflict.

**R25 — Safe ISBN improvement.** When an incoming row is the same reliably identified book, a new valid nonconflicting ISBN may fill a previously missing/invalid cleaned ISBN. Record the previous and new values; classify the row **updated**. A later blank or invalid value must **never** erase a valid stored ISBN.

**R26 — Conflicting ISBN on an existing book.** If an existing book has a validated ISBN and an incoming row for the same `book_id` presents a **different validated ISBN** for the same field, **reject the incoming row for review**; keep the stored record and its ISBN unchanged. Do not update other fields from that rejected row. This is different from R22's handling of a *new* book with an ISBN claimed by somebody else.

**R27 — Approved non-identity updates.** For a reliably matched existing book with no identity/ISBN conflict, allow changes to these supplier metadata fields: `books_count`, `average_rating`, `ratings_count`, `work_ratings_count`, `work_text_reviews_count`, `ratings_1`, `ratings_2`, `ratings_3`, `ratings_4`, `ratings_5`, `image_url`, and `small_image_url`. Compare these stored text values exactly. Update changed, nonblank values and record field, old value, new value, book, and run. **Never replace a nonblank value with a blank.** A row with one or more approved changes is **updated**, not loaded.

**R28 — Identity-sensitive and other fields.** Do not automatically change `book_id`, `goodreads_book_id`, `best_book_id`, `work_id`, `title`, `authors`, `original_title`, `original_publication_year`, or `language_code` on an existing book. Conflicting `book_id`, Goodreads identity, title, or authors follow R07–R08 and are rejected. Differences in the other protected fields are recorded for review without silently changing them; if no approved field changes, the row is **skipped with a review warning**. Title and author comparisons ignore only leading/trailing whitespace, remain case-sensitive, and preserve the originally stored text.

**R29 — Unchanged and warning-only rows.** If a row produces no permitted change, count it **skipped**, even when its incoming values contain a warning or a field that cannot replace an existing good value. Preserve any accepted database record. Do not invent an update to achieve a desired count.

## 5. Database, review history, and outputs

**R30 — Database content.** The output database is `books.db`. It must contain one accepted book per primary `book_id`, the 23 supplier fields, and separate normalized ISBN-10 and ISBN-13 values. Database table/column layout may differ between partners; the logical book identity, accepted values, and outcomes must follow this document. Store raw ISBNs separately from cleaned ISBNs.

**R31 — Review and change history.** Maintain a persistent record of rejected rows, warnings/review items, and accepted field changes, including original input and reasons. This information must be in `ingestion-log.json`; it may also be stored in dedicated review and change-history tables in `books.db` (Asha's original approach). Do not delete accepted books or erase earlier history when a new delivery arrives.

**R32 — Four mutually exclusive outcomes.** Every data row read has exactly one primary outcome:

- **Loaded:** a new book was inserted, including a new book with ISBN warnings.
- **Updated:** a previously accepted book had at least one permitted value changed.
- **Skipped:** no permitted database change, including an unchanged repeat or exact duplicate.
- **Rejected:** the incoming row was not accepted because required identity, contradictory identity, or row structure was unacceptable.

Warnings, repairs, and review flags are **additional attributes**, not fifth outcomes. For each successful input-file run, `loaded + updated + skipped + rejected = rows_read`.

**R33 — Append-only run log.** Every invocation appends one run record to `ingestion-log.json` without overwriting earlier runs, including failed file-level runs. Include filename, SHA-256 fingerprint of readable source bytes, date/time with local timezone, run status, rows read, all four outcome counts, warning count, issue counts, repairs, skips, rejections, updates, and reasons. Use date `yyyy-MM-dd` and a time containing `HH:mm:ss` and timezone. If a fingerprint cannot be calculated because the file cannot be read, record that it is unavailable.

**R34 — Detailed traceability.** For each affected row, record the actual CSV line number (first data record begins at line 2), `book_id` and title when available, raw ISBN values, cleaned/derived ISBN values, rule applied, outcome, warning/rejection reason, and any old/new changed field values. Record a separate reason for `exact duplicate in input` versus `already exists`. Report what a human should review. Keep the original bad values even when the book is loaded with NULL cleaned ISBNs.

**R35 — Self-contained HTML report.** `report.html` must open locally without a web server, allow the user to select `ingestion-log.json`, and display all runs newest first. Show header information, totals, issue counts, repairs, warnings, skipped/rejected records, and field changes, including the original and repaired values.

**R36 — Output locations and command.** Keep `books.db` and `ingestion-log.json` beside `ingest.py`, regardless of the terminal's current working directory. Accept the CSV filename/path as a command-line argument, e.g. `python ingest.py books.csv` on Windows or `python3 ingest.py books.csv` where appropriate. Do not hard-code one input file.

## 6. Error handling and repeatability

**R37 — File-level failure.** A missing or unreadable file, non-UTF-8 input, or invalid CSV header is a **failed run**. Do not insert, update, or delete accepted database books. Append a failure entry to the run log with the reason and available file details. Do not treat an unreadable file as an empty successful delivery.

**R38 — Malformed data row.** If an otherwise readable CSV has a data row with more or fewer than the required 23 fields, **reject that row** with `row has N fields, expected 23`, count it as rejected, and continue processing other rows. Preserve available row data and line number in the report.

**R39 — Determinism and three-run sequence.** Apply the same rules by pattern, not by a hard-coded list of book numbers. The expected sequence is: (1) ingest `books.csv`; (2) ingest the identical `books.csv` again; (3) ingest `books-batch2.csv`. The second run must not insert duplicates or create spurious updates. An identical fresh start with the same input and rules must produce the same logical database, regardless of who runs it.

**R40 — Test isolation.** Automated pytest tests must use a separate `test.db` and isolated temporary logs/files; they must never read/write the production `books.db` or production `ingestion-log.json` as test outputs. Tests must clean up created files after passing **or failing**. Every test must be traceable to a rule or example in this document. The two partners write their test suites and modify their ingestion programs independently.

---

## 7. Agreed ISBN and ingestion examples — source of truth for tests

**How to read the table:** Unless a row explicitly says otherwise, assume a **new** book with a unique valid positive `book_id`, a nonblank `title` and `authors`, no conflicting Goodreads ID, and no prior database record. `NULL` means an absent cleaned ISBN value, not the text `"NULL"`. Raw input strings remain preserved. The outcomes below are required by the numbered rules, not predictions from either student's old Python script.

| # | Input / situation | Expected cleaned ISBN-10 | Expected cleaned ISBN-13 | Primary outcome | Required action or explanation |
|---|---|---|---|---|---|
| 1 | `isbn=0439139600`; matching `isbn13=9780439139601` | `0439139600` | `9780439139601` | Loaded | Both valid; no repair. |
| 2 | `isbn=439023483`; `isbn13=9.78043902348e+12` (*The Hunger Games*) | `0439023483` | `9780439023481` | Loaded | Pad ISBN-10 and reconstruct ISBN-13 only after exact half-even rounding confirmation; log both repairs. |
| 3 | `isbn=61120081`; `isbn13` blank | `0061120081` | `9780061120084` | Loaded | Pad 8-digit ISBN-10, validate, derive missing ISBN-13, log repairs. |
| 4 | `isbn=0-450-19827-8`; `isbn13=9780450198274` (*Salt and Ember*) | `0450198278` | `9780450198274` | Loaded | Remove hyphens; log formatting repair. |
| 5 | `isbn=162 001 4750`; `isbn13=9781620014752` | `1620014750` | `9781620014752` | Loaded | Remove spaces; log formatting repair. |
| 6 | `isbn=031200009x`; `isbn13=9780312000097` (*Winter Orchard*) | `031200009X` | `9780312000097` | Loaded | Uppercase final `x`; log repair. |
| 7 | `isbn=9781982147600`; `isbn13` blank (*An Atlas of Small Kindnesses*) | `1982147601` | `9781982147600` | Loaded | Move valid ISBN-13 to correct cleaned field; derive ISBN-10; log. |
| 8 | `isbn` blank; `isbn13=9780062398123` | `0062398121` | `9780062398123` | Loaded | Derive ISBN-10 from valid 978 ISBN-13; log. |
| 9 | `isbn` blank; `isbn13=9798351220413` | NULL | `9798351220413` | Loaded | Valid 979; no missing-ISBN-10 warning. |
| 10 | `isbn=1416524797`; `isbn13=9.78141652479e+12` (*Angels & Demons*) | `1416524797` | `9781416524793` | Loaded | Scientific-notation value matches derived ISBN-13 at supplied precision; log reconstruction. |
| 11 | `isbn` blank; `isbn13=9.78067172365e+12` (*How to Win Friends and Influence People*) | NULL | NULL | Loaded | No valid companion ISBN-10; do not guess rounded ISBN-13; warn. |
| 12 | `isbn` blank; `isbn13=9.7804251888e+12` (*Watchers*) | NULL | NULL | Loaded | Rounded ISBN-13 ambiguous; leave NULL and warn. |
| 13 | `isbn=1854433017`; `isbn13` blank (*Small Hours*) | NULL | NULL | Loaded | Wrong check digit; do not change it; warn. |
| 14 | `isbn=04390A3483`; `isbn13` blank (*The Glass Aviary*) | NULL | NULL | Loaded | Illegal interior letter; warn, do not invent replacement. |
| 15 | `isbn=15287463097`; `isbn13` blank (*Borrowed Light*) | NULL | NULL | Loaded | Extra digit; do not truncate; warn. |
| 16 | `isbn` blank; `isbn13=2940016231920` | NULL | NULL | Loaded | Product code is not ISBN; warn. |
| 17 | `isbn=189221301X`; `isbn13=6.45573003012e+11` (*Sailor Moon #1*) | `189221301X` | `9781892213013` | Loaded | Reject raw product code, derive from valid ISBN-10; log invalid code, warning, and derivation. |
| 18 | `isbn=0439139600`; `isbn13=0439139600.0` | `0439139600` | `9780439139601` | Loaded | Narrow `.0` exception: exact match to validated ISBN-10; log confirmation and derivation. |
| 19 | `isbn` blank; `isbn13=195170342.0` | NULL | NULL | Loaded | Unconfirmed decimal form; no stripping or guessing; warn. |
| 20 | Both raw ISBN fields blank (*Bossypants* or *Untitled Manuscript*) | NULL | NULL | Loaded | Required `book_id`, title, authors are valid; missing-ISBN warning. |
| 21 | `isbn=1555550126`; `isbn13=9781555550134` (*Tides of Marrow Bay*) | NULL | NULL | Loaded | Both valid individually but not a matching pair; preserve book, flag conflict for review. |
| 22 | `isbn=1416524797`; `isbn13` blank (*Paper Moons*), after *Angels & Demons* already owns that ISBN | NULL | NULL | Loaded | Do not reuse another book's ISBN or derive from it; warn and flag. |
| 23 | `isbn=0446612707`; `isbn13=9780553813951` (*Nights in Rodanthe*) | NULL | NULL | Loaded | Two valid but mismatched ISBNs; preserve book without either cleaned ISBN; flag. |
| 24 | `isbn=1528746309`; `isbn13=9781528746304`; exact same new row occurs twice in one file | `1528746309` | `9781528746304` | Loaded, then Skipped | First occurrence loaded; second marked exact duplicate; two source rows counted. |
| 25 | Previously accepted book 24 repeats with `isbn=0439139600`, `isbn13=9780439139601`, same other values | Unchanged | Unchanged | Skipped | Normalized values match prior accepted values; no new record or update. |
| 26 | Existing book 25 has valid `0545010225`; later row has both ISBNs blank | Preserve existing | Preserve existing | Skipped | Never erase accepted ISBNs with blanks; warn only if appropriate to incoming row. |
| 27 | Existing book 132 has the same identity and ISBNs, but `ratings_count` changes from `449501` to `451425` | Preserve existing | Preserve existing | Updated | Update approved metadata; record old and new ratings counts. |
| 28 | Same `book_id` returns with a different non-whitespace `title` or `authors` | Preserve existing | Preserve existing | Rejected | Identity conflict; leave existing record unchanged. |
| 29 | Two new rows use different `book_id` values but the same valid `goodreads_book_id` | NULL if no accepted ISBNs | NULL if no accepted ISBNs | Both Rejected | Competing book identities; do not pick by row order. |
| 30 | New book has valid `book_id`, title, authors, but no Goodreads ID and no ISBN | NULL | NULL | Loaded | Primary identity is sufficient; warn about missing Goodreads ID and ISBNs. |
| 31 | Two new books in one delivery claim the same valid ISBN but have distinct reliable identities | NULL for disputed ISBN | NULL for disputed ISBN | Both Loaded | Flag both for review; neither gets the disputed cleaned ISBN. |
| 32 | Existing book has a validated ISBN; later row presents a different validated ISBN for that same field | Preserve existing | Preserve existing | Rejected | Do not overwrite accepted ISBN or update metadata from conflicting row. |
| 33 | A row has a blank/invalid `book_id` or a blank `title` or `authors` | Not assigned | Not assigned | Rejected | Required identity missing; no accepted book inserted. |
| 34 | Existing book lacks a cleaned ISBN; same book later supplies a valid nonconflicting ISBN | Fill valid ISBN | Fill/derive permitted counterpart | Updated | Safe ISBN improvement; log old and new values. |
| 35 | `isbn=812971060`; `isbn13=9.78081297106e+12` (*Reading Lolita in Tehran*) | NULL | NULL | Loaded | Invalid ISBN-10 and unsupported rounded ISBN-13; do not change `0` to `X`; warn. |
