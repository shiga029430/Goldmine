# app.py
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

TROY_OZ_G = 31.1034768  # 1トロイオンス = 31.1035 g
UP = "#16a34a"    # 利益の色（緑）
DOWN = "#dc2626"  # 損失の色（赤）
GOLD = "#d4a017"

# グラフは「なぞると値段が出る」以外の操作をすべて無効化
CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False, "doubleClick": False}

PERIODS = {
    "1か月": pd.DateOffset(months=1),
    "半年": pd.DateOffset(months=6),
    "1年": pd.DateOffset(years=1),
    "5年": pd.DateOffset(years=5),
    "10年": pd.DateOffset(years=10),
    "全期間": None,
}

st.set_page_config(page_title="金の値段と損益", layout="wide")


@st.cache_data(ttl=3600)
def load_prices() -> pd.DataFrame:
    gold = yf.download("GC=F", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    fx = yf.download("JPY=X", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    df = pd.concat({"usd_oz": gold, "usdjpy": fx}, axis=1).sort_index().ffill().dropna()
    df["jpy_g"] = df["usd_oz"] * df["usdjpy"] / TROY_OZ_G
    return df


def fmt_g(v: float) -> str:
    """g数を小数点以下2桁まで，不要な0を省いて表示"""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def pl_color(v: float) -> str:
    return UP if v >= 0 else DOWN


def market_price(df: pd.DataFrame, d) -> float:
    """指定日（休日なら直前の営業日）の1gあたりの相場"""
    return float(df["jpy_g"].asof(pd.Timestamp(d)))


def make_record(df: pd.DataFrame, d, grams, paid) -> dict:
    """日付と，量・金額の少なくとも一方から記録を作る．足りない方は相場から計算"""
    price = market_price(df, d)
    auto = ""
    if grams is None or pd.isna(grams) or grams == 0:
        grams = paid / price
        auto = "量"
    elif paid is None or pd.isna(paid) or paid == 0:
        paid = grams * price
        auto = "金額"
    return {"date": pd.Timestamp(d).date().isoformat(),
            "grams": float(grams), "paid": float(paid), "auto": auto}


def style_chart(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        dragmode=False,
        hovermode="x unified",
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", itemclick=False, itemdoubleclick=False),
        font=dict(size=14),
    )
    fig.update_xaxes(fixedrange=True, hoverformat="%Y年%m月%d日")
    fig.update_yaxes(fixedrange=True, tickformat=",")
    return fig


def hero_card(pl: float, pl_pct: float, cost: float, value: float, qty: float) -> str:
    if pl >= 0:
        bg, icon, label = "linear-gradient(135deg,#15803d,#22c55e)", "📈", "もうけ"
    else:
        bg, icon, label = "linear-gradient(135deg,#b91c1c,#f87171)", "📉", "損"
    return f"""
<div style="background:{bg};border-radius:18px;padding:20px 24px;color:#fff;
margin:8px 0 16px;box-shadow:0 6px 18px rgba(0,0,0,.25)">
<div style="font-size:1.1rem;opacity:.95">{icon} 今売ったときの{label}（全部合計）</div>
<div style="font-size:2.8rem;font-weight:800;line-height:1.15">{pl:+,.0f} 円</div>
<div style="font-size:1.4rem;font-weight:700">{pl_pct:+.2f}%</div>
<div style="margin-top:12px;font-size:1rem;opacity:.95;line-height:1.7">
買った金額 {cost:,.0f} 円 → 今の価値 {value:,.0f} 円<br>
持っている金 {fmt_g(qty)} g
</div>
</div>"""


def buy_card(r: pd.Series, now: float) -> str:
    c = pl_color(r["損益"])
    note = '<span style="opacity:.6;font-size:.85rem">（相場から計算）</span>'
    g_note = note if r["auto"] == "量" else ""
    p_note = note if r["auto"] == "金額" else ""
    return f"""
<div style="border:1px solid {c}55;border-left:8px solid {c};border-radius:12px;
padding:12px 16px;margin:8px 0 4px;background:{c}14">
<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px;font-size:1.05rem">
<b>{r['購入日'].strftime('%Y年%m月%d日')} に {fmt_g(r['数量(g)'])} g{g_note}</b>
<span style="color:{c};font-weight:800;font-size:1.4rem">{r['損益']:+,.0f} 円（{r['損益率(%)']:+.2f}%）</span>
</div>
<div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:4px 14px;margin-top:8px;font-size:1rem">
<span></span><span style="opacity:.65">買った時</span><span style="opacity:.65">今</span>
<span style="opacity:.65">1gの値段</span><span>{r['購入時単価']:,.0f} 円</span><span>{now:,.0f} 円</span>
<span style="opacity:.65">金額</span><span>{r['取得額']:,.0f} 円{p_note}</span><span style="color:{c};font-weight:700">{r['評価額']:,.0f} 円</span>
</div>
</div>"""


def records_to_df(records: list[dict], now: float) -> pd.DataFrame:
    b = pd.DataFrame(records)
    b["idx"] = range(len(b))
    b["購入日"] = pd.to_datetime(b["date"])
    b["数量(g)"] = b["grams"].astype(float)
    b["取得額"] = b["paid"].astype(float)
    b["購入時単価"] = b["取得額"] / b["数量(g)"]
    b["評価額"] = b["数量(g)"] * now
    b["損益"] = b["評価額"] - b["取得額"]
    b["損益率(%)"] = b["損益"] / b["取得額"] * 100
    return b.sort_values("購入日")


df = load_prices()
first_date = df.index.min().date()
last_date = df.index.max().date()
now = float(df["jpy_g"].iloc[-1])

if "buys" not in st.session_state:
    st.session_state["buys"] = []


def add_record():
    """「追加する」ボタンの処理．追加後に入力欄を空に戻す"""
    d = st.session_state["in_date"]
    g = st.session_state["in_grams"]
    p = st.session_state["in_paid"]
    st.session_state["buys"].append(make_record(df, d, g, p))
    st.session_state["in_date"] = None
    st.session_state["in_grams"] = None
    st.session_state["in_paid"] = None
    st.session_state["added_msg"] = True


# ---- ヘッダー ----
st.title("金の値段と損益")
st.metric(f"今日の金の値段（1gあたり，{last_date.strftime('%Y年%m月%d日')}時点）", f"{now:,.0f} 円")

# ---- 購入記録の追加 ----
st.subheader("買った金を記録する")
st.caption("「買った日」と，「買った量」か「支払った金額」のどちらか一方を入れてください．もう一方はその日の相場から計算します．")

d = st.date_input("買った日", value=None, min_value=first_date, max_value=last_date,
                  format="YYYY/MM/DD", key="in_date")
g = st.number_input("買った量（g）", min_value=0.0, step=1.0, value=None,
                    placeholder="例：10", key="in_grams")
p = st.number_input("支払った金額（円・合計）", min_value=0.0, step=1000.0, value=None,
                    placeholder="例：120000", key="in_paid")

has_g = g is not None and g > 0
has_p = p is not None and p > 0
ready = d is not None and (has_g or has_p)

if d is not None:
    price = market_price(df, d)
    if has_g and has_p:
        st.info(f"入力どおり {fmt_g(g)} g を {p:,.0f} 円（1gあたり {p / g:,.0f} 円）で記録します．"
                f"（この日の相場は1gあたり {price:,.0f} 円）")
    elif has_g:
        st.info(f"この日の相場は1gあたり {price:,.0f} 円です．"
                f"→ 支払った金額は 約 {g * price:,.0f} 円 として記録します．")
    elif has_p:
        st.info(f"この日の相場は1gあたり {price:,.0f} 円です．"
                f"→ 買った量は 約 {fmt_g(p / price)} g として記録します．")

st.button("追加する", type="primary", use_container_width=True,
          disabled=not ready, on_click=add_record)

if st.session_state.pop("added_msg", False):
    st.success("記録を追加しました．")

records = st.session_state["buys"]
has_buys = len(records) > 0

if has_buys:
    buys = records_to_df(records, now)

    # ---- トータル損益 ----
    qty, cost, value = buys["数量(g)"].sum(), buys["取得額"].sum(), buys["評価額"].sum()
    pl = value - cost
    st.html(hero_card(pl, pl / cost * 100, cost, value, qty))

    if pl > 0 and not st.session_state.get("celebrated"):
        st.balloons()
        st.session_state["celebrated"] = True

    # ---- 購入ごとの明細 ----
    st.subheader("買った分ごとの損益")
    for _, r in buys.iterrows():
        st.html(buy_card(r, now))
        if st.button("🗑 この記録を消す", key=f"del_{r['idx']}"):
            st.session_state["buys"].pop(int(r["idx"]))
            st.rerun()
else:
    st.info("上の欄に入力して「追加する」を押すと，もうけ・損が表示されます．")

# ---- 価格推移チャート ----
st.subheader("金の値段の移り変わり（1gあたり）")
st.caption("グラフを指でなぞると，その日の値段が表示されます．")
period = st.segmented_control("表示する期間", list(PERIODS), default="全期間", key="period")
if period is None:
    period = "全期間"

offset = PERIODS[period]
start = pd.Timestamp(first_date) if offset is None else pd.Timestamp(last_date) - offset
view = df[df.index >= start]

fig = go.Figure()
fig.add_trace(go.Scatter(x=view.index, y=view["jpy_g"], name="金の値段",
                         line=dict(width=2, color=GOLD),
                         hovertemplate="%{y:,.0f} 円<extra></extra>"))
if has_buys:
    shown = buys[buys["購入日"] >= start]
    if not shown.empty:
        fig.add_trace(go.Scatter(
            x=shown["購入日"], y=shown["購入日"].map(lambda dt: market_price(df, dt)),
            mode="markers", name="買った日",
            marker=dict(size=14, symbol="triangle-up",
                        color=[pl_color(v) for v in shown["損益"]],
                        line=dict(width=1, color="white")),
            customdata=[fmt_g(v) for v in shown["数量(g)"]],
            hovertemplate="買った日（%{customdata} g）<extra></extra>",
        ))
st.plotly_chart(style_chart(fig), use_container_width=True, config=CHART_CONFIG)

# ---- 保有資産の推移 ----
if has_buys:
    st.subheader("買った金額と今の価値の移り変わり")
    s = buys.groupby("購入日")[["数量(g)", "取得額"]].sum()
    s = s.reindex(df.index.union(s.index), fill_value=0).cumsum().reindex(df.index)
    hist = pd.DataFrame({"取得額": s["取得額"], "評価額": s["数量(g)"] * df["jpy_g"]})
    hist = hist[hist.index >= max(start, buys["購入日"].min())]

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=hist.index, y=hist["取得額"], name="買った金額",
                              line=dict(shape="hv", color="#9ca3af", width=2),
                              hovertemplate="買った金額 %{y:,.0f} 円<extra></extra>"))
    fig2.add_trace(go.Scatter(x=hist.index, y=hist["評価額"], name="今の価値",
                              line=dict(color=GOLD, width=2),
                              fill="tonexty", fillcolor="rgba(212,160,23,0.25)",
                              hovertemplate="価値 %{y:,.0f} 円<extra></extra>"))
    st.plotly_chart(style_chart(fig2), use_container_width=True, config=CHART_CONFIG)

# ---- 記録の保存・読み込み ----
with st.expander("記録の保存・読み込み"):
    st.caption("このページを閉じると記録は消えます．保存したファイルを次回読み込むと元に戻せます．")
    if has_buys:
        out = pd.DataFrame(records).rename(columns={
            "date": "購入日", "grams": "数量(g)", "paid": "支払額(円)", "auto": "自動計算"})
        st.download_button("記録をファイルに保存", out.to_csv(index=False).encode("utf-8-sig"),
                           "gold_buys.csv", use_container_width=True)
    uploaded = st.file_uploader("保存したファイルを読み込む", type="csv")
    if uploaded is not None and st.session_state.get("loaded_file") != (uploaded.name, uploaded.size):
        csv = pd.read_csv(uploaded)
        grams = csv["数量(g)"] if "数量(g)" in csv.columns else pd.Series([None] * len(csv))
        if "支払額(円)" in csv.columns:
            paid = csv["支払額(円)"]
        elif "購入単価(円/g)" in csv.columns:  # 旧形式のCSVにも対応
            paid = csv["購入単価(円/g)"] * grams
        else:
            paid = pd.Series([None] * len(csv))
        new_records = []
        for i, (dt, gr, pd_) in enumerate(zip(csv["購入日"], grams, paid)):
            rec = make_record(df, dt, gr, pd_)
            # 保存済みファイルの自動計算フラグを引き継ぐ
            if "自動計算" in csv.columns and isinstance(csv["自動計算"].iloc[i], str):
                rec["auto"] = csv["自動計算"].iloc[i]
            new_records.append(rec)
        st.session_state["buys"] = new_records
        st.session_state["loaded_file"] = (uploaded.name, uploaded.size)
        st.rerun()
