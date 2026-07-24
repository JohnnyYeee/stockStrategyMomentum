#!/usr/bin/env python3
"""
Mom 12-1 momentum screener -> data.json
每月第10个交易日,从「标普500 + AI名单」按 12-1 动量取前10只等权持有。
本脚本每天收盘后重算「当前」前10名单(live signal),并附带 11..N 观察区。
Runs headless (e.g. in GitHub Actions). No API key needed; uses yfinance.

Universe:
  - S&P 500 现成分 (Wikipedia 自动抓取)  ← 默认只用这个 (SPY 成分)
  - 可选: tickers.txt 的 AI/自选名单, 由 INCLUDE_AI_LIST 开关控制 (默认 False)

Signal:
  动量分 = AdjClose[t-21] / AdjClose[t-252] - 1   (12个月涨幅, 剔除最近1个月)

Tune the strategy with the CONFIG block below.
"""
import json, io, os, time, datetime, warnings
import numpy as np, pandas as pd, requests, yfinance as yf
warnings.filterwarnings("ignore")

# ----------------------- CONFIG (tune here) -----------------------
INCLUDE_AI_LIST = False # True=并入 tickers.txt 的 AI/自选名单; False=只用标普500(SPY)成分
TOP_N        = 10       # 持仓只数 (等权各 100/TOP_N %)
WATCH_EXTRA  = 15       # 额外展示紧邻前10的候选 (11 .. TOP_N+WATCH_EXTRA)
LOOKBACK     = 252      # 动量回看窗口 (约12个月交易日)
SKIP         = 21       # 剔除最近 N 个交易日 (约1个月, 即 12-1 的 "-1")
MIN_PRICE    = 5.0      # 价格过滤: 现价须 > $5
REB_DAY      = 10       # 换仓日 = 每月第 N 个交易日
COST_BPS     = 15       # 单边成本假设 (仅用于文案展示)
DL_PERIOD    = "14mo"   # 下载区间, 须 > LOOKBACK+SKIP 个交易日
SPARK_BARS   = 180      # 每只票输出的价格轨迹点数
# ------------------------------------------------------------------
MIN_BARS = LOOKBACK + 1                       # 需要 t-252 .. t

HERE = os.path.dirname(os.path.abspath(__file__))
EXCH_SUFFIX = (".SS", ".SZ", ".HK", ".T", ".KS", ".KQ", ".L", ".TO", ".V",
               ".HptK", ".SI", ".AX", ".PA", ".DE", ".MI", ".MC", ".AS")

def to_yahoo(t):
    t = t.strip().upper()
    if not t:
        return None
    if any(t.endswith(s.upper()) for s in EXCH_SUFFIX):
        return t                      # keep exchange-suffixed tickers (HK / A-share / etc.)
    if "." in t:
        return t.replace(".", "-")    # US class shares: BRK.B -> BRK-B
    return t

def load_sp500():
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                     headers=hdr, timeout=30)
    return pd.read_html(io.StringIO(r.text))[0]

def load_universe():
    """池子 = 标普500(SPY)现成分。INCLUDE_AI_LIST=True 时再并入 tickers.txt(AI名单, 标 ai=True)。"""
    meta = {}
    # 1) AI / priority list from tickers.txt -> ai=True  (仅当 INCLUDE_AI_LIST=True)
    p = os.path.join(HERE, "tickers.txt")
    if INCLUDE_AI_LIST and os.path.exists(p):
        for tok in open(p).read().replace(",", " ").split():
            u = tok.strip().upper()
            if u and not u.startswith("#"):
                meta[u] = {"name": u, "sector": "AI", "ai": True}
        print(f"AI/自选名单: {len(meta)} tickers")
    # 2) merge the full S&P 500 -> ai=False (unless already an AI name)
    df = load_sp500()
    added = 0
    for _, row in df.iterrows():
        sym = str(row["Symbol"]).upper()
        if sym in meta:                       # already an AI name: keep ai=True, enrich labels
            meta[sym]["name"]   = row["Security"]
            meta[sym]["sector"] = row["GICS Sector"]
        else:
            meta[sym] = {"name": row["Security"], "sector": row["GICS Sector"], "ai": False}
            added += 1
    print(f"merged S&P 500: +{added} (total {len(meta)})")
    return meta

def _fetch_batch(batch, period):
    """Download one chunk and return {ysym: Close-series} for whatever came back with data."""
    d = yf.download(batch, period=period, interval="1d", group_by="ticker",
                    auto_adjust=True, threads=True, progress=False)
    out = {}
    if len(batch) == 1:                       # single ticker -> flat columns, no ticker level
        if "Close" in d and not d["Close"].dropna().empty:
            out[batch[0]] = d["Close"].dropna()
        return out
    for t in batch:
        try:
            sub = d[t]["Close"].dropna()
            if not sub.empty:
                out[t] = sub
        except Exception:
            pass
    return out

def download_all(ysyms, chunk=25, pause=1.5):
    """Batched download to stay under Yahoo's rate limit, with one retry pass for misses."""
    data = {}
    for i in range(0, len(ysyms), chunk):
        data.update(_fetch_batch(ysyms[i:i+chunk], DL_PERIOD))
        print(f"  downloaded {min(i+chunk, len(ysyms))}/{len(ysyms)} (got {len(data)})")
        time.sleep(pause)
    missing = [s for s in ysyms if s not in data]
    if missing:
        print(f"retrying {len(missing)} missing tickers...")
        time.sleep(4)
        for i in range(0, len(missing), 15):
            data.update(_fetch_batch(missing[i:i+15], DL_PERIOD))
            time.sleep(2)
    return data

def rebalance_dates(index):
    """给定交易日 DatetimeIndex, 返回每月第 REB_DAY 个交易日的日期列表。"""
    months = index.to_period("M")
    firsts = np.where(~pd.Series(months).duplicated().values)[0]
    out = []
    for f in firsts:
        p = int(f) + REB_DAY - 1
        if p < len(index):
            out.append(index[p].date())
    return out

def screen():
    meta = load_universe()
    ymap = {o: to_yahoo(o) for o in meta if to_yahoo(o)}
    ysyms = list(dict.fromkeys(ymap.values()))          # de-dupe yahoo symbols
    y2o = {}
    for o, y in ymap.items():
        y2o.setdefault(y, o)                            # first original wins the yahoo symbol

    print(f"downloading {len(ysyms)} tickers (batched, {DL_PERIOD})...")
    raw = download_all(ysyms)
    print(f"got {len(raw)}/{len(ysyms)} tickers with data")

    # aligned adjusted-close frame keyed by original symbol
    closes = {}
    for ysym, s in raw.items():
        orig = y2o.get(ysym)
        if orig is None or len(s) < MIN_BARS:
            continue
        closes[orig] = s
    if not closes:
        raise SystemExit("no ticker returned enough history")

    A = pd.DataFrame(closes).sort_index()
    A = A[A.notna().mean(axis=1) > 0.5]                 # drop sparse/stray trading rows
    n = len(A)
    if n <= LOOKBACK:
        raise SystemExit(f"only {n} rows; need > {LOOKBACK}")
    i = n - 1
    last_date = A.index[-1].date()

    # ---- 12-1 momentum + filters (与回测/信号器一致) ----
    mom   = (A.iloc[i - SKIP] / A.iloc[i - LOOKBACK] - 1)
    pxnow = A.iloc[i]
    valid = mom.notna() & pxnow.notna() & (pxnow > MIN_PRICE)
    mom   = mom[valid]
    ret1m = (A.iloc[i] / A.iloc[i - SKIP]     - 1)
    ret12 = (A.iloc[i] / A.iloc[i - LOOKBACK] - 1)
    ranked = mom.sort_values(ascending=False)
    keep   = list(ranked.index[:TOP_N + WATCH_EXTRA])
    maxmom = float(ranked.iloc[0]) if len(ranked) else 1.0

    # ---- rebalance calendar ----
    rds = rebalance_dates(A.index)
    past   = [d for d in rds if d <= last_date]
    future = [d for d in rds if d >  last_date]
    last_reb = past[-1] if past else None
    is_reb_today = bool(last_reb == last_date)
    if future:
        next_reb = future[0]
    else:                                              # 估算下月第 REB_DAY 个交易日
        fn = pd.Timestamp(last_date) + pd.offsets.MonthBegin(1)
        bd = pd.bdate_range(fn, fn + pd.Timedelta(days=25))
        next_reb = bd[REB_DAY - 1].date()

    results = []
    for rank, sym in enumerate(keep, 1):
        s = closes[sym]
        spark = [round(float(x), 2) for x in s.iloc[-SPARK_BARS:].tolist()]
        results.append(dict(
            symbol=sym, name=meta[sym]["name"], sector=meta[sym]["sector"],
            ai=bool(meta[sym].get("ai", False)),
            rank=rank, held=bool(rank <= TOP_N),
            weight=round(100.0 / TOP_N, 1) if rank <= TOP_N else 0.0,
            mom=round(float(mom[sym]) * 100, 1),           # 12-1 动量分 %
            ret_1m=round(float(ret1m[sym]) * 100, 1),      # 被剔除的最近1月 %
            ret_12m=round(float(ret12[sym]) * 100, 1),     # 满12月涨幅 %
            price=round(float(pxnow[sym]), 2),
            mom_pct=round(max(float(mom[sym]) / maxmom, 0.0) * 100, 1) if maxmom > 0 else 0.0,
            skip_bars=SKIP,
            spark=spark,
        ))

    out = dict(
        scanned=int(valid.sum()), passed=TOP_N, top_n=TOP_N, universe=len(ysyms),
        date=str(last_date),
        generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        reb_day=REB_DAY, last_reb=str(last_reb) if last_reb else "",
        is_reb_today=is_reb_today, next_reb=str(next_reb), cost_bps=COST_BPS,
        params=dict(lookback=LOOKBACK, skip=SKIP, top_n=TOP_N, min_price=MIN_PRICE,
                    reb_day=REB_DAY, watch_extra=WATCH_EXTRA, cost_bps=COST_BPS),
        results=results,
    )

    # latest snapshot
    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(out, f)

    # dated archive: data/<date>.json + data/index.json
    ddir = os.path.join(HERE, "data"); os.makedirs(ddir, exist_ok=True)
    if out["date"]:
        with open(os.path.join(ddir, f"{out['date']}.json"), "w") as f:
            json.dump(out, f)
    dates = sorted(fn[:-5] for fn in os.listdir(ddir) if fn.endswith(".json") and fn != "index.json")
    with open(os.path.join(ddir, "index.json"), "w") as f:
        json.dump({"latest": out["date"], "dates": dates}, f)

    write_summary(out)
    print(f"eligible {out['scanned']}/{out['universe']}, held {TOP_N} -> "
          f"data.json + summary.txt + data/{out['date']}.json")


def write_summary(out):
    p = out["params"]
    lines = []
    lines.append("Mom 12-1 动量策略 · 前10 等权 · 月度换仓")
    lines.append(f"数据日 {out['date']}   生成 {out['generated_at']}")
    reb = "今日为换仓日" if out["is_reb_today"] else f"下次换仓 ~{out['next_reb']}"
    lines.append(f"合格 {out['scanned']}/{out['universe']} 只 · 持仓 {out['top_n']} 只等权各 "
                 f"{100/out['top_n']:.0f}% · {reb} (每月第{p['reb_day']}个交易日)")
    lines.append(f"信号: 动量分 = 复权价[t-{p['skip']}]/复权价[t-{p['lookback']}]-1 · "
                 f"过滤 价格>${p['min_price']:.0f} · 成本假设 单边{p['cost_bps']:.0f}bps")
    lines.append("")
    lines.append(f"{'#':>2} {'★':<1} {'持':<1} {'代码':<8} {'12-1动量':>9} {'近1月':>7} "
                 f"{'12月':>7} {'现价':>10}  {'板块':<22} 名称")
    lines.append("-" * 108)
    for r in out["results"]:
        star = "★" if r.get("ai") else " "
        hold = "●" if r["held"] else " "
        lines.append(f"{r['rank']:>2} {star:<1} {hold:<1} {r['symbol']:<8} "
                     f"{r['mom']:>+8.1f}% {r['ret_1m']:>+6.1f}% {r['ret_12m']:>+6.1f}% "
                     f"{r['price']:>10,.2f}  {r['sector'][:22]:<22} {r['name']}")
    lines.append("")
    lines.append("● = 本期持仓(前10)  ★ = AI增量名单(tickers.txt, 非标普成分)")
    lines.append("注: 研究用途,非投资建议。结构化数据见 data.json;历史见 data/<日期>.json。")
    with open(os.path.join(HERE, "summary.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")

if __name__ == "__main__":
    screen()
