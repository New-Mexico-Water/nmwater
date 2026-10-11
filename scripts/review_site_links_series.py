# Compares the daily series of two linked sites (issue #68): are they the same record? Usage: uv run python scripts/review_site_links_series.py OUT.csv (run capped, e.g. systemd-run --user -p MemoryMax=9G).
# The day is the UTC date, so two providers that cut their daily mean at local midnight agree closely (r near 1, ratio near 1) but not exactly.
import duckdb, sys
OUT = sys.argv[1]
con = duckdb.connect("data/duckdb/nmwater.duckdb", read_only=True)
con.execute("SET memory_limit='6GB'; SET threads=3; SET TimeZone='UTC'")
con.execute("""create temp table pv as
 select p.ua, p.ub, p.d, p.sa, p.sb, p.na, p.nb, x.variable from (
   select l.site_uid_a ua, l.site_uid_b ub, l.distance_m d, a.source sa, b.source sb, a.name na, b.name nb
   from site_links l join sites a on a.site_uid=l.site_uid_a join sites b on b.site_uid=l.site_uid_b where l.link_type='colocated'
   and a.site_type in ('stream','reservoir','lake','canal','diversion') and not ((a.source='usgs' and b.source='wqp') or (a.source='wqp' and b.source='usgs'))) p
 join site_variables x on x.site_uid=p.ua join site_variables y on y.site_uid=p.ub and y.variable=x.variable
 where x.variable in ('discharge','stage','reservoir_storage','reservoir_elevation','reservoir_release','reservoir_inflow','precip') group by all""")
print("pair-variables", con.execute("select count(*) from pv").fetchone(), flush=True)
con.execute("create temp table sv as select distinct ua site_uid, variable from pv union select distinct ub, variable from pv")
con.execute("create temp table srcs as select distinct source from sites where site_uid in (select site_uid from sv)")
# per site/variable: daily series; use stored daily values if the site has them, else the mean of its finer readings
con.execute("""create temp table daily as
 with o as (select o.site_uid, o.variable, cast(o.datetime_utc as date) dt, o.interval, o.value
            from observations o semi join sv using (site_uid, variable)
            where o.source in (select source from srcs) and o.variable in (select variable from sv)
              and o.interval in ('daily','instant','1min','5min','15min','30min','hourly') and o.value is not null),
 has as (select site_uid, variable, bool_or(interval='daily') has_daily from o group by 1,2)
 select o.site_uid, o.variable, o.dt, avg(o.value) v from o join has h using (site_uid, variable)
 where (h.has_daily and o.interval='daily') or (not h.has_daily) group by 1,2,3""")
print("daily rows", con.execute("select count(*) from daily").fetchone(), flush=True)
con.execute(f"""copy (
 select pv.ua, pv.ub, pv.sa, pv.sb, pv.na, pv.nb, round(pv.d,1) metres, pv.variable,
   count(*) overlap_days, min(a.dt) first_day, max(a.dt) last_day,
   round(avg((abs(a.v-b.v) <= greatest(0.005*abs(a.v), 0.01))::int),3) share_equal,
   round(corr(a.v,b.v),4) r, round(median(b.v/nullif(a.v,0)),3) median_ratio
 from pv join daily a on a.site_uid=pv.ua and a.variable=pv.variable join daily b on b.site_uid=pv.ub and b.variable=pv.variable and b.dt=a.dt
 group by pv.ua,pv.ub,pv.sa,pv.sb,pv.na,pv.nb,pv.d,pv.variable
) to '{OUT}' (header, delimiter ',')""")
print("done", flush=True)
