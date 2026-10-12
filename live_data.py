"""
即時資料:證交所「基本市況報導網站(MIS)」報價 + Yahoo 分時走勢
- MIS 是證交所網站自己用的即時報價來源(約每 5 秒更新一次),屬非正式公開介面,個人看盤用;
  需先取得 session,太頻繁或大量查詢可能被擋。
- 指數代號:加權 = tse_t00.tw、櫃買 = otc_o00.tw
- Yahoo 分時(^TWII / ^TWOII)可能有延遲,只用來補當天早上的走勢歷史。
"""
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

TZ = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-TW"}
INDEX_CH = {"加權指數": "tse_t00.tw", "櫃買指數": "otc_o00.tw"}
INDEX_YF = {"加權指數": "^TWII", "櫃買指數": "^TWOII"}
_state = {"session": None, "t": 0.0}
_last_price = {}          # 當盤沒有成交(z = "-")時,沿用上一次的價格


def now_str() -> str:
    return datetime.now(TZ).strftime("%H:%M:%S")


def to_channel(ticker: str):
    """2330.TW -> tse_2330.tw;6488.TWO -> otc_6488.tw"""
    if ticker.endswith(".TWO"):
        return f"otc_{ticker[:-4]}.tw"
    if ticker.endswith(".TW"):
        return f"tse_{ticker[:-3]}.tw"
    return None


def index_channel_for(ticker: str) -> str:
    """上櫃跟櫃買指數比,上市跟加權指數比"""
    return INDEX_CH["櫃買指數"] if ticker.endswith(".TWO") else INDEX_CH["加權指數"]


def _session():
    if _state["session"] is None or time.time() - _state["t"] > 900:
        s = requests.Session()
        s.headers.update(UA)
        try:
            s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=8)
        except Exception:
            pass
        _state["session"], _state["t"] = s, time.time()
    return _state["session"]


def _f(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def mis_quotes(channels: list, chunk: int = 40) -> dict:
    """回傳 {頻道: {name, price, prev, chg, pct, open, high, low, vol, time}}。
    price 是最新成交價;vol 是累積成交量(張)。抓不到的頻道不會出現在結果裡。"""
    out = {}
    channels = [c for c in dict.fromkeys(channels) if c]
    for i in range(0, len(channels), chunk):
        part = channels[i:i + chunk]
        try:
            r = _session().get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp", timeout=8,
                               params={"ex_ch": "|".join(part), "json": 1, "delay": 0,
                                       "_": int(time.time() * 1000)})
            for m in r.json().get("msgArray", []):
                key = f"{m.get('ex')}_{m.get('ch')}"
                price, prev = _f(m.get("z")), _f(m.get("y"))
                if price is None:
                    price = _last_price.get(key)
                else:
                    _last_price[key] = price
                pct = (price / prev - 1) * 100 if price is not None and prev else None
                out[key] = {"name": m.get("n", ""), "price": price, "prev": prev,
                            "chg": (price - prev) if price is not None and prev else None, "pct": pct,
                            "open": _f(m.get("o")), "high": _f(m.get("h")), "low": _f(m.get("l")),
                            "vol": _f(m.get("v")), "time": m.get("t", ""), "date": m.get("d", "")}
        except Exception as e:
            print(f"[警告] MIS 即時報價抓取失敗: {e}")
            _state["session"] = None          # 下次重新取得 session
    return out


def live_for_tickers(tickers: list) -> dict:
    """回傳 {代號: 報價記錄,以及 "IDX_TW"/"IDX_TWO": 兩個指數的報價}"""
    chs = {t: to_channel(t) for t in tickers}
    q = mis_quotes(list(chs.values()) + list(INDEX_CH.values()))
    out = {t: q[c] for t, c in chs.items() if c and c in q}
    out["IDX_TW"] = q.get(INDEX_CH["加權指數"])
    out["IDX_TWO"] = q.get(INDEX_CH["櫃買指數"])
    return out


def strength(pct, idx_pct) -> str:
    """個股漲跌幅 vs 指數漲跌幅:優於 💪、劣於 👎,後面接差距(百分點)"""
    if pct is None or idx_pct is None or pd.isna(pct) or pd.isna(idx_pct):
        return "-"
    d = pct - idx_pct
    return f"💪 {d:+.2f}%" if d > 0 else (f"👎 {d:+.2f}%" if d < 0 else "➖ 0.00%")


def index_daily_pct() -> dict:
    """用日線算兩個指數最近一天的漲跌幅(掃描時當後備)"""
    out = {}
    try:
        import yfinance as yf
        for key, sym in (("TW", "^TWII"), ("TWO", "^TWOII")):
            try:
                h = yf.Ticker(sym).history(period="10d", interval="1d")["Close"].dropna()
                if len(h) >= 2:
                    out[key] = float((h.iloc[-1] / h.iloc[-2] - 1) * 100)
            except Exception:
                pass
    except Exception:
        pass
    return out


def yahoo_intraday(symbol: str):
    """回傳 (分時 DataFrame[time, price], 昨收);抓不到回傳 (None, None)。Yahoo 分時可能延遲。"""
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        h = tk.history(period="1d", interval="1m")
        if h is None or h.empty:
            return None, None
        idx = h.index
        try:
            idx = idx.tz_convert("Asia/Taipei").tz_localize(None)
        except Exception:
            idx = idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx
        df = pd.DataFrame({"time": idx, "price": h["Close"].to_numpy()}).dropna()
        daily = tk.history(period="10d", interval="1d")["Close"].dropna()
        day = df["time"].iloc[-1].date()
        before = daily[[d.date() < day for d in daily.index]]
        prev = float(before.iloc[-1]) if len(before) else None
        return df, prev
    except Exception as e:
        print(f"[警告] Yahoo 分時抓取失敗 ({symbol}): {e}")
        return None, None


def patch_bar(df, q):
    """把即時報價併入日K:今天的K棒已存在就更新(收盤=現價、高低用 MIS 當天高低、量用累積量),
    還沒有今天的K棒就補一根。報價日期比最後一根K棒還舊時不動。"""
    if not q or q.get("price") is None or df is None or len(df) == 0:
        return df
    try:
        d = df.copy()
        ds = str(q.get("date") or "")
        day = datetime.strptime(ds, "%Y%m%d").date() if len(ds) == 8 else datetime.now(TZ).date()
        price = float(q["price"])
        hi = max(x for x in (q.get("high"), price) if x is not None)
        lo = min(x for x in (q.get("low"), price) if x is not None)
        op = q.get("open") or price
        vol = (q.get("vol") or 0) * 1000                  # 張 → 股
        last = d.index[-1]
        if last.date() == day:
            d.loc[last, "Open"] = q.get("open") or d.loc[last, "Open"]
            d.loc[last, "High"], d.loc[last, "Low"], d.loc[last, "Close"] = hi, lo, price
            if vol > 0:
                d.loc[last, "Volume"] = vol
            return d
        if last.date() < day:
            ts = pd.Timestamp(day)
            if getattr(d.index, "tz", None) is not None:
                ts = ts.tz_localize(d.index.tz)
            new = pd.DataFrame([{"Open": op, "High": hi, "Low": lo, "Close": price, "Volume": vol}], index=[ts])
            return pd.concat([d, new])
    except Exception as e:
        print(f"[警告] 併入即時K棒失敗: {e}")
    return df
