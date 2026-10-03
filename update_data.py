#!/usr/bin/env python3
"""Pull the latest public data and rewrite the data payloads inside index.html.

Run:  python update_data.py            (add --dry-run to print without writing)
Needs: python 3.9+, openpyxl (pip install openpyxl). No API keys.

What it refreshes (see README.md "Updating the data" for the full procedure):
  payload.gammaQ / gamma   California quarterly GDP / US quarterly GDP        (FRED: CANQGSP, GDP)
  payload.usGDP            US GDP, 2022 onward (the whole recent series is revised)   (FRED: GDPA, GDP)
  countyPayload            2026 county population (CA DOF E-1), GDP (wage-tilted allocation), GDP per capita
  payload.bayPop/popShare  final-year ten-county population and share
  footer "Data to <date>" and the LAST UPDATED line in the top comment

It does NOT rewrite prose/aria-labels/stat tiles; it prints a checklist of those at the end.
Counties' BEA GDP (the measured 2001-2024 segment, beta24, gdp2024USD) only changes when BEA
publishes a new county year (about December): that is a manual step, see the top comment of index.html.
"""
import csv, io, json, math, re, sys, urllib.request, datetime
from concurrent.futures import ThreadPoolExecutor

HTML = "index.html"
DRY = "--dry-run" in sys.argv
REFIT = "--refit" in sys.argv          # re-estimate the wage->GDP pass-through (slow: ~100 requests)
BETA_PASS = 0.762                      # fitted on 2019-24 county GDP-share vs wage-share changes (r = 0.88)
BASE_YEAR = 2024                       # last year of measured BEA county GDP
GROWTH = 0.015                         # page's default Bay-share drift (slider sB), used to size the county total
REVISED_FROM = 2022                    # US GDP years re-pulled each update

FIPS = {"Alameda": "06001", "Contra Costa": "06013", "Marin": "06041", "Napa": "06055",
        "San Francisco": "06075", "San Mateo": "06081", "Santa Clara": "06085",
        "Santa Cruz": "06087", "Solano": "06095", "Sonoma": "06097"}


def get(url, tries=4):
    """Download via curl (FRED stalls Python's urllib); returns bytes, raises on failure/404."""
    import subprocess
    last = None
    for _ in range(tries):
        # FRED hangs if a browser User-Agent is sent; BLS/DOF want one.
        ua = [] if "fred.stlouisfed.org" in url else ["-A", "Mozilla/5.0"]
        r = subprocess.run(["curl", "-sSL", "-m", "40", *ua, "-w", "%{http_code}", url], capture_output=True)
        code, body = r.stdout[-3:], r.stdout[:-3]
        if r.returncode == 0 and code == b"200" and body:
            return body
        last = f"http {code.decode(errors='ignore')} rc={r.returncode}"
        if code == b"404":
            break
    raise RuntimeError(f"failed: {url}: {last}")


def fred(series, start="2019-01-01"):
    t = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}").decode()
    rows = list(csv.reader(io.StringIO(t)))[1:]
    return [(d, float(v)) for d, v in rows if v not in ("", ".")]


def qlabel(d):  # '2026-04-01' -> '2026Q2'
    return f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}"


# ── 1. national / state GDP ────────────────────────────────────────────────
ca = {qlabel(d): v for d, v in fred("CANQGSP", "2024-01-01")}
us = {qlabel(d): v for d, v in fred("GDP", "2024-01-01")}
quarters = sorted(q for q in ca if q in us and q >= "2025Q1")
gammaQ = {q: round(ca[q] / (us[q] * 1000), 7) for q in quarters}
years = sorted({int(q[:4]) for q in quarters})
last_year = years[-1]
latest_q = quarters[-1]
gamma = {}
for y in years:
    qs = [q for q in quarters if q.startswith(str(y))]
    gamma[str(y)] = round(sum(gammaQ[q] for q in qs) / 4, 7) if len(qs) == 4 else gammaQ[qs[-1]]

usGDP_new = {}
for y in range(REVISED_FROM, last_year + 1):
    qs = [v for q, v in us.items() if q.startswith(str(y))]
    if y < 2024:  # 2022-23 not in the 2024+ pull above
        qs = [v for d, v in fred("GDP", f"{y}-01-01") if d.startswith(str(y))]
    usGDP_new[y] = round(sum(qs) / len(qs) * 1000, 0)   # partial years use the mean of the quarters so far

# ── 2. county inputs ───────────────────────────────────────────────────────
def qcew(a):
    y, q, f = a
    try:
        t = get(f"https://data.bls.gov/cew/data/api/{y}/{q}/area/{f}.csv").decode()
    except RuntimeError:
        return None
    for r in csv.DictReader(io.StringIO(t)):
        if r["own_code"] == "0" and r["industry_code"] == "10":
            return float(r["total_qtrly_wages"])
    return None


def latest_qcew_quarter():
    today = datetime.date.today()
    y, q = today.year, 4
    while y >= today.year - 1:
        if all(qcew((y, q, FIPS["Napa"])) is not None for _ in [0]):
            return y, q
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    raise RuntimeError("no QCEW quarter found")


ly, lq = latest_qcew_quarter()
trail = []
y, q = ly, lq
for _ in range(4):
    trail.append((y, q))
    q -= 1
    if q == 0:
        y, q = y - 1, 4
with ThreadPoolExecutor(6) as ex:
    futs = {(n, y, q): ex.submit(qcew, (y, q, f)) for n, f in FIPS.items()
            for (y, q) in trail + [(BASE_YEAR, k) for k in (1, 2, 3, 4)]}
    W = {k: v.result() for k, v in futs.items()}
assert all(v is not None for v in W.values()), "missing QCEW data"
wt = {n: sum(W[(n, y, q)] for (y, q) in trail) for n in FIPS}
w0 = {n: sum(W[(n, BASE_YEAR, k)] for k in (1, 2, 3, 4)) for n in FIPS}

beta_pass = BETA_PASS
if REFIT:
    def gdp(a):
        n, y = a
        return float(fred(f"GDPALL{FIPS[n]}", f"{y}-01-01")[0][1]) * 1000
    with ThreadPoolExecutor(4) as ex:
        G = {(n, y): ex.submit(gdp, (n, y)) for n in FIPS for y in (2019, BASE_YEAR)}
        W19 = {(n, k): ex.submit(qcew, (2019, k, FIPS[n])) for n in FIPS for k in (1, 2, 3, 4)}
        G = {k: v.result() for k, v in G.items()}
        W19 = {k: v.result() for k, v in W19.items()}
    N = list(FIPS)
    def sh(d):
        t = sum(d.values()); return {n: d[n] / t for n in N}
    g0, g1 = sh({n: G[(n, 2019)] for n in N}), sh({n: G[(n, BASE_YEAR)] for n in N})
    w19 = sh({n: sum(W19[(n, k)] for k in (1, 2, 3, 4)) for n in N}); w24 = sh(w0)
    x = [math.log(w24[n] / w19[n]) for n in N]; yv = [math.log(g1[n] / g0[n]) for n in N]
    mx, my = sum(x) / 10, sum(yv) / 10
    beta_pass = sum((a - mx) * (b - my) for a, b in zip(x, yv)) / sum((a - mx) ** 2 for a in x)
    print(f"refit pass-through = {beta_pass:.3f} (stored constant {BETA_PASS})")

# California DOF E-1 (published each May): Jan-1 prior vs Jan-1 latest, by county
def dof():
    import openpyxl
    for yr in (datetime.date.today().year, datetime.date.today().year - 1):
        try:
            raw = get(f"https://dof.ca.gov/media/docs/forecasting/Demographics/estimates-e1/E-1_{yr}_InternetVersion.xlsx")
        except RuntimeError:
            continue
        ws = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)[f"E-1 CountyState{yr}"]
        return yr, {str(r[0]): (r[1], r[2]) for r in ws.iter_rows(values_only=True)
                    if r[0] in FIPS and isinstance(r[1], (int, float))}
    raise RuntimeError("DOF E-1 workbook not found")

dof_year, dofpop = dof()

# ── 3. rewrite index.html ──────────────────────────────────────────────────
s = open(HTML, encoding="utf8").read()
mp = re.search(r'(<script id="payload" type="application/json">)(.*?)(</script>)', s, re.S)
mc = re.search(r'(<script id="countyPayload" type="application/json">)(.*?)(</script>)', s, re.S)
D = json.loads(mp.group(2)); C = json.loads(mc.group(2))
if last_year != D["years"][-1]:
    sys.exit(f"New calendar year {last_year} in the data but the page ends at {D['years'][-1]}: "
             "extend the page (Y1, years, arrays, model loop) by hand first.")

D["gammaQ"] = gammaQ; D["gamma"] = gamma
i0 = D["years"].index(REVISED_FROM)
D["usGDP"][i0:i0 + len(usGDP_new)] = [usGDP_new[y] for y in sorted(usGDP_new)]

share = D["beta24"] * math.exp(GROWTH * (last_year - BASE_YEAR)) * gamma[str(last_year)]
total = share * D["usGDP"][-1] * 1e6                       # $, nowcast ten-county GDP
t0 = sum(c["gdp2024USD"] for c in C)
w = {c["name"]: (c["gdp2024USD"] / t0) * (wt[c["name"]] / w0[c["name"]]) ** beta_pass for c in C}
z = sum(w.values())
pop25 = sum(c["population2025"] for c in C)
for c in C:
    j, k = dofpop[c["name"]]
    c["population2026"] = round(c["population2025"] * k / j)
    c["gdp2026USD"] = round(total * w[c["name"]] / z)
    c["perCapita2026USD"] = round(c["gdp2026USD"] / c["population2026"])
pop26 = sum(c["population2026"] for c in C)
us_pop = D["bayPop"][-1] / D["popShare"][-1]
D["bayPop"][-1] = round(D["bayPop"][-2] * pop26 / pop25)
D["popShare"][-1] = round(D["bayPop"][-1] / us_pop, 8)

s = s[:mp.start(2)] + json.dumps(D, separators=(",", ":")) + s[mp.end(2):]
mc = re.search(r'(<script id="countyPayload" type="application/json">)(.*?)(</script>)', s, re.S)
s = s[:mc.start(2)] + json.dumps(C, separators=(",", ":")) + s[mc.end(2):]
today = datetime.date.today()
s = re.sub(r"Data to \d+ \w+ \d{4}\.", f"Data to {today.day} {today.strftime('%B %Y')}.", s, count=1)
s = re.sub(r"LAST UPDATED: \d{4}-\d{2}-\d{2}\.", f"LAST UPDATED: {today.isoformat()}.", s, count=1)

print(f"latest GDP quarter: {latest_q}   QCEW trailing 4Q ends {ly}Q{lq}   DOF E-1 {dof_year}")
print(f"gammaQ: {gammaQ}\ngamma: {gamma}")
print(f"nowcast share {share*100:.3f}%  total ${total/1e9:,.1f}bn  ten-county pop {pop26:,}  bayPop {D['bayPop'][-1]:,}")
for c in C:
    print(f"  {c['name']:14s} pop {c['population2026']:>9,}  GDP ${c['gdp2026USD']/1e9:7.1f}bn  per cap ${c['perCapita2026USD']:,}")
if not DRY:
    open(HTML, "w", encoding="utf8").write(s)
    print("index.html written.")
print("""
MANUAL / CLAUDE CHECKLIST (prose that quotes the numbers):
  - Methodology: the gamma sentence (quarter list 'for the six quarters from ... to ...', CA vs US annual growth)
  - Stat tiles / chart end labels / aria-labels: nowcast %, 'income vs US' multiple, population share, population level,
    'added N residents since 2000', 'recovered ...' sentence (check against the rendered page)
  - Sources block: 'to 20XX Qn' for FRED series; QCEW quarter; DOF E-1 year
  - Label the latest period by quarter (e.g. '2026 Q2'); population is '1 Jul <year> (end of Q)'
  - Open the page: no console errors, county sum == headline GDP
""")
