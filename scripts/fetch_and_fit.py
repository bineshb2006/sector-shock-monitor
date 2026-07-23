"""
scripts/fetch_and_fit.py

The full pipeline, consolidated: fetches GSCPI, Brent crude, and Malaysia's
DOSM industrial production by division, refits a HAC-corrected regression
per sector (excluding the COVID window), and writes data/model_output.json.

This is what the monthly GitHub Actions workflow runs. It can also be run
locally / in Jupyter the same way as the earlier exploration scripts.

Requires an EIA API key, passed as an environment variable:
    EIA_API_KEY=your_key_here python scripts/fetch_and_fit.py

Free signup: https://www.eia.gov/opendata/register.php
"""

import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import statsmodels.api as sm


DIVISION_NAMES = {
    "10": "Food products", "11": "Beverages", "12": "Tobacco products",
    "13": "Textiles", "14": "Wearing apparel", "15": "Leather & related products",
    "16": "Wood & wood products", "17": "Paper & paper products",
    "18": "Printing & recorded media", "19": "Coke & refined petroleum products",
    "20": "Chemicals & chemical products", "21": "Pharmaceuticals & botanical products",
    "22": "Rubber & plastics products", "23": "Non-metallic mineral products",
    "24": "Basic metals", "25": "Fabricated metal products",
    "26": "Computer, electronic & optical products", "27": "Electrical equipment",
    "28": "Machinery & equipment", "29": "Motor vehicles & trailers",
    "30": "Other transport equipment", "31": "Furniture", "32": "Other manufacturing",
    "33": "Repair & installation of machinery",
    "35": "Electricity, gas, steam & air conditioning",
}

# Sectors where oil/macro stress has a plausible direct mechanism (petrochemical
# feedstock, synthetic fiber, energy-intensive input) — based on the analysis
# already run and validated in Jupyter. Sectors outside this set that still
# test statistically significant are flagged as likely coincidental rather
# than causal, since a p-value alone can't tell the two apart.
PLAUSIBLE_MECHANISM = {"13", "14", "19", "20", "31", "11", "25"}

EXCLUDE_START, EXCLUDE_END = "2020-01", "2022-06"
MAX_LAG = 6
MIN_MONTHS = 24


def fetch_gscpi() -> pd.DataFrame:
    url = "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open("gscpi_data.xlsx", "wb") as f:
        f.write(resp.content)
    df = pd.read_excel("gscpi_data.xlsx", sheet_name="GSCPI Monthly Data", header=0)
    df = df.dropna(subset=["Date"])
    df["Date"] = pd.to_datetime(df["Date"], format="%d-%b-%Y")
    df["GSCPI"] = df["GSCPI"].astype(float)
    df["month"] = df["Date"].values.astype("datetime64[M]")
    return df[["month", "GSCPI"]]


def fetch_full_brent(api_key: str) -> pd.DataFrame:
    url = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
    all_rows, offset, length = [], 0, 5000
    while True:
        params = {
            "api_key": api_key, "frequency": "daily", "data[0]": "value",
            "facets[series][]": "RBRTE", "sort[0][column]": "period",
            "sort[0][direction]": "asc", "offset": offset, "length": length,
        }
        resp = None
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=60)
                resp.raise_for_status()
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                print(f"  EIA request attempt {attempt + 1} failed ({e}), retrying...")
                if attempt == 2:
                    raise
        js = resp.json().get("response", {})
        rows = js.get("data", [])
        all_rows.extend(rows)
        total = int(js.get("total", len(all_rows)))
        offset += length
        if offset >= total or not rows:
            break
    df = pd.DataFrame(all_rows)
    df["period"] = pd.to_datetime(df["period"])
    df["value"] = df["value"].astype(float)
    df["month"] = df["period"].values.astype("datetime64[M]")
    return df.groupby("month")["value"].mean().reset_index().rename(columns={"value": "Brent"})


def fetch_ipi_divisions() -> pd.DataFrame:
    url = "https://storage.dosm.gov.my/ipi/ipi_2d.parquet"
    df = pd.read_parquet(url)
    df = df[df["series"] == "growth_yoy"].copy()
    df["month"] = pd.to_datetime(df["date"]).values.astype("datetime64[M]")
    return df[["month", "division", "index"]].rename(columns={"index": "growth_yoy"})


def fit_all_sectors(drivers: pd.DataFrame, ipi: pd.DataFrame,
                     latest_gscpi_z: float, latest_brent_z: float) -> list:
    results = []
    for div in sorted(ipi["division"].unique()):
        if div not in DIVISION_NAMES:
            continue
        sector = ipi[ipi["division"] == div][["month", "growth_yoy"]]

        best = None
        for lag in range(MAX_LAG + 1):
            shifted = drivers.copy()
            shifted["month"] = shifted["month"] + pd.DateOffset(months=lag)
            merged = pd.merge(sector, shifted, on="month", how="inner")
            if len(merged) < MIN_MONTHS:
                continue
            X = sm.add_constant(merged[["GSCPI_z", "Brent_z"]])
            y = merged["growth_yoy"]
            model = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 6})
            if best is None or model.rsquared > best["r2"]:
                pred = (model.params["const"]
                        + model.params["GSCPI_z"] * latest_gscpi_z
                        + model.params["Brent_z"] * latest_brent_z)
                best = {
                    "division": div,
                    "sector": DIVISION_NAMES[div],
                    "best_lag_months": lag,
                    "r2": round(float(model.rsquared), 3),
                    "f_pvalue": round(float(model.f_pvalue), 4),
                    "const": round(float(model.params["const"]), 3),
                    "w_gscpi": round(float(model.params["GSCPI_z"]), 3),
                    "w_brent": round(float(model.params["Brent_z"]), 3),
                    "gscpi_pvalue": round(float(model.pvalues["GSCPI_z"]), 4),
                    "brent_pvalue": round(float(model.pvalues["Brent_z"]), 4),
                    "n_months": int(len(merged)),
                    "predicted_growth_pct": round(float(pred), 2),
                }
        if best is None:
            continue

        if best["f_pvalue"] < 0.05 and div in PLAUSIBLE_MECHANISM:
            confidence = "robust"
        elif best["f_pvalue"] < 0.05:
            confidence = "significant_no_clear_mechanism"
        else:
            confidence = "not_significant"
        best["confidence"] = confidence
        results.append(best)

    return sorted(results, key=lambda r: r["r2"], reverse=True)


def main():
    api_key = os.environ.get("EIA_API_KEY", "")
    if not api_key:
        print("ERROR: set EIA_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    print("Fetching GSCPI...")
    gscpi = fetch_gscpi()
    print("Fetching full Brent history from EIA...")
    brent = fetch_full_brent(api_key)
    print("Fetching DOSM IPI by division...")
    ipi = fetch_ipi_divisions()

    gscpi_c = gscpi[~gscpi["month"].between(EXCLUDE_START, EXCLUDE_END)]
    brent_c = brent[~brent["month"].between(EXCLUDE_START, EXCLUDE_END)]
    ipi_c = ipi[~ipi["month"].between(EXCLUDE_START, EXCLUDE_END)]

    drivers = pd.merge(gscpi_c, brent_c, on="month", how="inner")
    g_mean, g_std = drivers["GSCPI"].mean(), drivers["GSCPI"].std()
    b_mean, b_std = drivers["Brent"].mean(), drivers["Brent"].std()
    drivers["GSCPI_z"] = (drivers["GSCPI"] - g_mean) / g_std
    drivers["Brent_z"] = (drivers["Brent"] - b_mean) / b_std

    latest_gscpi_z = (gscpi["GSCPI"].iloc[-1] - g_mean) / g_std
    latest_brent_z = (brent["Brent"].iloc[-1] - b_mean) / b_std

    print(f"Fitting regressions across {len(ipi_c['division'].unique())} sectors...")
    sectors = fit_all_sectors(drivers, ipi_c, latest_gscpi_z, latest_brent_z)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "excluded_period": {"start": EXCLUDE_START, "end": EXCLUDE_END, "reason": "COVID-19 confound"},
        "latest_gscpi": round(float(gscpi["GSCPI"].iloc[-1]), 3),
        "latest_gscpi_month": str(gscpi["month"].iloc[-1].date()),
        "latest_brent": round(float(brent["Brent"].iloc[-1]), 2),
        "latest_brent_month": str(brent["month"].iloc[-1].date()),
        "standardization": {
            "gscpi_mean": round(float(g_mean), 4), "gscpi_std": round(float(g_std), 4),
            "brent_mean": round(float(b_mean), 4), "brent_std": round(float(b_std), 4),
        },
        "sectors": sectors,
    }

    os.makedirs("data", exist_ok=True)
    with open("data/model_output.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote data/model_output.json with {len(sectors)} sectors.")
    robust = [s["sector"] for s in sectors if s["confidence"] == "robust"]
    print(f"Robust sectors: {robust}")


if __name__ == "__main__":
    main()
