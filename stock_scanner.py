"""
狗腿子瞄準器-大展鴻圖 掃描核心
策略:盤整突破 / 回後買上漲 / 多頭起漲 / 多頭爆量 / 爆量起漲 / 均線糾結量縮蹲點
"""
import json
import os
import time
import numpy as np
import pandas as pd
import requests

SQUAT = "均線糾結量縮蹲點"
STRATEGY_NAMES = ["盤整突破", "回後買上漲", "多頭起漲", "多頭爆量", "爆量起漲", SQUAT]
BIG_VOL_STRATEGIES = {"盤整突破", "回後買上漲", "多頭爆量", "爆量起漲"}   # 需要「爆量倍數」的策略

CONFIG = {
    "period": "1y",
    "batch_size": 100,
    "strategies": list(STRATEGY_NAMES),
    "ma_list": [5, 10, 20],        # 線型標籤「剛站上均線」用
    "vol_mult": 2.0,               # 爆量:今日量 >= 前5日均量 x 2
    "range_days": 20,              # 盤整區間天數
    "range_pct": 0.15,             # 盤整:區間高低差 <= 15%
    "pullback_days": 10,           # 回檔回看天數
    "pullback_pct": 0.03,          # 最少回檔 3%
    "prev_ref": "high",            # 回後買上漲:越過前兩天的 "high" / "close"
    "body_pct": 0.03,              # 中長紅K:實體 >= 3%
    "close_pos": 0.7,              # 中長紅K:收盤落在當日振幅上方 30%
    "mild_vol": 1.2,               # 多頭起漲:溫和放量倍數
    "low_pos": 0.5,                # 爆量起漲:相對低檔位階 <= 50%
    "tangle_pct": 0.03,            # 蹲點:均線相差 <= 股價 3%(固定)
    "tangle_days": 3,              # 蹲點:連續 3 天(固定)
    "ma20_up_max": 0.015,          # 蹲點:MA20 近10日漲幅 0~1.5%(固定)
    "shrink_ratio": 0.8,           # 蹲點:5日均量 <= 20日均量 x 0.8(固定)
    "min_price": 30,               # 基本篩選(蹲點不套用)
    "min_lots": 1000,
    "chg_mode": "up",
    "chg_pct": 5,
    "pivot_window": 5,
    "cluster_pct": 0.015,
    "stop_buffer_atr": 0.5,
    "tickers_file": "tickers.txt",
    "output_csv": "scan_result.csv",
}


# ------------------------- 策略邏輯文字 -------------------------
def strategy_logic(name: str, cfg: dict) -> list:
    vol = f"今日成交量 ≥ 前5日均量的 {cfg['vol_mult']:g} 倍(5日均量不含今天)"
    body = f"實體 ≥ {cfg['body_pct'] * 100:g}%、收在當日振幅上方 {round((1 - cfg['close_pos']) * 100)}%"
    rng = f"前 {cfg['range_days']} 日最高價與最低價相差 ≤ {cfg['range_pct'] * 100:g}%"
    if name == "盤整突破":
        return [f"盤整:{rng}", "突破:今日收盤價 > 盤整區間最高價", vol]
    if name == "回後買上漲":
        ref = "最高價" if cfg["prev_ref"] == "high" else "收盤價"
        return [f"回後:近 {cfg['pullback_days']} 日高點到昨日收盤,回檔 ≥ {cfg['pullback_pct'] * 100:g}%",
                "趨勢未壞:收盤價 > MA20", f"買上漲:收盤價直接越過前兩天的{ref}", vol]
    if name == "多頭起漲":
        return ["符合下列其一:",
                f"① 盤整突破起漲:前 {cfg['range_days']} 日高低差 ≤ {cfg['range_pct'] * 100:g}%(盤整),今日收中長紅K({body}),"
                f"收盤 > 盤整上頸線(前{cfg['range_days']}日最高價),且均線多頭排列向上(MA5>MA10>MA20,三條都上揚)",
                f"② 回檔止跌再起漲:近 {cfg['pullback_days']} 日高點回檔 ≥ {cfg['pullback_pct'] * 100:g}%,"
                "回檔期間不破月線(MA20)、不破前低,MA20 向上、收盤在 MA20 之上,今日收紅K且收盤突破前一天最高點",
                f"量能(兩者共通):價漲量增,成交量 ≥ 前5日均量的 {cfg['mild_vol']:g} 倍且大於昨量"]
    if name == "多頭爆量":
        return ["多頭走勢:MA5 > MA10 > MA20 且三條都上揚,收盤 > MA20", vol,
                "位階標示(收盤在近120日高低區間的位置):前 1/3 = 低檔進貨量(偏多)、"
                "中 1/3 = 中段調節/換手量(看回檔是否守住)、後 1/3 = 高檔爆量(留意出貨,宜停利)"]
    if name == "爆量起漲":
        return [f"位置:前一日收盤在近120日區間下半部(位階 ≤ {cfg['low_pos'] * 100:g}%,相對低檔),或處於盤整末期({rng})",
                vol, f"今日收中長紅K({body})",
                f"收盤突破關鍵壓力(符合其一):前 {cfg['range_days']} 日高點(盤整上頸線/前高)、站上 MA20、站上 MA60"]
    if name == SQUAT:
        return [f"均線糾結:MA5、MA10、MA20 三條線最高與最低相差 ≤ 股價的 {cfg['tangle_pct'] * 100:g}%,"
                f"且連續 {cfg['tangle_days']} 天都維持",
                f"月線(MA20)上揚:近10日漲幅介於 0% ~ {cfg['ma20_up_max'] * 100:g}%,且今日不低於昨日",
                f"量縮:近5日均量 ≤ 20日均量的 {cfg['shrink_ratio']:g} 倍",
                "※ 此策略參數固定、不可調整,也不套用基本篩選"]
    return []


# ------------------------- 判斷用的小工具 -------------------------
def vol_ratio(df: pd.DataFrame) -> float:
    v = df["Volume"]
    base = v.shift(1).rolling(5).mean().iloc[-1]
    return float(v.iloc[-1] / base) if base and base > 0 else 0.0


def strong_red(df: pd.DataFrame, cfg: dict) -> bool:
    """中長紅K:收紅、實體夠大、收盤收在當日振幅的上方"""
    o, h, l, c = (float(df[k].iloc[-1]) for k in ("Open", "High", "Low", "Close"))
    if c <= o or (c - o) / o < cfg["body_pct"]:
        return False
    return (c - l) / (h - l) >= cfg["close_pos"] if h > l else True


def bull_aligned(c: pd.Series) -> bool:
    """MA5 > MA10 > MA20,三條今天都比昨天高,且收盤在 MA20 之上"""
    m = [c.rolling(n).mean() for n in (5, 10, 20)]
    return bool(m[0].iloc[-1] > m[1].iloc[-1] > m[2].iloc[-1] and all(x.iloc[-1] > x.iloc[-2] for x in m)
                and c.iloc[-1] > m[2].iloc[-1])


def position_pct(df: pd.DataFrame, use_prev: bool = False) -> float:
    d = (df.iloc[:-1] if use_prev else df).tail(120)
    hi, lo = d["High"].max(), d["Low"].min()
    return float((d["Close"].iloc[-1] - lo) / (hi - lo)) if hi > lo else 0.5


def tangle_spread(c: pd.Series) -> pd.Series:
    stack = pd.concat([c.rolling(n).mean() for n in (5, 10, 20)], axis=1)
    return (stack.max(axis=1) - stack.min(axis=1)) / c


def passes(df: pd.DataFrame, cfg: dict, name: str) -> bool:
    o, c, h, l, v = df["Open"], df["Close"], df["High"], df["Low"], df["Volume"]
    if name in BIG_VOL_STRATEGIES and vol_ratio(df) < cfg["vol_mult"]:
        return False

    ma20 = c.rolling(20).mean()
    if name == SQUAT:
        if not bool((tangle_spread(c).iloc[-cfg["tangle_days"]:] <= cfg["tangle_pct"]).all()):
            return False
        chg10 = ma20.iloc[-1] / ma20.iloc[-11] - 1
        if not (0 < chg10 <= cfg["ma20_up_max"] and ma20.iloc[-1] >= ma20.iloc[-2]):
            return False
        return bool(v.rolling(5).mean().iloc[-1] <= v.rolling(20).mean().iloc[-1] * cfg["shrink_ratio"])

    n = cfg["range_days"]
    top, bottom = h.iloc[-n - 1:-1].max(), l.iloc[-n - 1:-1].min()
    consolidating = (top - bottom) / bottom <= cfg["range_pct"]
    ma60 = c.rolling(60).mean()

    if name == "盤整突破":
        return bool(consolidating and c.iloc[-1] > top)

    if name == "回後買上漲":
        ref = h if cfg["prev_ref"] == "high" else c
        prev2 = max(ref.iloc[-2], ref.iloc[-3])
        swing = h.iloc[-1 - cfg["pullback_days"]:-1].max()
        pulled = (swing - c.iloc[-2]) / swing >= cfg["pullback_pct"]
        return bool(pulled and c.iloc[-1] > ma20.iloc[-1] and c.iloc[-1] > prev2)

    if name == "多頭起漲":
        if vol_ratio(df) < cfg["mild_vol"] or v.iloc[-1] <= v.iloc[-2]:
            return False
        a = bool(consolidating and strong_red(df, cfg) and c.iloc[-1] > top and bull_aligned(c))
        k = cfg["pullback_days"]
        swing = h.iloc[-1 - k:-1].max()
        pulled = (swing - c.iloc[-2]) / swing >= cfg["pullback_pct"]
        trend_up = ma20.iloc[-1] > ma20.iloc[-2] and c.iloc[-1] > ma20.iloc[-1]
        win_low = l.iloc[-1 - k:-1]
        hold_ma = bool((win_low >= ma20.iloc[-1 - k:-1] * 0.99).all())
        hold_low = bool(win_low.min() > l.iloc[-71:-1 - k].min())
        b = bool(pulled and trend_up and hold_ma and hold_low and c.iloc[-1] > o.iloc[-1] and c.iloc[-1] > h.iloc[-2])
        return a or b

    if name == "多頭爆量":
        return bull_aligned(c)

    if name == "爆量起漲":
        low_zone = position_pct(df, use_prev=True) <= cfg["low_pos"]
        broke = (c.iloc[-1] > top
                 or (c.iloc[-2] <= ma20.iloc[-2] and c.iloc[-1] > ma20.iloc[-1])
                 or (c.iloc[-2] <= ma60.iloc[-2] and c.iloc[-1] > ma60.iloc[-1]))
        return bool((low_zone or consolidating) and strong_red(df, cfg) and broke)
    return False


# ------------------------- 基本篩選 -------------------------
def basic_ok(df: pd.DataFrame, cfg: dict) -> bool:
    c = df["Close"]
    if c.iloc[-1] < cfg.get("min_price", 0):
        return False
    if df["Volume"].iloc[-1] < cfg.get("min_lots", 0) * 1000:
        return False
    chg = (c.iloc[-1] / c.iloc[-2] - 1) * 100
    mode, p = cfg.get("chg_mode", "none"), cfg.get("chg_pct", 0)
    if mode == "up" and chg < p:
        return False
    if mode == "down" and -chg < p:
        return False
    return True


def basic_filter_text(cfg: dict) -> str:
    parts = []
    if cfg.get("min_price", 0) > 0:
        parts.append(f"股價 ≥ {cfg['min_price']:g} 元")
    if cfg.get("min_lots", 0) > 0:
        parts.append(f"成交量 ≥ {cfg['min_lots']:,} 張")
    if cfg.get("chg_mode") == "up":
        parts.append(f"漲幅 ≥ {cfg['chg_pct']:g}%")
    elif cfg.get("chg_mode") == "down":
        parts.append(f"跌幅 ≥ {cfg['chg_pct']:g}%")
    return "、".join(parts)


def match_strategies(df: pd.DataFrame, cfg: dict, use_basic: bool = True) -> list:
    """回傳符合的策略清單。蹲點策略永遠不套用基本篩選。"""
    base = basic_ok(df, cfg) if use_basic else True
    out = []
    for nm in cfg["strategies"]:
        if (nm == SQUAT or base) and passes(df, cfg, nm):
            out.append(nm)
    return out


# ------------------------- 頭底 / 壓撐 -------------------------
def find_swings(df: pd.DataFrame, pct: float = 0.05) -> list:
    """回傳 [(位置, 價格, "頭"/"底"), ...],頭底交錯;最後一段未確認的高低點不標。"""
    hi, lo = df["High"].to_numpy(), df["Low"].to_numpy()
    pts, trend, hi_i, lo_i = [], 0, 0, 0
    for i in range(1, len(df)):
        if trend == 0:
            if hi[i] > hi[hi_i]:
                hi_i = i
            if lo[i] < lo[lo_i]:
                lo_i = i
            if hi_i > lo_i and hi[hi_i] >= lo[lo_i] * (1 + pct):
                pts.append((lo_i, float(lo[lo_i]), "底"))
                trend = 1
            elif lo_i > hi_i and lo[lo_i] <= hi[hi_i] * (1 - pct):
                pts.append((hi_i, float(hi[hi_i]), "頭"))
                trend = -1
        elif trend == 1:
            if hi[i] > hi[hi_i]:
                hi_i = i
            elif lo[i] <= hi[hi_i] * (1 - pct):
                pts.append((hi_i, float(hi[hi_i]), "頭"))
                trend, lo_i = -1, i
        else:
            if lo[i] < lo[lo_i]:
                lo_i = i
            elif hi[i] >= lo[lo_i] * (1 + pct):
                pts.append((lo_i, float(lo[lo_i]), "底"))
                trend, hi_i = 1, i
    return pts


def key_levels(df: pd.DataFrame, vol_mult: float = 2.0, lookback: int = 60, swing_pct: float = 0.05) -> list:
    """大量撐/壓、缺口撐/壓、前低撐/前高壓。回傳 [{"名稱","價格","類型"}, ...]"""
    out = []
    if len(df) < 10:
        return out
    close = float(df["Close"].iloc[-1])
    d = df.tail(lookback)
    vol5 = df["Volume"].shift(1).rolling(5).mean().reindex(d.index)
    big = d["Volume"] >= vol_mult * vol5
    s1 = d.loc[big & (d["High"] < close), "High"]
    if not s1.empty:
        out.append({"名稱": "大量撐", "價格": round(float(s1.max()), 2), "類型": "撐"})
    r1 = d.loc[big & (d["Low"] > close), "Low"]
    if not r1.empty:
        out.append({"名稱": "大量壓", "價格": round(float(r1.min()), 2), "類型": "壓"})
    H, L = d["High"].to_numpy(), d["Low"].to_numpy()
    up_gaps, down_gaps = [], []
    for i in range(1, len(d)):
        if L[i] > H[i - 1] and H[i - 1] < close and L[i:].min() > H[i - 1]:
            up_gaps.append(float(H[i - 1]))
        if H[i] < L[i - 1] and L[i - 1] > close and H[i:].max() < L[i - 1]:
            down_gaps.append(float(L[i - 1]))
    if up_gaps:
        out.append({"名稱": "缺口撐", "價格": round(max(up_gaps), 2), "類型": "撐"})
    if down_gaps:
        out.append({"名稱": "缺口壓", "價格": round(min(down_gaps), 2), "類型": "壓"})
    pts = find_swings(df.tail(120), swing_pct)
    lows = [p for p in pts if p[2] == "底" and p[1] < close]
    highs = [p for p in pts if p[2] == "頭" and p[1] > close]
    if lows:
        out.append({"名稱": "前低撐", "價格": round(lows[-1][1], 2), "類型": "撐"})
    if highs:
        out.append({"名稱": "前高壓", "價格": round(highs[-1][1], 2), "類型": "壓"})
    return out


def calc_atr(df: pd.DataFrame, n: int = 14) -> float:
    pc = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def find_levels(df, win, cluster_pct):
    k = 2 * win + 1
    hi = df["High"] == df["High"].rolling(k, center=True).max()
    lo = df["Low"] == df["Low"].rolling(k, center=True).min()
    pts = sorted(list(df["High"][hi].dropna()) + list(df["Low"][lo].dropna()))
    groups = []
    for p in pts:
        if groups and (p - np.mean(groups[-1])) / np.mean(groups[-1]) <= cluster_pct:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(float(np.mean(g)), len(g)) for g in groups]


def risk_reward(df: pd.DataFrame, cfg: dict) -> dict:
    entry = float(df["Close"].iloc[-1])
    atr = calc_atr(df)
    levels = find_levels(df, cfg["pivot_window"], cfg["cluster_pct"])
    sup = [x for x in levels if x[0] < entry * 0.995]
    res = [x for x in levels if x[0] > entry * 1.005]
    support = max(sup, key=lambda x: x[0]) if sup else None
    resistance = min(res, key=lambda x: x[0]) if res else None
    stop = support[0] - cfg["stop_buffer_atr"] * atr if support else entry - 2 * atr
    risk = entry - stop
    if resistance:
        target, note = resistance[0], ""
    else:
        target, note = entry + 3 * atr, "上方無壓力(目標以3ATR估)"
    rr = (target - entry) / risk if risk > 0 else np.nan
    return {
        "進場價": round(entry, 2),
        "支撐": round(support[0], 2) if support else None,
        "壓力": round(resistance[0], 2) if resistance else None,
        "停損": round(stop, 2), "目標": round(target, 2),
        "風險%": round(risk / entry * 100, 2),
        "報酬%": round((target - entry) / entry * 100, 2),
        "風報比": round(rr, 2),
        "支撐強度": support[1] if support else None,
        "壓力強度": resistance[1] if resistance else None,
        "備註": note,
    }


# ------------------------- 線型說明 -------------------------
def pattern_tags(df: pd.DataFrame, cfg: dict, matched: list) -> str:
    c = df["Close"]
    ma = {n: c.rolling(n).mean().iloc[-1] for n in (5, 10, 20, 60)}
    tags = []
    if ma[5] > ma[10] > ma[20] > ma[60]:
        tags.append("多頭排列")
    ref = [c.rolling(n).mean() for n in cfg["ma_list"]]
    if all(c.iloc[-1] > m.iloc[-1] for m in ref) and not all(c.iloc[-2] > m.iloc[-2] for m in ref):
        tags.append("剛站上MA5/10/20")
    if c.iloc[-1] > df["High"].iloc[-21:-1].max():
        tags.append("突破20日高")
    elif c.iloc[-1] >= df["High"].iloc[-61:-1].max() * 0.97:
        tags.append("逼近60日高")
    bias = (c.iloc[-1] / ma[20] - 1) * 100
    if bias > 10:
        tags.append(f"乖離大{bias:.0f}%")
    if SQUAT in matched:
        v = df["Volume"]
        tags.insert(0, f"均線糾結{tangle_spread(c).iloc[-1] * 100:.1f}%、"
                       f"量縮(5日量÷20日量={v.rolling(5).mean().iloc[-1] / v.rolling(20).mean().iloc[-1]:.2f})")
    if "多頭爆量" in matched or "爆量起漲" in matched:
        pos = position_pct(df)
        lab = "低檔·進貨量(偏多)" if pos < 1 / 3 else "中段·調節/換手量" if pos < 2 / 3 else "高檔·留意出貨"
        tags.insert(0, f"位階{pos * 100:.0f}% {lab}")
    return "、".join(tags) if tags else "一般"


def ma_condition_lines(df: pd.DataFrame, cfg: dict) -> list:
    """三陽開泰 / 四海遊龍 的條件對照(已取消成獨立策略,改在線型說明裡顯示)。回傳 [(是否全符合, 文字), ...]"""
    c = df["Close"]
    vr = vol_ratio(df)
    vol_ok = vr >= cfg["vol_mult"]
    three = all(c.iloc[-1] > c.rolling(n).mean().iloc[-1] for n in (5, 10, 20))
    four = three and c.iloc[-1] > c.rolling(60).mean().iloc[-1]
    yn = lambda x: "✅" if x else "❌"
    vol_txt = f"成交量 ≥ 前5日均量 {cfg['vol_mult']:g} 倍 {yn(vol_ok)}(量比 {vr:.2f})"
    return [(three and vol_ok, f"三陽開泰條件:站上三條均線 MA5/10/20 {yn(three)};{vol_txt}"),
            (four and vol_ok, f"四海遊龍條件:站上四條均線 MA5/10/20/60 {yn(four)};{vol_txt}")]


# ------------------------- 資料 -------------------------
NAMES_CACHE = "names_cache.json"


def _pick(d: dict, *keys) -> str:
    for k in keys:
        if d.get(k):
            return str(d[k]).strip()
    return ""


def _fetch_isin(modes) -> dict:
    out = {}
    for mode, suffix in modes:
        try:
            r = requests.get(f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}", timeout=10)
            r.encoding = "big5"
            for cell in pd.read_html(r.text)[0][0].dropna():
                parts = str(cell).split("\u3000")
                code = parts[0].strip()
                if code.isdigit() and len(code) == 4:
                    out[code + suffix] = parts[1].strip() if len(parts) > 1 else ""
        except Exception as e:
            print(f"[警告] ISIN 抓取失敗 (mode={mode}): {e}")
    return out


def fetch_market() -> dict:
    """回傳 {代號: 股名}。先讀本機快取(7天內),否則用開放資料 API,失敗再退回 ISIN;全部失敗時用舊快取。"""
    try:
        if os.path.exists(NAMES_CACHE) and time.time() - os.path.getmtime(NAMES_CACHE) < 7 * 86400:
            with open(NAMES_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            if cached:
                return cached
    except Exception:
        pass
    out = {}
    sources = [("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", ".TW"),
               ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", ".TWO")]
    for url, suffix in sources:
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for d in r.json():
                code = _pick(d, "公司代號", "SecuritiesCompanyCode", "Code")
                name = _pick(d, "公司簡稱", "CompanyAbbreviation", "Name")
                if code.isdigit() and len(code) == 4:
                    out[code + suffix] = name
        except Exception as e:
            print(f"[警告] 開放資料抓取失敗 ({url}): {e}")
    missing = [(m, sfx) for m, sfx in (("2", ".TW"), ("4", ".TWO")) if not any(k.endswith(sfx) for k in out)]
    if missing:
        out.update(_fetch_isin(missing))
    if out:
        try:
            with open(NAMES_CACHE, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        except Exception:
            pass
    else:
        try:
            with open(NAMES_CACHE, encoding="utf-8") as f:
                out = json.load(f)
        except Exception:
            pass
    return out


def normalize_ticker(code: str, names: dict = None) -> str:
    """2330 -> 2330.TW;6488 -> 6488.TWO(用股名清單判斷上市/上櫃,查不到預設 .TW)"""
    code = code.strip().upper()
    if not code or "." in code:
        return code
    for sfx in (".TW", ".TWO"):
        if code + sfx in (names or {}):
            return code + sfx
    return code + ".TW"


def load_tickers(cfg: dict) -> list:
    f = cfg["tickers_file"]
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            return [x.strip() for x in fh if x.strip()]
    m = fetch_market()
    if not m:
        raise SystemExit("沒有股票清單,請改用「自己輸入代號」或建立 tickers.txt")
    return list(m)


def download_batch(tickers: list, period: str) -> dict:
    import yfinance as yf
    raw = yf.download(tickers, period=period, group_by="ticker",
                      auto_adjust=True, threads=True, progress=False, timeout=20)
    result = {}
    for t in tickers:
        try:
            d = raw[t] if len(tickers) > 1 else raw
            d = d.dropna(subset=["Close"])
            if len(d) >= 80:
                result[t] = d
        except KeyError:
            continue
    return result


# ------------------------- 結果列 / 掃描 -------------------------
COLS = ["代號", "股名", "符合策略", "線型", "走勢", "成交量(張)", "量比", "漲跌幅%", "進場價", "支撐", "壓力",
        "停損", "目標", "風險%", "報酬%", "風報比", "支撐強度", "壓力強度", "備註"]


def make_row(t: str, df: pd.DataFrame, cfg: dict, names: dict, matched: list) -> dict:
    return {"代號": t, "股名": (names or {}).get(t, ""),
            "符合策略": "、".join(matched) if matched else "無",
            "線型": pattern_tags(df, cfg, matched),
            "走勢": df["Close"].tail(60).round(2).tolist(),
            "成交量(張)": int(df["Volume"].iloc[-1] / 1000), "量比": round(vol_ratio(df), 2),
            "漲跌幅%": round((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100, 2),
            **risk_reward(df, cfg)}


def scan(data: dict, cfg: dict, names: dict = None) -> pd.DataFrame:
    rows = []
    for t, df in data.items():
        try:
            matched = match_strategies(df, cfg, use_basic=True)
            if matched:
                rows.append(make_row(t, df, cfg, names, matched))
        except Exception as e:
            print(f"[略過] {t}: {e}")
    return pd.DataFrame(rows, columns=COLS) if rows else pd.DataFrame(columns=COLS)


def main():
    cfg = dict(CONFIG)
    tickers = load_tickers(cfg)
    names = fetch_market()
    print(f"共 {len(tickers)} 檔,開始掃描...")
    parts, got = [], 0
    for i in range(0, len(tickers), cfg["batch_size"]):
        try:
            data = download_batch(tickers[i:i + cfg["batch_size"]], cfg["period"])
            got += len(data)
            parts.append(scan(data, cfg, names))
        except Exception as e:
            print(f"[錯誤] 批次 {i} 失敗: {e}")
        print(f"進度 {min(i + cfg['batch_size'], len(tickers))}/{len(tickers)}")
        time.sleep(1)
    parts = [p for p in parts if not p.empty]
    hits = pd.concat(parts, ignore_index=True).sort_values("風報比", ascending=False) if parts else pd.DataFrame()
    print(f"\n抓到資料 {got} 檔,其中 {len(hits)} 檔符合")
    if not hits.empty:
        hits.drop(columns=["走勢"]).to_csv(cfg["output_csv"], index=False, encoding="utf-8-sig")
        print(hits.drop(columns=["走勢"]).to_string(index=False))


if __name__ == "__main__":
    main()
