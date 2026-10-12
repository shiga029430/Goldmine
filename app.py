# app.py
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

TROY_OZ_G = 31.1034768  # 1トロイオンス = 31.1035 g

st.set_page_config(page_title="金価格トラッカー", layout="wide")


@st.cache_data(ttl=3600)
def load_prices() -> pd.DataFrame:
    gold = yf.download("GC=F", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    fx = yf.download("JPY=X", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    df = pd.concat({"usd_oz": gold, "usdjpy": fx}, axis=1).ffill().dropna()
    df["jpy_g"] = df["usd_oz"] * df["usdjpy"] / TROY_OZ_G
    return df


df = load_prices()

# ---- サイドバー ----
with st.sidebar:
    start = st.date_input("表示開始日", pd.Timestamp("2015-01-01"))
    unit = st.radio("価格単位", ["円/g", "USD/oz"])
col = "jpy_g" if unit == "円/g" else "usd_oz"
view = df[df.index >= pd.Timestamp(start)]

st.title("金価格トラッカー")
latest = df.iloc[-1]
c1, c2, c3 = st.columns(3)
c1.metric("最新 円/g", f"{latest['jpy_g']:,.0f}")
c2.metric("最新 USD/oz", f"{latest['usd_oz']:,.1f}")
c3.metric("USD/JPY", f"{latest['usdjpy']:.2f}")

# ---- 購入履歴の入力 ----
st.subheader("購入履歴")
uploaded = st.file_uploader("CSVから読み込み（任意）", type="csv")
if uploaded:
    init = pd.read_csv(uploaded, parse_dates=["購入日"])
    init["購入日"] = init["購入日"].dt.date
else:
    init = pd.DataFrame({
        "購入日": [pd.Timestamp("2020-04-01").date()],
        "数量(g)": [10.0],
        "購入単価(円/g)": [float("nan")],
    })

buys = st.data_editor(
    init,
    num_rows="dynamic",
    key="buys",
    column_config={
        "購入日": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "数量(g)": st.column_config.NumberColumn(min_value=0.0, step=0.1),
        "購入単価(円/g)": st.column_config.NumberColumn(help="空欄ならその日の相場価格を使用"),
    },
)
st.download_button("購入履歴をCSV保存", buys.to_csv(index=False).encode("utf-8-sig"), "gold_buys.csv")

buys = buys.dropna(subset=["購入日", "数量(g)"]).copy()
if buys.empty:
    st.info("購入履歴を入力してください．")
    st.stop()

buys["購入日"] = pd.to_datetime(buys["購入日"])
market = buys["購入日"].map(lambda d: df["jpy_g"].asof(d))
buys["約定単価"] = buys["購入単価(円/g)"].astype(float).fillna(market)
buys["取得額"] = buys["数量(g)"] * buys["約定単価"]
now = latest["jpy_g"]
buys["評価額"] = buys["数量(g)"] * now
buys["損益"] = buys["評価額"] - buys["取得額"]
buys["損益率(%)"] = buys["損益"] / buys["取得額"] * 100

# ---- 集計 ----
qty, cost, value = buys["数量(g)"].sum(), buys["取得額"].sum(), buys["評価額"].sum()
pl = value - cost
m1, m2, m3, m4 = st.columns(4)
m1.metric("保有量", f"{qty:,.2f} g")
m2.metric("平均取得単価", f"{cost / qty:,.0f} 円/g")
m3.metric("評価額", f"{value:,.0f} 円")
m4.metric("損益", f"{pl:,.0f} 円", f"{pl / cost * 100:+.2f}%")
st.dataframe(buys.style.format({
    "約定単価": "{:,.0f}", "取得額": "{:,.0f}", "評価額": "{:,.0f}",
    "損益": "{:+,.0f}", "損益率(%)": "{:+.2f}",
}), use_container_width=True)

# ---- 価格推移チャート（購入点つき）----
st.subheader(f"価格推移（{unit}）")
fig = go.Figure()
fig.add_trace(go.Scatter(x=view.index, y=view[col], name="金価格", line=dict(width=1.5)))
fig.add_trace(go.Scatter(
    x=buys["購入日"], y=buys["購入日"].map(lambda d: df[col].asof(d)),
    mode="markers", name="購入", marker=dict(size=10, symbol="triangle-up"),
    text=buys["数量(g)"].map(lambda q: f"{q} g"),
))
st.plotly_chart(fig, use_container_width=True)

# ---- 保有資産の推移 ----
st.subheader("取得額・評価額の推移（円）")
s = buys.groupby("購入日")[["数量(g)", "取得額"]].sum()
s = s.reindex(df.index.union(s.index), fill_value=0).cumsum().reindex(df.index)
hist = pd.DataFrame({"取得額": s["取得額"], "評価額": s["数量(g)"] * df["jpy_g"]})
hist = hist[hist.index >= max(pd.Timestamp(start), buys["購入日"].min())]
fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=hist.index, y=hist["取得額"], name="取得額", line=dict(shape="hv")))
fig2.add_trace(go.Scatter(x=hist.index, y=hist["評価額"], name="評価額", fill="tonexty"))
st.plotly_chart(fig2, use_container_width=True)
