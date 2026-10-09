"""Work out the five statewide ratings the website home page uses as they stood on the 1st of each month (Nov 2025 to Oct 2026), with the same functions the site export uses.

    uv run python scripts/state_now_backfill.py ratings.json     then, in the website repository: bun scripts/backtest-state-now.ts ratings.json (writes the summary line for each date)
"""
import json, sys, tempfile, logging
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from nmwater.core.config import Settings
from nmwater.derived import statewide as sw, statewide_flow as swf, statewide_temperature as stt
from nmwater.site.precip import window_stats as precip_stats
from nmwater.site.reservoirs import export_reservoirs
logging.disable(logging.WARNING)

OUT = Path(sys.argv[1])
DATES = [pd.Timestamp(2025, 11, 1) + pd.DateOffset(months=i) for i in range(12)]   # 2025-11-01 .. 2026-10-01
s = Settings.load(); con = duckdb.connect(str(s.duckdb_path), read_only=True); con.execute("SET TimeZone = 'UTC'")
grids = s.data_dir / "grids"
num = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)

# rain: the area-weighted mean of the daily watershed series (as site/precip.py does), 30-day window ending on the date
ws = con.sql("SELECT huc8, area_km2, nm_fraction FROM watersheds WHERE has_precip").df()
codes = list(ws["huc8"])
daily = con.sql("SELECT huc8, date::DATE AS date, precip_in FROM watershed_precip WHERE interval = 'daily' AND date >= DATE '1990-01-01' AND huc8 IN (" + ",".join(f"'{c}'" for c in codes) + ")").df()
daily["date"] = pd.to_datetime(daily["date"])
w_in = ws.assign(w=ws["area_km2"] * ws["nm_fraction"]).set_index("huc8")["w"]
pv = daily.pivot(index="date", columns="huc8", values="precip_in")
cols = [c for c in pv.columns if w_in.get(c, 0) > 0]
wts = pv[cols].notna().mul(w_in[cols], axis=1)
state = (pv[cols].fillna(0).mul(w_in[cols], axis=1)).sum(axis=1) / wts.sum(axis=1)

# reservoirs: every reservoir's daily frame, the storage group, as the statewide file builds it
tmp = Path(tempfile.mkdtemp())
res = export_reservoirs(con, tmp, tmp / "rivers", "backfill", False)
frames = res[1] if res else {}
sup = sorted(k for k, f in frames.items() if sw.GROUP_OF_MEASURE[f["measure"].iloc[-1]] == "supply")

flow_all = swf.read_series(con)
poly = stt.nm_polygon(grids)
tdaily, _, _ = stt.series(grids, poly)
dw = sw.drought_weekly(con)

out = []
for D in DATES:
    row = {"date": D.date().isoformat()}
    r = precip_stats(state[:D].dropna(), 30)
    row["rain"] = None if not r else {"class": r["class"], "percentile": num(r["percentile"]), "percent_of_normal": num(r["percent_of_normal"]), "window_end": str(r["window_end"].date())}
    g = sw.group_series(frames, sup, end=D)
    asof = sw.latest_complete(g)
    if asof is not None:
        cap = float(g.loc[asof, "capacity_af"]); st = float(g.loc[asof, "storage_af"])
        _, pctile, klass, floored, med = sw.storage_normal(g, sw.MATERIAL_POINTS / 100 * cap)
        row["reservoirs"] = {"class": klass, "percentile": num(pctile), "percent": 100 * st / cap if cap else None, "as_of": str(asof.date())}
    ser = {k: v[:D] for k, v in flow_all.items() if len(v[:D])}
    used, stale, end = swf.select(ser)
    if used and end is not None:
        wd = swf.window_stats(ser, used, end, 30)
        t = wd and wd.get("total")
        row["flow"] = None if not t else {"class": t["class"], "percentile": num(t["percentile"]), "percent_of_median": num(t["percent_of_median"]), "window_end": str(end.date())}
    tw = stt.window_stats(tdaily[:D], 30)
    row["temperature"] = None if not tw else {"class": tw["class"], "percentile": num(tw["percentile"]), "anomaly_c": num(tw["anomaly_c"]), "window_end": tw["window_end"]}
    ds = sw.drought_summary(dw[dw.index <= D])
    row["drought"] = None if not ds else {"d2": ds["d2"], "percentile": num(ds["percentile"]), "as_of": ds["as_of"]}
    out.append(row)
    print(D.date(), {k: (v and (v.get("class") or v.get("d2"))) for k, v in row.items() if k != "date"}, flush=True)
OUT.write_text(json.dumps(out, indent=1))
