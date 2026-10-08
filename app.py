"""
股票掃描器網站 (Streamlit)
啟動: streamlit run app.py   (與 stock_scanner.py 放同一資料夾)
"""
import re
import time
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import stock_scanner as sc


@st.cache_resource
def data_store() -> dict:
    """記憶體快取 {代號: (下載時間, DataFrame)},換條件重掃不用重新下載"""
    return {}


CACHE_SECONDS = 1800

st.set_page_config(page_title="股票掃描器", page_icon="📈", layout="wide")
st.title("📈 股票掃描器")


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


names = get_names()

# ----------------------------- 側邊欄 -----------------------------
with st.sidebar:
    st.header("⚙️ 設定")
    source = st.radio("股票範圍", ["自己輸入代號", "全部上市櫃(自動抓取)"])

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
                if nm:
                    lines.append(f"✅ {t.split('.')[0]} **{nm}**")
                elif names:
                    lines.append(f"⚠️ {t.split('.')[0]} 查無此代號")
                else:
                    lines.append(f"• {t.split('.')[0]}(股名清單未載入)")
            if len(typed) > 30:
                lines.append(f"…另有 {len(typed) - 30} 檔")
            st.caption("股名預覽")
            st.markdown("  \n".join(lines))

    st.divider()
    strategy = st.selectbox("策略", sc.STRATEGY_NAMES)
    logic_box = st.container()          # 策略邏輯顯示位置(等進階參數決定後再填)

    with st.expander("進階參數"):
        vol_mult = st.slider("爆量倍數(今日量 ÷ 前5日均量)", 1.0, 5.0, 2.0, 0.1)
        fresh = st.checkbox("均線要「今天剛站上」(三陽開泰/四海遊龍)", value=False)
        range_days = st.slider("盤整區間天數(突破盤整區間)", 10, 60, 20)
        range_pct = st.slider("盤整區間高低差上限 %", 3.0, 30.0, 15.0, 0.5) / 100
        pullback_days = st.slider("回檔回看天數(回後買上漲)", 5, 30, 10)
        pullback_pct = st.slider("最少回檔 %(回後買上漲)", 1.0, 15.0, 3.0, 0.5) / 100
        body_pct = st.slider("中長紅K實體 ≥ %(多頭起漲/爆量起漲)", 1.0, 8.0, 3.0, 0.5) / 100
        mild_vol = st.slider("溫和放量倍數(多頭起漲)", 1.0, 2.0, 1.2, 0.1)
        prev_ref = st.radio("越過前兩天的…", ["high", "close"], horizontal=True,
                            format_func=lambda x: "最高價" if x == "high" else "收盤價")

    with st.expander("基本篩選(同選股 App)"):
        min_price = st.number_input("股價 ≥(元)", 0.0, 5000.0, 0.0, 5.0, help="0 = 不限")
        vol_opt = st.selectbox("成交量(最新一日)≥", ["不限", "100張", "300張", "500張", "1000張"])
        chg_opt = st.selectbox("漲跌幅", ["不限", "漲幅 ≥ 3%", "漲幅 ≥ 5%", "跌幅 ≥ 3%", "跌幅 ≥ 5%"])
    min_lots = 0 if vol_opt == "不限" else int(vol_opt.replace("張", ""))
    chg_mode, chg_pct = "none", 0.0
    if chg_opt != "不限":
        chg_mode = "up" if chg_opt.startswith("漲") else "down"
        chg_pct = float(re.search(r"(\d+)%", chg_opt).group(1))
    basic = dict(min_price=min_price, min_lots=min_lots, chg_mode=chg_mode, chg_pct=chg_pct)

    cfg = sc.prepare_cfg(strategy, {**sc.CONFIG, **basic, "vol_mult": vol_mult, "fresh_cross": fresh,
                                    "range_days": range_days, "range_pct": range_pct,
                                    "pullback_days": pullback_days, "pullback_pct": pullback_pct,
                                    "prev_ref": prev_ref, "body_pct": body_pct, "mild_vol": mild_vol})
    with logic_box:
        st.markdown("**📐 策略邏輯**")
        st.markdown("\n".join(f"- {x}" for x in sc.strategy_logic(strategy, cfg)))

    run = st.button("🚀 開始掃描", type="primary", use_container_width=True)
    if st.button("🔄 清除快取(強制重新下載)", use_container_width=True):
        data_store().clear()
        st.success("已清除,下次掃描會重新下載")

# ----------------------------- 掃描 -----------------------------
if run:
    t0 = time.time()
    if source == "自己輸入代號":
        tickers = typed
        if not tickers:
            st.warning("請先輸入至少一個股票代號")
            st.stop()
    else:
        tickers = list(names)
        if not tickers:
            st.error("抓不到股票清單(網路問題),請改用「自己輸入代號」")
            st.stop()

    bar, msg = st.progress(0.0), st.empty()
    parts, got = [], 0
    for i in range(0, len(tickers), cfg["batch_size"]):
        try:
            batch = tickers[i:i + cfg["batch_size"]]
            store, now = data_store(), time.time()
            need = [t for t in batch if t not in store or now - store[t][0] > CACHE_SECONDS]
            if need:
                for t, d in sc.download_batch(need, cfg["period"]).items():
                    store[t] = (now, d)
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
    st.session_state["cfg"] = cfg
    st.session_state["info"] = (f"{time.strftime('%Y-%m-%d %H:%M')} ‧ 共 {len(tickers)} 檔,"
                                f"抓到資料 {got} 檔 ‧ 耗時 {time.time() - t0:.1f} 秒")

result = st.session_state.get("result")
if result is None:
    st.info("👈 左側輸入代號、選好策略後,按「開始掃描」。")
    st.stop()

run_cfg = st.session_state["cfg"]
st.subheader(f"「{run_cfg['strategy']}」共 {len(result)} 檔符合")
st.caption("條件:" + ";".join(sc.strategy_logic(run_cfg["strategy"], run_cfg)))
if sc.basic_filter_text(run_cfg):
    st.caption("基本篩選:" + sc.basic_filter_text(run_cfg))
st.caption(st.session_state["info"])
if result.empty:
    st.warning("沒有符合的股票。若上面「抓到資料」是 0 檔,代表資料下載失敗(網路或被限流),稍後再試。")
    st.stop()

st.download_button("⬇️ 下載 CSV", result.drop(columns=["走勢"]).to_csv(index=False).encode("utf-8-sig"),
                   "scan_result.csv", "text/csv")

tab1, tab2 = st.tabs(["📋 表格(點選一列看技術線型圖)", "🖼️ 圖卡總覽"])
event = None
with tab1:
    st.caption("👆 點選表格最左邊的小方框選取一檔,下方會顯示技術線型圖")
    event = st.dataframe(
        result, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        column_config={"走勢": st.column_config.LineChartColumn("近60日走勢", width="medium")})

with tab2:
    per = 12
    pages = (len(result) - 1) // per + 1
    page = st.number_input(f"頁數(共 {pages} 頁,依風報比排序)", 1, pages, 1) if pages > 1 else 1
    sub = result.iloc[(page - 1) * per: page * per]
    cols = st.columns(3)
    for k, (_, r) in enumerate(sub.iterrows()):
        with cols[k % 3]:
            st.markdown(f"**{r['代號']} {r['股名']}** ‧ 風報比 {r['風報比']}")
            st.caption(r["線型"])
            y = r["走勢"]
            fig = go.Figure(go.Scatter(y=y, mode="lines",
                                       line=dict(color="#e53935" if y[-1] >= y[0] else "#2e7d32", width=2)))
            for price, color in [(r["壓力"], "#e53935"), (r["支撐"], "#1e88e5")]:
                if price is not None and pd.notna(price):
                    fig.add_hline(y=price, line_dash="dot", line_color=color)
            fig.update_layout(height=170, margin=dict(l=0, r=0, t=5, b=0), showlegend=False,
                              xaxis=dict(visible=False))
            st.plotly_chart(fig, use_container_width=True, key=f"g{page}_{k}")
            st.caption(f"進 {r['進場價']} ‧ 損 {r['停損']} ‧ 標 {r['目標']}")

# ----------------------------- 技術線型圖(仿看盤軟體風格) -----------------------------
st.divider()
sel = event.selection.rows if event is not None else []
idx = sel[0] if sel and sel[0] < len(result) else 0
row = result.iloc[idx]
code = row["代號"]

st.subheader(f"📊 {code.split('.')[0]} {row['股名']} ‧ 技術線型圖")
if not sel:
    st.caption("(目前顯示風報比最高的一檔;點選上方表格任一列可切換)")
st.caption(f"線型:{row['線型']}")
m = st.columns(6)
m[0].metric("進場價", row["進場價"])
m[1].metric("停損", row["停損"], f"-{row['風險%']}%", delta_color="off")
m[2].metric("目標", row["目標"], f"+{row['報酬%']}%", delta_color="off")
m[3].metric("風報比", row["風報比"])
m[4].metric("成交量(張)", f"{row['成交量(張)']:,}")
m[5].metric("量比(÷5日均量)", row["量比"])
if row["備註"]:
    st.caption(f"⚠️ {row['備註']}")


@st.cache_data(ttl=3600)
def load_one(t):
    return sc.download_batch([t], "1y").get(t, pd.DataFrame())


cached = data_store().get(code)
df = cached[1] if cached is not None else load_one(code)

UP, DOWN, BG = "#ff3b30", "#22c55e", "#0b0e13"          # 台股:紅漲綠跌
MA_COLORS = {5: "#ffd54f", 10: "#4fc3f7", 20: "#ce93d8", 60: "#81c784"}

zc1, zc2, zc3 = st.columns([1, 1.4, 3])
show_swing = zc1.checkbox("顯示頭底", value=True)
show_keys = zc2.checkbox("顯示大量/缺口/前高低 壓撐", value=True)
zz_pct = zc3.slider("頭底波段幅度 %(越小,標得越多)", 2.0, 15.0, 5.0, 0.5) / 100

if df is not None and not df.empty:
    d = df.tail(120)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.74, 0.26], vertical_spacing=0.02)
    fig.add_trace(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
        increasing=dict(line=dict(color=UP), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN), name="K線"), row=1, col=1)
    for n, col in MA_COLORS.items():
        fig.add_trace(go.Scatter(x=d.index, y=df["Close"].rolling(n).mean().loc[d.index], mode="lines",
                                 name=f"MA{n}", line=dict(width=1.3, color=col)), row=1, col=1)

    # 頭 / 底:紫色「頭」、藍色「底」標籤,白色實線連線
    if show_swing:
        pts = sc.find_swings(d, zz_pct)
        if len(pts) >= 2:
            fig.add_trace(go.Scatter(x=[d.index[q[0]] for q in pts], y=[q[1] for q in pts], mode="lines",
                                     name="頭底連線", line=dict(color="white", width=1.6)), row=1, col=1)
        for kind, color, symbol in (("頭", "#d500f9", "triangle-down"), ("底", "#00b0ff", "triangle-up")):
            sp = [q for q in pts if q[2] == kind]
            if not sp:
                continue
            fig.add_trace(go.Scatter(x=[d.index[q[0]] for q in sp], y=[q[1] for q in sp], mode="markers",
                                     name=kind, marker=dict(color=color, size=7, symbol=symbol)), row=1, col=1)
            for q in sp:
                fig.add_annotation(x=d.index[q[0]], y=q[1], text=kind, showarrow=False,
                                   yshift=16 if kind == "頭" else -16, bgcolor=color, borderpad=2,
                                   font=dict(color="white", size=12), row=1, col=1)

    # 大量撐壓 / 缺口撐壓 / 前低撐・前高壓:綠色虛線=撐、紅色虛線=壓,線上有標籤、圖例有價格
    keys = sc.key_levels(d, run_cfg["vol_mult"], 60, zz_pct) if show_keys else []
    for kv in keys:
        color = "#2ecc71" if kv["類型"] == "撐" else "#ff5252"
        fig.add_trace(go.Scatter(x=[d.index[0], d.index[-1]], y=[kv["價格"]] * 2, mode="lines",
                                 name=f"{kv['名稱']} {kv['價格']}",
                                 line=dict(color=color, width=1.3, dash="dash")), row=1, col=1)
        fig.add_annotation(x=d.index[0], y=kv["價格"], text=f"{kv['名稱']} {kv['價格']}", showarrow=False,
                           xanchor="left", yshift=9, bgcolor=color, borderpad=2,
                           font=dict(color="white", size=11), row=1, col=1)

    # 壓力 / 支撐 / 停損 / 目標:實線橫跨整張圖 + 圖例 + 右側價格標籤
    def touches(n):
        return f"(觸碰{int(n)}次)" if n is not None and pd.notna(n) else ""

    levels = [("壓力", row["壓力"], "#ff7043", 2.4, touches(row["壓力強度"])),
              ("支撐", row["支撐"], "#29b6f6", 2.4, touches(row["支撐強度"])),
              ("目標", row["目標"], "#ffa726", 1.4, ""),
              ("停損", row["停損"], "#bdbdbd", 1.4, "")]
    for label, price, color, width, extra in levels:
        if price is None or pd.isna(price):
            continue
        fig.add_trace(go.Scatter(x=[d.index[0], d.index[-1]], y=[price, price], mode="lines",
                                 name=f"{label} {price}{extra}",
                                 line=dict(color=color, width=width, dash="solid" if width > 2 else "dash")),
                      row=1, col=1)
        fig.add_annotation(x=d.index[-1], y=price, text=f" {label} {price}", showarrow=False,
                           xanchor="left", font=dict(color=color, size=12), row=1, col=1)

    vol_color = [UP if c >= o else DOWN for c, o in zip(d["Close"], d["Open"])]
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"] / 1000, marker_color=vol_color, name="成交量(張)"),
                  row=2, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=(df["Volume"].rolling(5).mean() / 1000).loc[d.index], mode="lines",
                             name="5日均量", line=dict(color="#ffd54f", width=1.2)), row=2, col=1)

    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], gridcolor="#1f2630")
    fig.update_yaxes(gridcolor="#1f2630")
    fig.update_layout(height=700, template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
                      xaxis_rangeslider_visible=False, hovermode="x unified",
                      margin=dict(l=10, r=110, t=30, b=10),
                      legend=dict(orientation="h", y=1.06, x=0))
    st.plotly_chart(fig, use_container_width=True)
    if keys:
        st.caption("  ‧  ".join(f"{'🟢' if k['類型'] == '撐' else '🔴'} {k['名稱']} {k['價格']}" for k in keys))
    else:
        st.caption("目前沒有符合的大量 / 缺口 / 前高低 壓撐")
else:
    st.warning("無法載入此股票的K線資料")

st.caption("技術面輔助工具,不構成投資建議。資料來自 Yahoo Finance;圖表為仿看盤軟體風格,非三竹官方畫面。")
