"""
狗腿子瞄準器-大展鴻圖 (Streamlit)
啟動: streamlit run app.py   (與 stock_scanner.py 放同一資料夾)
"""
import re
import time
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import stock_scanner as sc

TITLE = "狗腿子瞄準器-大展鴻圖"
st.set_page_config(page_title=TITLE, page_icon="🎯", layout="wide")
st.markdown("""<style>
div[class*='st-key-chartbox']{border:2px solid #87CEFA;border-radius:10px;padding:8px 8px 2px 8px;}
div[class*='st-key-tiles'] button{min-height:68px;border-radius:12px;font-weight:600;line-height:1.3;}
div[class*='st-key-tiles'] [data-testid='stHorizontalBlock']{flex-wrap:wrap !important;gap:.5rem;}
div[class*='st-key-tiles'] [data-testid='stColumn'],div[class*='st-key-tiles'] [data-testid='column']{
  min-width:30% !important;flex:1 1 30% !important;width:auto !important;}
</style>""", unsafe_allow_html=True)
st.title(f"🎯 {TITLE}")

CACHE_SECONDS = 1800
UP, DOWN, BG = "#ff3b30", "#22c55e", "#0b0e13"          # 台股:紅漲綠跌
MA_COLORS = {5: "#ffd54f", 10: "#4fc3f7", 20: "#ce93d8", 60: "#81c784"}
EXTRA_MA_STRATS = {"多頭起漲", "多頭爆量", "爆量起漲"}      # 這三個策略的線型下方,會顯示三陽開泰/四海遊龍條件


@st.cache_resource
def data_store() -> dict:
    """記憶體快取 {代號: (下載時間, DataFrame)},換條件重掃不用重新下載"""
    return {}


@st.cache_data(ttl=3600)
def load_one(t):
    return sc.download_batch([t], "1y").get(t, pd.DataFrame())


def get_df(code: str) -> pd.DataFrame:
    cached = data_store().get(code)
    return cached[1] if cached is not None else load_one(code)


def get_names() -> dict:
    """股名對照表。失敗時 10 分鐘內不再重試,避免每次都卡在等網路。"""
    if st.session_state.get("names"):
        return st.session_state["names"]
    if time.time() - st.session_state.get("names_failed_at", 0) < 600:
        return {}
    with st.spinner("取得股名對照表…(第一次較久,成功後會存成 names_cache.json)"):
        names = sc.fetch_market()
    if names:
        st.session_state["names"] = names
    else:
        st.session_state["names_failed_at"] = time.time()
        st.warning("股名抓取失敗(網路或網站擋連線),這次先不顯示股名,其他功能不受影響。")
    return names


def is_mobile_client() -> bool:
    try:
        ua = st.context.headers.get("User-Agent", "")
        return any(k in ua for k in ("iPhone", "Android", "Mobile"))
    except Exception:
        return False


def f2(x):
    """統一顯示到小數點後兩位"""
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "-"


@st.cache_data(ttl=600, show_spinner=False)
def get_inst():
    """三大法人買賣超(張),回傳 ({代號: {外資, 投信, 自營}}, 資料日期)"""
    return sc.fetch_institutional()


names = get_names()
D = sc.CONFIG

# ============================ 側邊欄 ============================
with st.sidebar:
    st.header("⚙️ 設定")
    source = st.radio("股票範圍", ["全部上市櫃", "自己輸入代號"])
    typed = []
    if source == "自己輸入代號":
        pasted = st.text_area("股票代號(逗號、空白或換行分隔)", "2330\n2317\n2454\n6488",
                              help="直接輸入 2330 即可,不用加 .TW / .TWO")
        seen = set()
        for raw in re.split(r"[,\s;，、]+", pasted):
            t = sc.normalize_ticker(raw, names)
            if t and t not in seen:
                seen.add(t)
                typed.append(t)
        if typed:
            lines = []
            for t in typed[:30]:
                nm = names.get(t, "")
                code_ = t.split(".")[0]
                lines.append(f"✅ {code_} **{nm}**" if nm else
                             (f"⚠️ {code_} 查無此代號" if names else f"• {code_}(股名清單未載入)"))
            if len(typed) > 30:
                lines.append(f"…另有 {len(typed) - 30} 檔")
            st.caption("股名預覽")
            st.markdown("  \n".join(lines))

    st.divider()
    strategies = [s for s in sc.STRATEGY_NAMES if s in st.session_state.get("sel_strats", [])]
    st.markdown("**目前檢視**:" + (strategies[0] if strategies else "全部策略(掃描後,點上方方塊分類)"))
    logic_box = st.container()

    with st.expander("進階參數"):
        vol_mult = st.slider("爆量倍數(盤整突破/回後買上漲/多頭爆量/爆量起漲)", 1.0, 5.0, 2.0, 0.1)
        body_pct = st.slider("中長紅K實體 ≥ %(多頭起漲/爆量起漲)", 1.0, 8.0, 3.0, 0.5) / 100
        mild_vol = st.slider("溫和放量倍數(多頭起漲)", 1.0, 2.0, 1.2, 0.1)
        pullback_pct = st.slider("最少回檔 %(回後買上漲)", 1.0, 15.0, 3.0, 0.5) / 100
        pullback_days = st.slider("回檔回看天數(回後買上漲)", 5, 30, 10)
        prev_ref = st.radio("回後買上漲:越過前兩天的…", ["high", "close"], horizontal=True,
                            format_func=lambda x: "最高價" if x == "high" else "收盤價")
        st.caption(f"{sc.SQUAT}:參數固定,不可調整")

    with st.expander("基本篩選", expanded=True):
        min_price = st.number_input("股價 ≥(元)", 0.0, 5000.0, 30.0, 5.0, help="0 = 不限")
        vol_opt = st.selectbox("成交量(最新一日)≥", ["不限", "100張", "300張", "500張", "1000張"], index=4)
        chg_opt = st.selectbox("漲跌幅", ["不限", "漲幅 ≥ 3%", "漲幅 ≥ 5%", "跌幅 ≥ 3%", "跌幅 ≥ 5%"], index=2)
        st.caption(f"※「{sc.SQUAT}」不套用基本篩選")
    with st.expander("🏦 外資 / 投信 / 自營買賣超"):
        inst_sel = st.multiselect("篩選(可多選,預設不篩選)", sc.INST_OPTIONS, default=[])
        inst_min = st.number_input("至少幾張", 0, 1_000_000, 0, 100, help="0 = 只要買超或賣超就算")
        st.caption("選多項代表「全部都要符合」。資料來自證交所/櫃買,收盤後約下午4點才有當日資料。"
                   "結果表會多出外資、投信、自營(張)三欄。")
    min_lots = 0 if vol_opt == "不限" else int(vol_opt.replace("張", ""))
    chg_mode, chg_pct = "none", 0.0
    if chg_opt != "不限":
        chg_mode = "up" if chg_opt.startswith("漲") else "down"
        chg_pct = float(re.search(r"(\d+)%", chg_opt).group(1))

    cfg = {**D, "strategies": list(sc.STRATEGY_NAMES), "vol_mult": vol_mult, "body_pct": body_pct, "mild_vol": mild_vol,
           "pullback_pct": pullback_pct, "pullback_days": pullback_days, "prev_ref": prev_ref,
           "min_price": min_price, "min_lots": min_lots, "chg_mode": chg_mode, "chg_pct": chg_pct}

    with logic_box:
        st.markdown("**📐 策略邏輯**")
        for nm in (strategies or sc.STRATEGY_NAMES):
            with st.expander(nm, expanded=len(strategies) == 1):
                st.markdown("\n".join(f"- {x}" for x in sc.strategy_logic(nm, cfg)))

    st.divider()
    mobile = st.checkbox("📱 手機版面", value=is_mobile_client(),
                         help="縮小指標與標籤、圖例移到圖下方、只畫近60日、不干擾上下滑動")
    if st.button("🔄 清除快取(強制重新下載)", use_container_width=True):
        data_store().clear()
        st.success("已清除,下次掃描會重新下載")


# ============================ 詳細說明 + 技術圖 ============================
def render_detail(row: dict, df: pd.DataFrame, cfg: dict, matched: list, kp: str):
    code = row["代號"]
    st.divider()
    title = f"{code.split('.')[0]} {row['股名']}"
    st.markdown(f"##### 📊 {title}" if mobile else f"### 📊 {title}")

    # ---- 線型說明 ----
    st.markdown("**📌 線型說明**")
    st.markdown(f"- 策略:{row['符合策略']}")
    st.markdown(f"- 線型:{row['線型']}")
    if pd.notna(row.get("外資(張)")):
        st.markdown(f"- 法人買賣超({st.session_state.get('inst_date', '')}):外資 {int(row['外資(張)']):+,} 張、"
                    f"投信 {int(row['投信(張)']):+,} 張、自營 {int(row['自營(張)']):+,} 張")
    if set(matched) & EXTRA_MA_STRATS:
        for ok, txt in sc.ma_condition_lines(df, cfg):
            st.markdown(f"- {'🟢' if ok else '⚪'} {txt}")

    sig = sc.macd_signal(df) if df is not None and len(df) > 40 else None
    if sig:
        when = lambda k: "今日" if k == 0 else f"{k} 日前"
        alerts = []
        if sig["golden"] is not None:
            alerts.append(f"MACD 黃金交叉({when(sig['golden'])},DIF 上穿 DEA,"
                          f"{'零軸上方' if sig['above_zero'] else '零軸下方'})")
        if sig["red"] is not None:
            alerts.append(f"MACD 紅柱出現({when(sig['red'])},柱狀體由綠轉紅)")
        if alerts:
            st.success("🔔 " + ";".join(alerts))
        st.caption(f"MACD(12,26,9):DIF {f2(sig['dif'])}、DEA {f2(sig['dea'])}、柱狀體 {f2(sig['osc'])}"
                   + ("" if alerts else ";近 3 日沒有黃金交叉或紅柱出現"))

    items = [("進場價", f2(row["進場價"]), ""), ("停損", f2(row["停損"]), f"-{f2(row['風險%'])}%"),
             ("目標", f2(row["目標"]), f"+{f2(row['報酬%'])}%"), ("風報比", f2(row["風報比"]), ""),
             ("成交量(張)", f"{row['成交量(張)']:,}", ""), ("量比", f2(row["量比"]), "÷前5日均量")]
    if mobile:
        cells = "".join(
            f"<div style='flex:0 0 33.33%;box-sizing:border-box;padding:2px 4px'>"
            f"<div style='font-size:11px;opacity:.65'>{lab}</div>"
            f"<div style='font-size:18px;font-weight:600;line-height:1.25'>{val}</div>"
            f"<div style='font-size:11px;opacity:.65;min-height:14px'>{sub}</div></div>" for lab, val, sub in items)
        st.markdown(f"<div style='display:flex;flex-wrap:wrap'>{cells}</div>", unsafe_allow_html=True)
    else:
        for col, (lab, val, sub) in zip(st.columns(6), items):
            col.metric(lab, val, sub or None, delta_color="off")

    # ---- 支撐壓力 / 目標價邏輯說明 ----
    sup, res = row["支撐"], row["壓力"]
    with st.expander("🧮 支撐、壓力、停損與目標價怎麼算", expanded=not mobile):
        st.markdown(
            f"- **支撐 {f2(sup) if sup is not None and pd.notna(sup) else '無'}**"
            f"(強度:近期被碰觸 {row['支撐強度'] if pd.notna(row['支撐強度']) else 0} 次,次數越多越強)\n"
            f"- **壓力 {f2(res) if res is not None and pd.notna(res) else '無'}**"
            f"(強度:近期被碰觸 {row['壓力強度'] if pd.notna(row['壓力強度']) else 0} 次,次數越多越強)\n"
            f"- **停損 {f2(row['停損'])}** = 最近支撐下方 {cfg['stop_buffer_atr']:g} 倍 ATR(找不到支撐時,用進場價 − 2 倍 ATR)\n"
            f"- **目標 {f2(row['目標'])}** = 最近的壓力價(上方沒有壓力時,用進場價 + 3 倍 ATR 估算)\n"
            f"- **風報比 {f2(row['風報比'])}** = (目標 − 進場價)÷(進場價 − 停損)\n"
            f"- 支撐壓力是以近一年的轉折高低點,把相差 {cfg['cluster_pct'] * 100:g}% 以內的價位合併成一個價位區算出來的")
        if row["備註"]:
            st.caption(f"⚠️ {row['備註']}")

    # ---- 圖表控制 ----
    c1, c2 = st.columns([1, 1.6])
    show_swing = c1.checkbox("顯示頭底", value=True, key=f"{kp}_swing")
    show_keys = c2.checkbox("顯示大量/缺口/前高低 壓撐", value=True, key=f"{kp}_keys")
    zz_pct = st.slider("頭底波段幅度 %(越小,標得越多)", 2.0, 15.0, 8.0 if mobile else 5.0, 0.5,
                       key=f"{kp}_zz") / 100

    if df is None or df.empty:
        st.warning("無法載入此股票的K線資料")
        return

    d = df.tail(60 if mobile else 120)
    fs = 9 if mobile else 12
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.58, 0.16, 0.26], vertical_spacing=0.025)
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing=dict(line=dict(color=UP), fillcolor=UP), decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN),
        name="K線", showlegend=False), row=1, col=1)
    for n, col in MA_COLORS.items():
        fig.add_trace(go.Scatter(x=d.index, y=df["Close"].rolling(n).mean().loc[d.index], mode="lines",
                                 name=f"MA{n}", line=dict(width=1.3, color=col)), row=1, col=1)

    if show_swing:      # 頭 / 底 + 白色實線
        pts = sc.find_swings(d, zz_pct)
        if len(pts) >= 2:
            fig.add_trace(go.Scatter(x=[d.index[q[0]] for q in pts], y=[q[1] for q in pts], mode="lines",
                                     name="頭底連線", line=dict(color="white", width=1.6)), row=1, col=1)
        for kind, color, symbol in (("頭", "#d500f9", "triangle-down"), ("底", "#00b0ff", "triangle-up")):
            sp = [q for q in pts if q[2] == kind]
            if not sp:
                continue
            fig.add_trace(go.Scatter(x=[d.index[q[0]] for q in sp], y=[q[1] for q in sp], mode="markers",
                                     name=kind, showlegend=False,
                                     marker=dict(color=color, size=5 if mobile else 7, symbol=symbol)), row=1, col=1)
            for q in sp:
                fig.add_annotation(x=d.index[q[0]], y=q[1], text=kind, showarrow=False,
                                   yshift=(11 if mobile else 16) * (1 if kind == "頭" else -1),
                                   bgcolor=color, borderpad=1 if mobile else 2,
                                   font=dict(color="white", size=fs), row=1, col=1)

    level_prices = []

    def add_line(label, price, color, width, dash, text_color="white", fill=None):
        """畫橫線 + 圖例 + 線的最右邊標籤(標籤放在圖內右端)"""
        level_prices.append(float(price))
        fig.add_trace(go.Scatter(x=[d.index[0], d.index[-1]], y=[price, price], mode="lines",
                                 name=f"{label} {f2(price)}", line=dict(color=color, width=width, dash=dash)),
                      row=1, col=1)
        fig.add_annotation(x=d.index[-1], y=price, text=f"{label} {f2(price)}", showarrow=False, xanchor="right",
                           yshift=9, bgcolor=fill or "rgba(11,14,19,0.75)", borderpad=1 if mobile else 2,
                           font=dict(color=text_color, size=fs - 1), row=1, col=1)

    keys = sc.key_levels(d, cfg["vol_mult"], 60, zz_pct) if show_keys else []
    for kv in keys:         # 大量撐壓 / 缺口撐壓 / 前低撐・前高壓(綠=撐、紅=壓)
        color = "#2ecc71" if kv["類型"] == "撐" else "#ff5252"
        add_line(kv["名稱"], kv["價格"], color, 1.3, "dash", fill=color)

    def touches(n):
        return f"(觸碰{int(n)}次)" if n is not None and pd.notna(n) else ""

    for label, price, color, width, extra in [("壓力", row["壓力"], "#ff7043", 2.4, touches(row["壓力強度"])),
                                              ("支撐", row["支撐"], "#29b6f6", 2.4, touches(row["支撐強度"])),
                                              ("目標", row["目標"], "#ffa726", 1.4, ""),
                                              ("停損", row["停損"], "#bdbdbd", 1.4, "")]:
        if price is None or pd.isna(price):
            continue
        add_line(label + extra, price, color, width, "solid" if width > 2 else "dash", text_color=color)

    vol_color = [UP if c >= o else DOWN for c, o in zip(d["Close"], d["Open"])]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"] / 1000, marker_color=vol_color, name="成交量(張)",
                         showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=(df["Volume"].rolling(5).mean() / 1000).loc[d.index], mode="lines",
                             name="5日均量", showlegend=False, line=dict(color="#ffd54f", width=1.2)), row=2, col=1)

    # ---- MACD(12,26,9):柱狀體 + DIF + DEA,黃金交叉打星號 ----
    mac = sc.macd(df).loc[d.index]
    fig.add_trace(go.Bar(x=d.index, y=mac["OSC"], marker_color=[UP if v >= 0 else DOWN for v in mac["OSC"]],
                         name="MACD柱", showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=mac["DIF"], mode="lines", name="DIF",
                             line=dict(color="#ffd54f", width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=mac["DEA"], mode="lines", name="DEA(訊號線)",
                             line=dict(color="#4fc3f7", width=1.2)), row=3, col=1)
    crosses = [i for i in range(1, len(mac)) if mac["DIF"].iloc[i] > mac["DEA"].iloc[i]
               and mac["DIF"].iloc[i - 1] <= mac["DEA"].iloc[i - 1]]
    if crosses:
        fig.add_trace(go.Scatter(x=[mac.index[i] for i in crosses], y=[mac["DIF"].iloc[i] for i in crosses],
                                 mode="markers", name="MACD黃金交叉",
                                 marker=dict(symbol="star", size=9 if mobile else 12, color="#fff176",
                                             line=dict(color="#ff9800", width=1))), row=3, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)

    lo_, hi_ = float(d["Low"].min()), float(d["High"].max())      # Y 軸:太遠的線不要把圖拉扁
    for pr in level_prices:
        if lo_ * 0.9 <= pr <= hi_ * 1.1:
            lo_, hi_ = min(lo_, pr), max(hi_, pr)
    fig.update_yaxes(range=[lo_ * 0.98, hi_ * 1.02], row=1, col=1)
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], gridcolor="#1f2630")
    fig.update_yaxes(gridcolor="#1f2630")
    fig.update_layout(template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG, xaxis_rangeslider_visible=False)
    if mobile:      # 圖例在下方
        fig.update_layout(height=780, margin=dict(l=5, r=5, t=10, b=190), hovermode="closest", dragmode=False,
                          legend=dict(orientation="h", x=0, y=-0.08, yanchor="top", font=dict(size=10)))
    else:           # 圖例在最右邊
        fig.update_layout(height=860, margin=dict(l=10, r=10, t=20, b=10), hovermode="x unified",
                          legend=dict(orientation="v", x=1.01, y=1, xanchor="left", yanchor="top",
                                      font=dict(size=11)))
    try:
        box = st.container(key=f"chartbox_{kp}")
    except TypeError:
        box = st.container(border=True)
    with box:
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{kp}",
                        config={"displayModeBar": False, "scrollZoom": False} if mobile else {})
    if keys:
        st.caption("  ‧  ".join(f"{'🟢' if k['類型'] == '撐' else '🔴'} {k['名稱']} {f2(k['價格'])}" for k in keys))
    else:
        st.caption("目前沒有符合的大量 / 缺口 / 前高低 壓撐")


# ============================ 掃描 ============================
def do_scan():
    t0 = time.time()
    tickers = typed if source == "自己輸入代號" else list(names)
    if not tickers:
        st.warning("請先輸入至少一個股票代號" if source == "自己輸入代號"
                   else "抓不到股票清單(網路問題),請改用「自己輸入代號」")
        return
    bar, msg = st.progress(0.0), st.empty()
    parts, got = [], 0
    for i in range(0, len(tickers), cfg["batch_size"]):
        try:
            batch = tickers[i:i + cfg["batch_size"]]
            store, now = data_store(), time.time()
            need = [t for t in batch if t not in store or now - store[t][0] > CACHE_SECONDS]
            if need:
                for t, d_ in sc.download_batch(need, cfg["period"]).items():
                    store[t] = (now, d_)
            data = {t: store[t][1] for t in batch if t in store}
            got += len(data)
            parts.append(sc.scan(data, cfg, names))
        except Exception as e:
            st.warning(f"批次 {i} 下載失敗:{e}")
        done = min(i + cfg["batch_size"], len(tickers))
        bar.progress(done / len(tickers))
        msg.text(f"進度 {done}/{len(tickers)}")
    msg.empty()
    parts = [p for p in parts if not p.empty]
    st.session_state["result"] = (pd.concat(parts, ignore_index=True).sort_values("風報比", ascending=False)
                                  .reset_index(drop=True) if parts else pd.DataFrame())
    result = st.session_state["result"]
    note = ""
    inst, inst_date = get_inst()
    if inst and not result.empty:
        for col, key in (("外資(張)", "外資"), ("投信(張)", "投信"), ("自營(張)", "自營")):
            result[col] = pd.to_numeric(result["代號"].map(lambda t, k=key: (inst.get(t) or {}).get(k)),
                                        errors="coerce")
        if inst_sel:
            keep = result["代號"].map(lambda t: sc.inst_ok(inst.get(t), inst_sel, inst_min))
            result = result[keep].reset_index(drop=True)
            note = f" ‧ 法人篩選:{'、'.join(inst_sel)}" + (f"(≥{inst_min:,}張)" if inst_min else "")
        st.session_state["result"] = result
        note += f" ‧ 法人資料日期 {inst_date}"
    elif inst_sel:
        note = " ‧ ⚠️ 法人資料抓取失敗,未套用法人篩選"
    st.session_state["inst_date"] = inst_date
    st.session_state["cfg"] = cfg
    st.session_state["info"] = (f"{time.strftime('%Y-%m-%d %H:%M')} ‧ 共 {len(tickers)} 檔,"
                                f"抓到資料 {got} 檔 ‧ 耗時 {time.time() - t0:.1f} 秒{note}")


DISPLAY_COLS = ["代號", "股名", "符合策略", "MACD提示", "成交量(張)", "量比", "漲跌幅%", "外資(張)", "投信(張)", "自營(張)",
                "進場價", "支撐", "壓力",
                "停損", "目標", "風險%", "報酬%", "風報比"]


def inst_color(v):
    try:
        return "color:#ff3b30" if v > 0 else ("color:#22c55e" if v < 0 else "")    # 買超紅、賣超綠
    except Exception:
        return ""


def vr_color(v):
    if v > 3:
        return "color:#ff3b30;font-weight:700"      # 大於 3 倍:紅
    if v >= 2:
        return "color:#ff9500;font-weight:700"      # 2~3 倍:橘
    return ""


def scan_view():
    result = st.session_state.get("result")
    if result is None:
        st.info("👆 按「開始掃描」,一次掃完全部 6 個策略;掃完點上方策略方塊,就能看到各策略分到哪些股票。"
                "第一次掃全市場要幾分鐘;也可以到「股票查詢」直接查單一檔。")
        return
    run_cfg = st.session_state["cfg"]
    total_n = len(result)
    if total_n == 0:
        st.subheader("全部策略共 0 檔")
        st.caption(st.session_state["info"])
        st.warning("沒有符合的股票。可到左側放寬基本篩選或爆量倍數;若「抓到資料」是 0 檔,代表下載失敗(網路或被限流),稍後再試。")
        return
    if strategies:      # 點了策略方塊:只看分到這個策略的股票
        result = result[result["符合策略"].astype(str).apply(
            lambda s_: strategies[0] in s_.split("、"))].reset_index(drop=True)
        st.subheader(f"「{strategies[0]}」共 {len(result)} 檔")
        st.caption(f"全部策略合計 {total_n} 檔(同一檔可能同時符合多個策略)")
        if result.empty:
            st.info("這個策略目前沒有符合的股票。可以點別的方塊,或到左側放寬基本篩選。")
            return
    else:
        st.subheader(f"全部策略共 {len(result)} 檔")
        st.caption("點上方策略方塊,可以只看分到該策略的股票")
    if sc.basic_filter_text(run_cfg):
        st.caption(f"基本篩選:{sc.basic_filter_text(run_cfg)}(「{sc.SQUAT}」不套用)")
    st.caption(st.session_state["info"])
    if result.empty:
        st.warning("沒有符合的股票。可到左側放寬基本篩選或爆量倍數;若「抓到資料」是 0 檔,代表下載失敗(網路或被限流),稍後再試。")
        return

    only_macd = st.checkbox("🔔 只顯示近3日出現 MACD 黃金交叉 / 紅柱的股票", value=False, key="only_macd")
    if only_macd:
        result = result[result["MACD提示"].astype(str) != ""].reset_index(drop=True)
        st.caption(f"MACD 篩選後:{len(result)} 檔")
        if result.empty:
            st.info("目前沒有出現 MACD 黃金交叉或紅柱的股票")
            return

    st.download_button("⬇️ 下載目前清單 CSV", result.drop(columns=["走勢"]).to_csv(index=False).encode("utf-8-sig"),
                       "scan_result.csv", "text/csv")
    tab1, tab2 = st.tabs(["📋 表格(點選一列看詳細說明與技術圖)", "🖼️ 圖卡總覽"])
    event = None
    with tab1:
        st.caption("👆 點選表格最左邊的小方框選取一檔,下方會顯示線型說明與技術線型圖。量比:大於 3 倍紅字、2~3 倍橘字")
        disp = result[[c for c in DISPLAY_COLS if c in result.columns]]
        fmt = {c: "{:.2f}" for c in ["量比", "進場價", "支撐", "壓力", "停損", "目標", "風險%", "報酬%", "風報比"]}
        fmt["漲跌幅%"] = "{:+.2f}"
        fmt.update({c: "{:+,.0f}" for c in ["外資(張)", "投信(張)", "自營(張)"]})
        fmt["成交量(張)"] = "{:,.0f}"
        styler = disp.style.format({k: v for k, v in fmt.items() if k in disp.columns}, na_rep="-")
        mapper = styler.map if hasattr(styler, "map") else styler.applymap
        styler = mapper(vr_color, subset=["量比"])
        inst_cols = [c for c in ("外資(張)", "投信(張)", "自營(張)") if c in disp.columns]
        if inst_cols:
            styler = (styler.map if hasattr(styler, "map") else styler.applymap)(inst_color, subset=inst_cols)
        event = st.dataframe(styler, use_container_width=True, hide_index=True,
                             on_select="rerun", selection_mode="single-row")
    with tab2:
        per = 12
        pages = (len(result) - 1) // per + 1
        page = st.number_input(f"頁數(共 {pages} 頁,依風報比排序)", 1, pages, 1) if pages > 1 else 1
        cols = st.columns(3)
        for k, (_, r) in enumerate(result.iloc[(page - 1) * per: page * per].iterrows()):
            with cols[k % 3]:
                st.markdown(f"**{r['代號']} {r['股名']}** ‧ 風報比 {f2(r['風報比'])}")
                st.caption(r["符合策略"])
                y = r["走勢"]
                fg = go.Figure(go.Scatter(y=y, mode="lines",
                                          line=dict(color="#e53935" if y[-1] >= y[0] else "#2e7d32", width=2)))
                for price, color in [(r["壓力"], "#e53935"), (r["支撐"], "#1e88e5")]:
                    if price is not None and pd.notna(price):
                        fg.add_hline(y=price, line_dash="dot", line_color=color)
                fg.update_layout(height=170, margin=dict(l=0, r=0, t=5, b=0), showlegend=False,
                                 xaxis=dict(visible=False))
                st.plotly_chart(fg, use_container_width=True, key=f"g{page}_{k}")
                st.caption(f"進 {f2(r['進場價'])} ‧ 損 {f2(r['停損'])} ‧ 標 {f2(r['目標'])}")

    sel = event.selection.rows if event is not None else []
    idx = sel[0] if sel and sel[0] < len(result) else 0
    row = result.iloc[idx].to_dict()
    if not sel:
        st.caption("(目前顯示風報比最高的一檔;點選上方表格任一列可切換)")
    render_detail(row, get_df(row["代號"]), run_cfg, row["符合策略"].split("、"), "scan")


# ============================ 股票查詢 ============================
def find_matches(q: str) -> list:
    q = q.strip().upper()
    if not q:
        return []
    if "." in q:
        return [q]
    exact = [t for t in names if t.split(".")[0] == q]
    prefix = [t for t in names if t.split(".")[0].startswith(q) and t not in exact]
    by_name = [t for t, nm in names.items() if q in nm.upper() and t not in exact + prefix]
    found = (exact + prefix + by_name)[:30]
    if not found and not names and q.isalnum():
        found = [sc.normalize_ticker(q, names)]
    return found


def query_view():
    st.caption("輸入代號或股名,立刻看這一檔的線型、目前符合哪些策略(查詢不套用基本篩選)")
    q = st.text_input("股票代號或股名", placeholder="例如 2330 或 台積電", key="query_text")
    if not q.strip():
        return
    found = find_matches(q)
    if not found:
        st.warning("找不到這檔股票,請確認代號或股名")
        return
    code = found[0] if len(found) == 1 else st.selectbox(
        "找到多檔,請選擇", found, format_func=lambda t: f"{t.split('.')[0]} {names.get(t, '')}")
    with st.spinner("載入資料…"):
        df = get_df(code)
    if df is None or df.empty or len(df) < 80:
        st.warning("抓不到足夠的K線資料(新上市、下市、或資料來源被限流)")
        return
    cfg_all = {**cfg, "strategies": list(sc.STRATEGY_NAMES)}
    matched = sc.match_strategies(df, cfg_all, use_basic=False)
    st.markdown("**目前符合的策略**:" + ("、".join(matched) if matched else "沒有符合任何策略"))
    st.markdown("  ‧  ".join(f"{'✅' if nm in matched else '❌'} {nm}" for nm in sc.STRATEGY_NAMES))
    row = sc.make_row(code, df, cfg_all, names, matched)
    inst, inst_date = get_inst()
    if inst.get(code):
        row.update({"外資(張)": inst[code]["外資"], "投信(張)": inst[code]["投信"], "自營(張)": inst[code]["自營"]})
        st.session_state["inst_date"] = inst_date
    render_detail(row, df, cfg_all, matched, "query")


# ============================ 策略方塊 ============================
def toggle_strat(nm: str):
    """單選:點別的方塊就換成那個;再點一次已選的就取消"""
    cur = st.session_state.get("sel_strats", [])
    st.session_state["sel_strats"] = [] if nm in cur else [nm]


def tiles_view() -> bool:
    """畫出策略方塊與「開始掃描」,回傳這次有沒有按下開始掃描"""
    st.markdown("**🎯 策略分類**(先按下方「開始掃描」,掃完點方塊就會看到分到該策略的股票;再點一次取消,看全部)")
    res, ran = st.session_state.get("result"), st.session_state.get("cfg", {}).get("strategies", [])
    counts = {}
    if res is not None and not res.empty:
        for s_ in res["符合策略"]:
            for nm in str(s_).split("、"):
                counts[nm] = counts.get(nm, 0) + 1
    try:
        box = st.container(key="tiles")
    except TypeError:
        box = st.container()
    with box:
        cols = st.columns(3)
        for i, nm in enumerate(sc.STRATEGY_NAMES):
            label = f"{nm} ({counts.get(nm, 0)})" if res is not None else nm
            cols[i % 3].button(label, key=f"tile_{nm}", on_click=toggle_strat, args=(nm,),
                               type="primary" if nm in strategies else "secondary", use_container_width=True)
    return st.button("🚀 開始掃描", type="primary", use_container_width=True, key="run_scan")


# ============================ 主畫面 ============================
tab_scan, tab_query = st.tabs(["🎯 策略掃描", "🔍 股票查詢"])
with tab_scan:
    run = tiles_view()
    if run:
        do_scan()
    scan_view()
with tab_query:
    query_view()

st.caption("技術面輔助工具,不構成投資建議。資料來自 Yahoo Finance;圖表為仿看盤軟體風格,非三竹官方畫面。")
