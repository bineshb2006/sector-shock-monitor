# sector-shock-monitor
**A free, interactive early-warning tool for bank/SME lending portfolios — which sectors are sensitive to oil price and supply chain stress, and how many months of warning you actually get.**

[Live demo](https://bineshb2006.github.io/sector-shock-monitor/) · [GitHub repo](https://github.com/bineshb2006/sector-shock-monitor)

## What it is

Enter your loan portfolio's sector mix (e.g. 50% apparel manufacturers, 50% chemicals manufacturers) and get:

- A **weighted growth-impact forecast** for the portfolio as a whole, under either today's actual conditions or a hypothetical scenario you set yourself
- A **time horizon** — how many months before that impact typically shows up, based on the sector mix
- A **confidence breakdown** — how much of the portfolio sits in sectors where the model's relationship is statistically robust, versus sectors where it's weak or unproven
- Per sector: **baseline** growth (what that sector does in calm conditions), **forecast** growth (under the active scenario), and the **delta between them** — so a shock that's still dragging a sector down is visible even when the absolute forecast number is positive

**Two scenario modes:**
- **Current conditions** — uses today's actual Brent crude price and GSCPI level, refreshed monthly by the automated pipeline
- **Custom scenario** — drag Brent and GSCPI to hypothetical levels (e.g. "what if Brent hits $130") and every forecast recomputes live, client-side, using the same fitted weights

This is the honest version of a stress-testing tool: instead of one confident number, every sector is labeled by how much to trust its forecast, and every forecast is shown against its own calm-conditions baseline rather than in isolation.

## Why "confidence" matters more than the forecast number

Building this surfaced an important lesson, kept visible in the product rather than hidden: a naive correlation test suggested rubber & plastics was the most oil-sensitive Malaysian manufacturing sector — the intuitive result, matching the Farm Fresh PET-bottle shortage case this project started from. After correcting for autocorrelation (HAC standard errors) and multiple-testing bias, that result did not hold up. **Textiles and wearing apparel did** — a less obvious but statistically robust finding. The dashboard reports both: which sectors are "robust" (survive rigorous testing with a plausible mechanism), which are "significant but no clear mechanism" (likely coincidental), and which are "not significant" at all.

## Architecture — kept at $0

```
Free public data (EIA, NY Fed GSCPI, DOSM)
            |
GitHub Actions (monthly cron) -- refits the HAC-corrected regression per sector
            |
Opens a pull request with the updated data/model_output.json
            |
You review and merge
            |
GitHub Pages serves index.html, which fetches model_output.json
            |
Visitor's browser: enters portfolio mix, computes weighted forecast client-side
```

No server, no database, no per-visitor compute cost. Git + a JSON file is the "database." The monthly refresh opens a **pull request** rather than committing directly, so you can sanity-check the numbers (e.g. a sector's confidence flipping from robust to not-significant) before they go live.

## Repo structure

```
/index.html                     — the interactive dashboard
/data/model_output.json         — precomputed weights, lags, confidence per sector
/scripts/fetch_and_fit.py       — fetch + HAC regression pipeline
/scripts/requirements.txt
/.github/workflows/update-model.yml  — monthly cron, opens a PR
```

## Running the pipeline yourself

```bash
pip install -r scripts/requirements.txt --break-system-packages
EIA_API_KEY=your_key_here python scripts/fetch_and_fit.py
```
Free EIA key: https://www.eia.gov/opendata/register.php

## Setting up the automated monthly refresh

1. Push this repo to GitHub (public repo — free Actions minutes).
2. In repo **Settings → Secrets and variables → Actions**, add a secret named `EIA_API_KEY` with your free EIA key.
3. The workflow runs automatically on the 1st of each month, or trigger it manually from the **Actions** tab (`workflow_dispatch`).
4. Each run opens a pull request titled "Monthly model refresh" — review the diff in `data/model_output.json`, then merge.

## Deploying the dashboard (GitHub Pages)

1. **Settings → Pages** → Source: Deploy from branch → `main` → `/ (root)`.
2. Your live URL: `https://<your-username>.github.io/<repo-name>/`.

**Local testing note:** `index.html` fetches `data/model_output.json` via `fetch()`, which browsers block on a plain `file://` path (CORS). Run a tiny local server instead:
```bash
python -m http.server 8000
```
then open `http://localhost:8000` in your browser.

## Methodology summary

- **Drivers:** Brent crude spot price (EIA) and NY Fed's Global Supply Chain Pressure Index, both standardized (z-scores).
- **Outcome:** year-on-year growth for each of Malaysia's 25 manufacturing/utility divisions (DOSM Industrial Production Index, 2-digit MSIC).
- **Period:** monthly, 2015-present, excluding 2020-01 to 2022-06 (COVID-19 demand/supply confound).
- **Per sector:** tests lags of 0-6 months, keeps the best-fitting lag, reports HAC-corrected (Newey-West) standard errors to correct for autocorrelation inherent in year-on-year growth series.
- **Confidence label:** "robust" requires both statistical significance (F-test p<0.05) *and* a plausible direct mechanism (petrochemical feedstock, synthetic fiber input, energy-intensive process) — significance without a mechanism is flagged separately rather than presented with false confidence.
- **Scenario recomputation:** each sector's fitted intercept (`const`) and driver weights (`w_gscpi`, `w_brent`) are stored alongside the standardization stats (mean/std used to convert raw Brent/GSCPI values to z-scores), so the dashboard can recompute `const + w_gscpi × z(GSCPI) + w_brent × z(Brent)` entirely client-side — for today's actual values, or for a hypothetical scenario set via the sliders.

## Honest limitations

- ~90-125 monthly observations per sector is a reasonable starting signal, not enough for formal model validation (e.g. SR 11-7-style review). Treat this as decision support, not a capital or provisioning input.
- Two drivers (oil, macro stress) can't capture every real-world shock — a genuinely comprehensive model would add sector-specific commodity data (palm oil, natural gas, base metals) as free sources are found.
- Correlation, even HAC-corrected, is not proof of causation. The "robust" label means "survived rigorous statistical testing and has a plausible mechanism," not "certain."

## License

MIT — see [LICENSE](./LICENSE).
