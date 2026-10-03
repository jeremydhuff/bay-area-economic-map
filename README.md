bay area GDP share, income vs. national average, and other stats 

## Updating the data

> **Status: unverified.** `update_data.py` and this procedure were written but never completed a successful end-to-end test run (the test runs timed out pulling from FRED/BLS). The pull-and-compute logic mirrors the manual update done on 2026-10-03, which was verified by hand. On the next update, run it with `--dry-run` first and compare the output to the page; if it fails or disagrees, fall back to the manual steps in the comment at the top of `index.html`. Pulls are slow (about 50 downloads, run one after another).

When asked to "update the site" / "update the whole thing", do a **full update across the whole site**, labeling the latest period by quarter (e.g. "2026 Q2", never "H1"), and refresh every part that depends on the data.

1. Run the data script from the project folder (needs Python and `pip install openpyxl`; no API keys):

   ```
   python update_data.py            # add --dry-run to preview, --refit to re-estimate the wage pass-through
   ```

   It pulls and rewrites the data inside `index.html`:

   | Data | Source | Used for |
   |---|---|---|
   | California quarterly GDP (`CANQGSP`), US quarterly GDP (`GDP`), US annual (`GDPA`) | FRED (`fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>`) | `gammaQ`, `gamma` (CA share of US), `usGDP` (whole 2022+ series is revised each vintage) |
   | County total wages, latest four quarters vs 2024 | BLS QCEW (`data.bls.gov/cew/data/api/<year>/<qtr>/area/<fips>.csv`) | tilts the 2026 county GDP allocation (pass-through 0.76 fitted on 2019-24) |
   | County population, Jan 1 prior vs Jan 1 latest | California DOF E-1 (`dof.ca.gov/forecasting/demographics/estimates-e1/`, published each May) | 2026 county population (1 July, end of Q2), Bay total and share |

2. Update the prose that quotes those numbers (the script prints a checklist): methodology gamma sentence, stat tiles, chart end labels, aria-labels, "added N since 2000" / "recovered" sentences, sources block ("to 20XX Qn"), footer date.
3. Open the page: no console errors, the county GDP sum equals the headline GDP, both views (share and county map) look right.
4. Commit and push.

**Manual cases the script does not handle:**
- *BEA publishes new county GDP* (about December; adds 2025): extend `gdpShare`, `beta24`, each county's `gdp2024USD` (rename to the new year), the QCEW base year (`BASE_YEAR`), the "measured" cutoffs in the script and copy, and `piShare` if county personal income is also out. Refit with `--refit`.
- *New calendar year in the data* (e.g. 2027 Q1): the page ends at 2026, so extend `years` and the arrays, `Y1`, and the model loop by hand first (the script stops and says so).
- *Census Vintage 2026 population* (about December): replace the Census base (`bayPop`, `popShare`, each `population2025`) with the new July 1 estimates before the DOF growth step.
- *US GDP for the final year* is the mean of the quarters so far (so 2026 is the Q1-Q2 mean), while the CA share uses the latest quarter alone.

More detail is in the comment at the top of `index.html`.
