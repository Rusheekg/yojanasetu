# Expected results for the demo citizens

Synthetic citizens only (fake first names; no Aadhaar or phone numbers).
Source data: `fixtures/citizens.csv`, `fixtures/citizen_links.csv`.

Each citizen has:
- **Must hold**: the demo story. A test fails if any of these is wrong.
- **Full result**: worked out by hand from `data/scheme_criteria.csv` and
  `data/document_links.csv` (10 schemes, including the `bride_to_be` rule)
  using the rules in `docs/graph_model.md`. Update it whenever scheme data
  changes.

Buckets (`docs/graph_model.md`, section 1.4 rule 9):
- `needs_info` – every failed check is an unknown numeric value; `ask_for` lists the facts.
- `near_miss` – not excluded, all categorical checks pass, exactly one numeric check fails; `gap_check` shows the gap.
- `not_eligible` – everything else, including exclusions. Never stored in session memory.

`find_almost_eligible` returns only `needs_info` and `near_miss` rows.

---

## 1. demo_lakshmi – 58, female, farmer, BC, Telangana, ₹1,50,000, 2 acres

Holds: aadhaar, bank_account, pattadar_passbook. No statuses.

**Must hold**
- `find_eligible` includes `pm_kisan`, `rythu_bharosa`, `rythu_bima`, `mahalakshmi`.
- `pm_kmy` is `near_miss`: `gap_check` = `max_age`, required 40, actual 58, gap 18.
- `kalyana_lakshmi` is not eligible (she is not `bride_to_be`).

**Full result**
- Eligible: `pm_kisan`, `rythu_bharosa`, `rythu_bima`, `mahalakshmi`.
- `near_miss`: `pm_kmy` (max_age, 18 years over).
- `needs_info`: none.
- `not_eligible` (examples): `kalyana_lakshmi` (status), `pmuy` (status bpl),
  `shaadi_mubarak` (category, status).
- `pm_kisan` `exclusions_to_confirm`: all 6 excluded statuses (none recorded yet).

## 2. demo_ravi – 35, male, farmer, Telangana, ₹1,20,000, 3 acres

Holds: aadhaar only.

**Must hold**
- `document_path(demo_ravi, pm_kisan)` lists `bank_account` with
  `needs_first = [Aadhaar card]` and aadhaar `done = true`; `pattadar_passbook` not done.
- `best_next_document(demo_ravi)` = `pattadar_passbook`.

**Full result**
- Eligible: `pm_kisan`, `pm_kmy`, `rythu_bharosa`, `rythu_bima`.
- `near_miss`: none. `needs_info`: none.
- `not_eligible` (example): `mahalakshmi` (gender).
- `best_next_document` ranking (made ready, moved forward):
  1. `pattadar_passbook` (1, 4) – makes `rythu_bima` fully ready via the `land_proof` group
  2. `rofr_patta` (1, 2)
  3. `bank_account` (0, 3)

## 3. demo_priya – 22, female, SC, bride_to_be, Telangana, ₹2,20,000, land unknown

Holds: aadhaar, bank_account, caste_certificate.

**Must hold**
- `kalyana_lakshmi` is `near_miss`: `gap_check` = `max_income`, required 200000,
  actual 220000, gap 20000, basis household.

**Full result**
- Eligible: `mahalakshmi`.
- `near_miss`: `kalyana_lakshmi` (max_income, ₹20,000 over).
- `needs_info`: none.
- `not_eligible` (examples): `shaadi_mubarak` (category, max_income),
  `pmuy` (status bpl), `pm_kisan` / `rythu_bharosa` / `rythu_bima` (occupation),
  `pm_kmy` (occupation; land unknown does not help because a categorical check fails).

## 4. demo_suresh – 45, male, farmer, Telangana, ₹9,00,000, 5 acres, income-tax payer

**Must hold**
- `explain_not_eligible(demo_suresh, pm_kisan)`: bucket `not_eligible`,
  `excluded_by` contains status `income_tax_payer`.
- `explain_not_eligible(demo_suresh, pm_kmy)`: bucket `not_eligible`,
  `excluded_by` contains status `income_tax_payer`.
- Neither scheme appears in `find_almost_eligible` (exclusions are never near misses).

**Full result**
- Eligible: `rythu_bharosa`, `rythu_bima`.
- `near_miss`: none. `needs_info`: none.
- `pm_kisan` fails only `excluded`.
- `pm_kmy` fails `max_age` (45 > 40), `max_land_acres` (5 > 4.94) and `excluded`.

## 5. demo_kamala – female, widow, BPL, Telangana, age/income/land unknown

**Must hold**
- `ignwps` is `needs_info` with `ask_for` = `[age]` after de-duplication
  (raw: `min_age` and `max_age`, both `actual = null`).
- `ignoaps` is `needs_info` with `ask_for` = `[age]`.
- The agent asks her age instead of saying "not eligible" for either scheme.

**Full result**
- Eligible: `mahalakshmi`.
- `near_miss`: none.
- `needs_info`: `ignoaps` (age), `ignwps` (age), `pmuy` (age).
- `pmuy` `exclusions_to_confirm`: `lpg_connection_holder`.
- `not_eligible` (examples): `kalyana_lakshmi` (category, status; age and
  income unknown too), `pm_kisan` (occupation).

## 6. demo_fatima – 20, female, minority, bride_to_be, Telangana, ₹1,50,000, land unknown

Holds: aadhaar, bank_account, income_certificate.

**Must hold**
- `find_eligible` includes `shaadi_mubarak`.
- `kalyana_lakshmi` is `not_eligible`; its only failed check is `category`.

**Full result**
- Eligible: `shaadi_mubarak`, `mahalakshmi`.
- `near_miss`: none. `needs_info`: none.
- `not_eligible` (examples): `kalyana_lakshmi` (category), `pmuy` (status bpl),
  `pm_kisan` / `rythu_bharosa` / `rythu_bima` (occupation).

---

## Resolved gaps

| # | Gap | Resolution |
|---|---|---|
| G1 | Unmarried-bride rule was only a note | Status `bride_to_be` + `for_status` rule on `kalyana_lakshmi` and `shaadi_mubarak` (D12) |
| G2 | Exclusion counted as a near miss | Exclusions are always `not_eligible` (D13) |
| G3 | Unknown age failed two checks, hiding IGNWPS | Unknown-only failures are `needs_info`, regardless of how many checks (D13) |
| G4 | Every non-farmer was a near miss on farmer schemes | A categorical failure is `not_eligible` and is not stored (D13) |
