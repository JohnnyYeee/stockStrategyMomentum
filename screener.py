#!/usr/bin/env python3
"""
Right-side shallow pullback screener -> data.json
Runs headless (e.g. in GitHub Actions). No API key needed; uses yfinance.

Universe:
  - If tickers.txt exists next to this script, use it (comma / space / newline separated).
  - Otherwise default to the full S&P 500.

Tune the strategy with the THRESHOLDS block below.
"""
import json, io, os, sys, time, datetime, warnings
import numpy as np, pandas as pd, requests, yfinance as yf
warnings.filterwarnings("ignore")

# ----------------------- THRESHOLDS (tune here) -----------------------
DEPTH_MIN      = 0.03   # pullback must be at least this deep (3%)
DEPTH_MAX      = 0.20   # ...and at most this deep ("shallow" ceiling). 0.18 = looser, 0.08 = tighter
FROM_52W_MAX   = 0.20   # must be within 20% of its 52-week high (strength)
RSI_LOW        = 38     # confirmation RSI band
RSI_HIGH       = 68
ADV_MIN        = 1e6    # min 20-day average dollar volume ($)
EXT_EMA10_MAX  = 0.07   # entry must not be >7% extended above the 10-EMA
MIN_BARS       = 210    # need ~1y history for the 200-MA
# --- universe & ranking ---
INCLUDE_SP500  = False  # merge the full S&P 500 into the universe (bigger candidate pool)
AI_BONUS       = 15.0   # score bonus for AI/custom names (from tickers.txt) so they rank higher
# ----------------------------------------------------------------------

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
    meta = {}
    # 1) AI / priority list from tickers.txt -> ai=True (gets AI_BONUS at scoring time)
    p = os.path.join(HERE, "tickers.txt")
    if os.path.exists(p):
        toks = [x for x in open(p).read().replace(",", " ").split()]
        for t in toks:
            u = t.strip().upper()
            if u:
                meta[u] = {"name": u, "sector": "AI", "ai": True}
        print(f"AI/custom list: {len(meta)} tickers")
    # 2) optionally merge the full S&P 500 -> ai=False (unless already in the AI list)
    if INCLUDE_SP500 or not meta:
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

def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))

def _fetch_batch(batch):
    """Download one chunk and return {ysym: DataFrame} for whatever came back with data."""
    d = yf.download(batch, period="1y", interval="1d", group_by="ticker",
                    auto_adjust=True, threads=True, progress=False)
    out = {}
    if len(batch) == 1:                       # single ticker -> flat columns, no ticker level
        if not d.dropna(how="all").empty:
            out[batch[0]] = d
        return out
    for t in batch:
        try:
            sub = d[t]
            if not sub.dropna(how="all").empty:
                out[t] = sub
        except Exception:
            pass
    return out

def download_all(ysyms, chunk=25, pause=1.5):
    """Batched download to stay under Yahoo's rate limit, with one retry pass for misses."""
    data = {}
    for i in range(0, len(ysyms), chunk):
        data.update(_fetch_batch(ysyms[i:i+chunk]))
        print(f"  downloaded {min(i+chunk, len(ysyms))}/{len(ysyms)} (got {len(data)})")
        time.sleep(pause)
    missing = [s for s in ysyms if s not in data]
    if missing:
        print(f"retrying {len(missing)} missing tickers...")
        time.sleep(4)
        for i in range(0, len(missing), 15):
            data.update(_fetch_batch(missing[i:i+15]))
            time.sleep(2)
    return data

def screen():
    meta = load_universe()
    orig = list(meta.keys())
    ymap = {o: to_yahoo(o) for o in orig if to_yahoo(o)}
    ysyms = list(ymap.values())

    print(f"downloading {len(ysyms)} tickers (batched)...")
    data = download_all(ysyms)
    print(f"got {len(data)}/{len(ysyms)} tickers with data")

    results, scanned, last_date = [], 0, None
    for orig_sym, ysym in ymap.items():
        df = data.get(ysym)
        if df is None:
            continue
        df = df.dropna()
        if len(df) < MIN_BARS:
            continue
        scanned += 1
        last_date = df.index[-1]
        c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
        sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
        ema10, ema20 = c.ewm(span=10, adjust=False).mean(), c.ewm(span=20, adjust=False).mean()
        r = rsi(c); advd = (c*v).rolling(20).mean()
        px = c.iloc[-1]

        if not (px > sma50.iloc[-1] > sma200.iloc[-1]):           continue
        if not (sma50.iloc[-1] > sma50.iloc[-21]):                continue
        if advd.iloc[-1] < ADV_MIN:                               continue

        recent_high = h.iloc[-30:].max(); hi_idx = h.iloc[-30:].idxmax()
        hi_pos = df.index.get_loc(hi_idx); low_since = l.iloc[hi_pos:].min()
        depth = (recent_high - low_since)/recent_high
        cur_from_high = (recent_high - px)/recent_high
        bars_since_high = len(df)-1-hi_pos
        if not (DEPTH_MIN <= depth <= DEPTH_MAX):                 continue
        if bars_since_high < 2:                                   continue

        high_52 = h.iloc[-252:].max() if len(df) >= 252 else h.max()
        from_52 = (high_52 - px)/high_52
        if from_52 > FROM_52W_MAX:                                continue
        if not (low_since > sma50.iloc[-1]*0.985):                continue

        up_day = c.iloc[-1] > c.iloc[-2]
        rsi_turn = r.iloc[-1] > r.iloc[-2]
        confirm = (px > ema10.iloc[-1]) and (up_day or rsi_turn) \
            and (RSI_LOW <= r.iloc[-1] <= RSI_HIGH) and ((px-low_since)/low_since > 0.005)
        if not confirm:                                           continue
        ext = (px - ema10.iloc[-1])/ema10.iloc[-1]
        if ext > EXT_EMA10_MAX:                                   continue

        gap = (sma50.iloc[-1]-sma200.iloc[-1])/sma200.iloc[-1]
        slope = (sma50.iloc[-1]-sma50.iloc[-21])/sma50.iloc[-21]
        trend_score = np.clip(gap*180, 0, 25) + np.clip(slope*220, 0, 15)
        rs_score = np.clip((1-from_52)*30, 0, 30)
        shallow_score = np.clip((1-abs(depth-0.075)/0.075)*15, 0, 15)
        conf = (6 if up_day else 0) + (3 if rsi_turn else 0) + np.clip((1-abs(ext)/0.07)*6, 0, 6)
        is_ai = bool(meta[orig_sym].get("ai", False))
        score = round(float(trend_score+rs_score+shallow_score+conf + (AI_BONUS if is_ai else 0)), 1)

        results.append(dict(
            symbol=orig_sym, name=meta[orig_sym]["name"], sector=meta[orig_sym]["sector"], ai=is_ai,
            price=round(float(px), 2), from_52w=round(float(from_52)*100, 1),
            depth=round(float(depth)*100, 1), from_high=round(float(cur_from_high)*100, 1),
            rsi=round(float(r.iloc[-1]), 1), ext_ema10=round(float(ext)*100, 1),
            gap_50_200=round(float(gap)*100, 1), bars_since_high=int(bars_since_high),
            adv_m=round(float(advd.iloc[-1])/1e6, 0), score=score, up_day=bool(up_day),
            spark=[round(float(x), 2) for x in c.iloc[-70:].tolist()],
        ))

    results.sort(key=lambda x: -x["score"])
    out = dict(
        scanned=scanned, passed=len(results),
        date=str(last_date.date()) if last_date is not None else "",
        generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        params=dict(depth_min=DEPTH_MIN, depth_max=DEPTH_MAX, from_52w_max=FROM_52W_MAX,
                    rsi=[RSI_LOW, RSI_HIGH], adv_min=ADV_MIN,
                    include_sp500=INCLUDE_SP500, ai_bonus=AI_BONUS),
        results=results,
    )
    # latest snapshot
    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(out, f)

    # dated archive: data/<date>.json  +  data/index.json (list of available dates for AI discovery)
    ddir = os.path.join(HERE, "data"); os.makedirs(ddir, exist_ok=True)
    if out["date"]:
        with open(os.path.join(ddir, f"{out['date']}.json"), "w") as f:
            json.dump(out, f)
    dates = sorted(fn[:-5] for fn in os.listdir(ddir) if fn.endswith(".json") and fn != "index.json")
    with open(os.path.join(ddir, "index.json"), "w") as f:
        json.dump({"latest": out["date"], "dates": dates}, f)

    # plain-text summary for easy AI / human reading
    write_summary(out)
    print(f"scanned {scanned}, passed {len(results)} -> data.json + summary.txt + data/{out['date']}.json")


def write_summary(out):
    p = out["params"]
    lines = []
    lines.append(f"右侧浅回调筛选 · Right-Side Shallow Pullback")
    lines.append(f"数据日 {out['date']}   生成 {out['generated_at']}")
    lines.append(f"扫描 {out['scanned']} · 通过 {out['passed']}"
                 f"   (★ = AI/自选池, 评分含 +{AI_BONUS:.0f} 加成)")
    lines.append(f"参数: 回调 {p['depth_min']*100:.0f}-{p['depth_max']*100:.0f}% · "
                 f"RSI {p['rsi'][0]}-{p['rsi'][1]} · 距52周高<{p['from_52w_max']*100:.0f}% · "
                 f"日均成交额>${p['adv_min']/1e6:.0f}M")
    lines.append("")
    lines.append(f"{'#':>2} {'★':<1} {'代码':<10} {'评分':>5}  {'回调%':>6} {'距52周高%':>9} {'RSI':>5} "
                 f"{'偏离EMA10%':>10}  {'板块':<22} 名称")
    lines.append("-" * 112)
    for i, r in enumerate(out["results"], 1):
        tag = "★" if r.get("ai") else " "
        lines.append(f"{i:>2} {tag:<1} {r['symbol']:<10} {r['score']:>5}  {r['depth']:>6} "
                     f"{r['from_52w']:>9} {r['rsi']:>5} {r['ext_ema10']:>10}  "
                     f"{r['sector'][:22]:<22} {r['name']}")
    lines.append("")
    lines.append("注:技术筛选与研究用途,非投资建议。结构化数据见同目录 data.json;历史见 data/<日期>.json。")
    with open(os.path.join(HERE, "summary.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")

if __name__ == "__main__":
    screen()
