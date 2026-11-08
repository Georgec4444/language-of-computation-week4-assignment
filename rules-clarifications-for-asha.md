# Rule Clarifications for Asha — Module 4

**Course:** MSAI-5523 — Language of Computation
**From:** George (`Georgec4444`)
**For:** Asha (`AshaAbdulkadir`)
**Rules version:** `rules.md` 1.0 — November 6, 2026
**Date:** November 7, 2026

## Purpose

While updating my `ingest.py` to pass our shared test examples, I found nine places where `rules.md` doesn't fully decide what the program should do. None of these cases is one of the 35 agreed examples, so our tests don't settle them. For each one I picked the most literal or most cautious reading and **did not invent a new rule**. Because we both implement the same rules independently, we should agree on each answer. Then we can either update `rules.md` together or confirm that the current wording is enough.

Each item lists the rule, what `rules.md` says now, the gap, what my program currently does, a concrete example (checked against my current program), and a question for us to decide.

## Summary

| # | Rule(s) | Topic | My program's current behavior |
|---|---|---|---|
| 1 | R16, R19 | When is scientific notation "rounded"? | Rounded only if it shows fewer digits than the number has |
| 2 | R17, R19 | Can R19 replace a decimal-looking ISBN-13? | No: R17 alone decides decimal values |
| 3 | R19 | Bad `979` / `9790` ISBN-13 with a valid ISBN-10 | Derives the `978` ISBN-13 from the ISBN-10 |
| 4 | R21 | Conflicting pair for an existing book with no ISBNs | Skipped with a review flag (not rejected) |
| 5 | R03 | How a blank raw ISBN cell is stored | Stored as empty text `""`, not NULL |
| 6 | R23, R24 | Same `book_id` twice in one file, formatting-only difference | First loaded; second skipped as "already exists" |
| 7 | R23 | Exact duplicate of a row that was rejected | Second copy skipped as "exact duplicate in input" |
| 8 | R25, R26 | New ISBN for an empty field that contradicts the other stored ISBN | Flagged and skipped; nothing changed |
| 9 | R13, R19 (safeguard 4) | Invalid `isbn` plus a valid ISBN-10 in the `isbn13` column | Moves the ISBN-10 into place and derives the ISBN-13 |

---

## 1. When is scientific notation "rounded"?

**Rule:** R16, R19 (safeguard 1)

**What the rules say:**
- R16: "Do **not** expand scientific notation and assume its zero-filled digits are the original ISBN … round the derived 13-digit value to the number of significant digits explicitly represented by the original scientific-notation text … Accept the derivation only if the rounded value exactly equals the supplied scientific-notation value."
- R19, safeguard 1: "If the original ISBN-13 is rounded scientific notation, R16 must pass; otherwise **do not** derive it."
- Example 17: `isbn=189221301X`; `isbn13=6.45573003012e+11` → derive `9781892213013`.

**Ambiguity:** The rules never define "rounded" scientific notation. If every scientific-notation value counts as rounded, safeguard 1 applies to example 17. R16 would then fail, since `9781892213013` can never round to `645573003012`, and the example's expected result would contradict the rule.

**What my program does:** A value counts as rounded only when it shows fewer digits than the number really has (`digits shown < exponent + 1`). `6.45573003012e+11` shows all 12 digits of a 12-digit number, so it's exact, not rounded. It's treated as an unusable product code, and R19 derives the ISBN-13. A rounded non-ISBN value must still pass R16, so in practice it never produces an ISBN-13.

**Concrete example:**

| `isbn` | `isbn13` | Digits shown / actual | My result for cleaned ISBN-13 |
|---|---|---|---|
| `189221301X` | `6.45573003012e+11` | 12 / 12 (exact) | `9781892213013` (derived, per example 17) |
| `189221301X` | `6.4557300301e+11` | 11 / 12 (rounded) | NULL with warning (safeguard 1) |

**Question for us:** Do we agree that "rounded scientific notation" means a value showing fewer significant digits than the number has, so that an exact scientific-notation product code like example 17 falls under R19's main rule? Should we add that definition to R16?

---

## 2. Can R19 replace a decimal-looking ISBN-13?

**Rule:** R17, R19

**What the rules say:**
- R17: "If the raw `isbn13` field contains an ISBN-10-shaped value followed by exactly `.0`, it may be recognized as a misplaced ISBN-10 **only when** removing `.0` yields exactly the same valid ISBN-10 already present in that row's `isbn` field … All other decimal-looking ISBN values are invalid; do not strip `.0` by default."
- R19: "If the raw `isbn13` is an invalid product code or another unusable value, a valid ISBN-10 in the same row **may** supply its derived `978` ISBN-13."

**Ambiguity:** An unconfirmed decimal value is "invalid" under R17, which could make it "another unusable value" under R19. Read that way, R19 would derive the ISBN-13 anyway. That gives the same result as accepting the decimal, which seems to defeat R17's "narrow" exception. The decimal might even point to a *different* ISBN-10 than the row's `isbn` field. R19's safeguards don't mention decimal values.

**What my program does:** R17 alone decides decimal-looking `isbn13` values. If R17's exact-match test fails, the cleaned ISBN-13 stays NULL with a warning, and R19 does **not** derive one from the ISBN-10.

**Concrete example:**

| `isbn` | `isbn13` | My result for cleaned ISBN-10 / ISBN-13 |
|---|---|---|
| `0439139600` | `0439139600.0` | `0439139600` / `9780439139601` (R17 confirmed, example 18) |
| `0439139600` | `0439139601.0` | `0439139600` / NULL (unconfirmed, and points to a different ISBN-10) |
| `0439139600` | `9780439139601.0` | `0439139600` / NULL (not ISBN-10-shaped, so invalid under R17) |

**Question for us:** When an `isbn13` value is decimal-looking and R17 doesn't confirm it, should R19 still derive the ISBN-13 from a valid ISBN-10 in the row, or should it stay NULL as my program does now?

---

## 3. Bad `979` or `9790` ISBN-13 with a valid ISBN-10

**Rule:** R19 (safeguards 2 and 3), R11, R14

**What the rules say:**
- R19, safeguard 2: "If the original ISBN-13 is a 13-digit `978` candidate with a bad checksum, its first 12 digits must agree with the ISBN-13 derived from the valid ISBN-10; otherwise leave ISBN-13 NULL and flag the contradiction."
- R11: values beginning `9790` "are treated as ISMN/non-ISBN values and are not accepted as ISBNs."
- R14: a valid ISBN-10 converts only to its `978` ISBN-13.

**Ambiguity:** Safeguard 2 covers only `978` candidates. A 13-digit `979` value with a bad checksum, or a `9790` ISMN value, falls under R19's general "unusable value" wording. That would allow deriving a `978` ISBN-13, even though the supplier's value claims a different prefix that can never match an ISBN-10 conversion.

**What my program does:** It applies R19 literally. The bad `979`/`9790` value gets a warning, and the `978` ISBN-13 is derived from the valid ISBN-10, with the derivation logged.

**Concrete example:**

| `isbn` | `isbn13` | My result for cleaned ISBN-10 / ISBN-13 |
|---|---|---|
| `0439139600` | `9798351220410` (979, bad checksum) | `0439139600` / `9780439139601` (derived, R19) |
| `0439139600` | `9790439139601` (9790 ISMN) | `0439139600` / `9780439139601` (derived, R19) |

**Question for us:** Should a `979` or `9790` value be treated like safeguard 2's contradiction case (ISBN-13 NULL plus a flag), since its prefix can never agree with a `978` derivation? Or is deriving from the valid ISBN-10 acceptable, as my program does now?

---

## 4. Conflicting ISBN pair for an existing book with no stored ISBNs

**Rule:** R21 (with R25, R29)

**What the rules say:**
- R21: "For an **existing** book, preserve its previously accepted ISBNs; reject an incoming row that attempts to replace them with a conflicting pair."

**Ambiguity:** R21 covers an existing book that *has* accepted ISBNs. It doesn't say what happens when the existing book has **no** cleaned ISBNs, so there is nothing to "replace." Rejecting the row would treat a harmless field-level problem as an identity failure. Accepting either ISBN would break R21's "do not select one."

**What my program does:** If the existing book has any cleaned ISBN, the row is **rejected** (R21). If it has none, no ISBN is accepted, a review flag is logged, and the row follows R29: **skipped** if nothing else changes, or **updated** if approved metadata changes.

**Concrete example:**
- First delivery: book 4 has both ISBN fields blank → loaded with cleaned ISBNs NULL.
- Later delivery: book 4, same identity, with `isbn=1555550126` and `isbn13=9781555550134` (each valid, but not a matching pair, as in example 21).
- My result: **skipped**, with a review flag ("incoming ISBN pair conflicts; no ISBN accepted"), and the cleaned ISBNs stay NULL.

**Question for us:** For an existing book with no accepted ISBNs, should a conflicting incoming pair be **skipped with a review flag** (my program's current behavior) or **rejected**?

---

## 5. How a blank raw ISBN cell is stored

**Rule:** R03 (with R30)

**What the rules say:**
- R03: "Preserve the original `isbn` and `isbn13` source text exactly as received (including original formatting), and store cleaned ISBNs separately; for other supplier fields, whitespace-only cells are treated as missing/NULL for normalized storage."

**Ambiguity:** "Exactly as received" is clear for a cell that contains text. For an empty cell it could mean empty text (`""`) or NULL, and the rule only spells out NULL handling for the *other* fields. This changes how a database query has to look for "no raw ISBN," and it could make our two databases differ.

**What my program does:** It stores the raw `isbn`/`isbn13` text exactly as the CSV gave it, so a blank cell is stored as empty text `""`. Cleaned ISBN columns use NULL. Other supplier fields that are blank or whitespace-only are stored as NULL.

**Concrete example:**
- Input: `isbn` blank, `isbn13=9798351220413` (example 9).
- My stored values: `isbn = ''` (empty text), `isbn13 = '9798351220413'`, `isbn10_clean = NULL`, `isbn13_clean = '9798351220413'`.

**Question for us:** Should a blank raw `isbn`/`isbn13` cell be stored as empty text (exactly as received) or as NULL, like the other supplier fields?

---

## 6. Same `book_id` twice in one file, differing only in formatting

**Rule:** R23, R24, R34

**What the rules say:**
- R23: "Within a delivery, insert an exact repeated row at most once; mark subsequent identical copies **skipped** with an `exact duplicate in input` reason … Compare cleaned ISBN values so harmless differences in formatting do not create an update."
- R24: "A mere formatting change that normalizes to the same value is not a material conflict."
- R34: "Record a separate reason for `exact duplicate in input` versus `already exists`."

**Ambiguity:** Two rows in the same file that differ only in formatting aren't *exact* duplicates (R23), and they aren't a material conflict (R24). The rules don't say which skip reason the second row should get. It's a repeat within the input, but not an exact one.

**What my program does:** The first row is loaded. The second row is then compared with the book that was just inserted, like any repeat of an existing book. Its cleaned values match, so it's **skipped** with the reason "already exists".

**Concrete example:**
- Line 2: book 6 with `isbn=0-439-13960-0` → **loaded** (hyphens removed; ISBN-13 derived).
- Line 3: book 6 with `isbn=0439139600`, all other values the same → **skipped**, reason "already exists: book_id 6 has no permitted change".

**Question for us:** For a second row in the same file that differs only by formatting, should the reason be "already exists" (current), "exact duplicate in input", or a third reason such as "normalized duplicate in input"?

---

## 7. Exact duplicate of a row that was rejected

**Rule:** R23 (with R08, R26, R32)

**What the rules say:**
- R23: "Within a delivery, insert an exact repeated row at most once; mark subsequent identical copies **skipped** with an `exact duplicate in input` reason."
- R08: "An exact repeated row is governed by R23 instead."

**Ambiguity:** R23 was written for repeats of a row that was *accepted*. It doesn't say what happens when the first copy was **rejected**. The second copy could be skipped as a duplicate (R23 literally), or rejected for the same reason as the first. The choice changes the rejected and skipped counts.

**What my program does:** Exact duplicates are detected before any other check except row structure (R38) and required identity (R05). So the second identical copy of a rejected row is **skipped** as "exact duplicate in input", whatever happened to the first copy. Rows that fail R05 or R38 are rejected every time.

**Concrete example:**
- Earlier delivery: book 7 accepted with `isbn=0439139600`.
- New delivery: two identical rows for book 7, each with a different valid ISBN `1620014750`.
- My result: line 2 **rejected** (R26: different validated ISBN); line 3 **skipped** ("exact duplicate in input (same as line 2)").

**Question for us:** Should an exact copy of a rejected row be **skipped** as an exact duplicate (current), or **rejected** with the same reason as the first copy?

---

## 8. New ISBN for an empty field that contradicts the other stored ISBN

**Rule:** R25, R26 (with R21, R29)

**What the rules say:**
- R25: "When an incoming row is the same reliably identified book, a new valid nonconflicting ISBN may fill a previously missing/invalid cleaned ISBN … classify the row **updated**."
- R26: "If an existing book has a validated ISBN and an incoming row for the same `book_id` presents a **different validated ISBN** for the same field, **reject the incoming row for review**."

**Ambiguity:** R26 covers a conflict in the **same** field. Here the incoming ISBN fills a field that is empty, but it doesn't match the book's *other* stored ISBN. That makes it not "nonconflicting" (so R25 doesn't allow it), but it isn't R26's same-field case either. The rules don't say whether this row is rejected or skipped.

**What my program does:** The fill isn't applied. A conflict is flagged and the stored values are unchanged. With no other approved change, the row is **skipped** (R29), not rejected.

**Concrete example:**
- Earlier delivery: book 8 with `isbn=1854433017` (bad check digit → cleaned ISBN-10 NULL) and `isbn13=9780062398123` (valid).
- Later delivery: book 8 with `isbn=0439139600` (valid) and `isbn13=195170342.0` (unusable decimal, so nothing is derived).
- The incoming ISBN-10 `0439139600` would fill the empty cleaned ISBN-10, but it doesn't correspond to the stored `9780062398123`.
- My result: **skipped**, with a conflict flag; the stored ISBN-10 stays NULL and the ISBN-13 stays `9780062398123`.

**Question for us:** When an incoming ISBN would fill an empty field but contradicts the book's other accepted ISBN, should the row be **skipped with a flag** (current) or **rejected for review** like R26?

---

## 9. Invalid `isbn` plus a valid ISBN-10 in the `isbn13` column

**Rule:** R13, R19 (safeguard 4)

**What the rules say:**
- R13: "If … a valid ISBN-10 appears in `isbn13`, move its cleaned value to the appropriate logical field, provided this creates no conflict with a separate valid identifier."
- R19, safeguard 4: "An **invalid raw ISBN-10** is left NULL with a warning even if an independent valid ISBN-13 exists; do not silently replace the bad ISBN-10 by calculating a new check digit."

**Ambiguity:** When the `isbn` field holds an invalid value and the `isbn13` field holds a valid ISBN-10, R13 says to move it, because the invalid value isn't "a separate valid identifier." But safeguard 4 says an invalid raw ISBN-10 is left NULL. It's unclear whether the safeguard only forbids *calculating* a replacement (which moving doesn't do) or forbids *any* replacement of a bad `isbn`.

**What my program does:** It applies R13. The valid ISBN-10 is moved into the cleaned ISBN-10, the matching `978` ISBN-13 is derived, and the log records a warning for the invalid `isbn` value, the move, and the derivation. It never calculates a new check digit.

**Concrete example:**
- Input: `isbn=1854433017` (bad check digit, as in example 13); `isbn13=0439139600` (a valid ISBN-10 in the wrong column).
- My result: cleaned ISBN-10 `0439139600`, cleaned ISBN-13 `9780439139601`.
- Logged: a warning that `1854433017` fails the checksum; a warning that the invalid `isbn` was replaced by the ISBN-10 found in `isbn13`; the move (R13); and the derivation (R14/R15).

**Question for us:** In this case, should the valid ISBN-10 found in `isbn13` be moved into place (current, R13), or should safeguard 4 apply so the cleaned ISBN-10 stays NULL, given the row's two ISBN fields disagree?

---

## Next steps

1. Asha and I discuss each question and record our agreed answer.
2. If an answer changes or adds to a rule, we update **both** copies of `rules.md` identically and increase the version number.
3. Each of us then updates our own tests and `ingest.py` independently, as R40 and our working agreement require.
