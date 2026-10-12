# app.py
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

TROY_OZ_G = 31.1034768  # 1トロイオンス = 31.1035 g
UP = "#16a34a"    # 利益の色（緑）
DOWN = "#dc2626"  # 損失の色（赤）

st.set_page_config(page_title="金価格トラッカー", layout="wide")


@st.cache_data(ttl=3600)
def load_prices() -> pd.DataFrame:
    gold = yf.download("GC=F", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    fx = yf.download("JPY=X", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    df = pd.concat({"usd_oz": gold, "usdjpy": fx}, axis=1).sort_index().ffill().dropna()
    df["jpy_g"] = df["usd_oz"] * df["usdjpy"] / TROY_OZ_G
    return df


def pl_color(v: float) -> str:
    return UP if v >= 0 else DOWN


def hero_card(pl: float, pl_pct: float, cost: float, value: float,
              qty: float, avg: float, now: float) -> str:
    if pl >= 0:
        bg, icon, label = "linear-gradient(135deg,#15803d,#22c55e)", "📈", "含み益"
    else:
        bg, icon, label = "linear-gradient(135deg,#b91c1c,#f87171)", "📉", "含み損"
    return f"""
<div style="background:{bg};border-radius:18px;padding:20px 24px;color:#fff;
margin:8px 0 16px;box-shadow:0 6px 18px rgba(0,0,0,.25)">
<div style="font-size:1rem;opacity:.9">{icon} トータル{label}</div>
<div style="font-size:2.8rem;font-weight:800;line-height:1.15">{pl:+,.0f} 円</div>
<div style="font-size:1.4rem;font-weight:700">{pl_pct:+.2f}%</div>
<div style="margin-top:12px;font-size:.92rem;opacity:.95;line-height:1.6">
取得額 {cost:,.0f} 円 → 評価額 {value:,.0f} 円<br>
保有 {qty:,.2f} g ／ 平均取得 {avg:,.0f} 円/g → 現在 {now:,.0f} 円/g
</div>
</div>"""


def buy_card(r: pd.Series, now: float) -> str:
    c = pl_color(r["損益"])
    src = "（相場）" if r["相場で計算"] else ""
    return f"""
<div style="border:1px solid {c}55;border-left:8px solid {c};border-radius:12px;
padding:12px 16px;margin:8px 0;background:{c}14">
<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px">
<b>{r['購入日'].strftime('%Y-%m-%d')} ・ {r['数量(g)']:g} g</b>
<span style="color:{c};font-weight:800;font-size:1.35rem">{r['損益']:+,.0f} 円（{r['損益率(%)']:+.2f}%）</span>
</div>
<div style="display:grid;grid-template-columns:auto 1fr 1fr;gap:2px 14px;margin-top:8px;font-size:.95rem">
<span></span><span style="opacity:.65">購入時</span><span style="opacity:.65">現在</span>
<span style="opacity:.65">単価</span><span>{r['購入時単価']:,.0f} 円/g{src}</span><span>{now:,.0f} 円/g</span>
<span style="opacity:.65">金額</span><span>{r['取得額']:,.0f} 円</span><span style="color:{c};font-weight:700">{r['評価額']:,.0f} 円</span>
</div>
</div>"""


df = load_prices()
first_date = df.index.min().date()
last_date = df.index.max().date()
latest = df.iloc[-1]
now = latest["jpy_g"]

# ---- ヘッダー・最新価格 ----
st.title("金価格トラッカー")
c1, c2, c3 = st.columns(3)
c1.metric("最新 円/g", f"{latest['jpy_g']:,.0f}")
c2.metric("最新 USD/oz", f"{latest['usd_oz']:,.1f}")
c3.metric("USD/JPY", f"{latest['usdjpy']:.2f}")
st.caption(f"データ期間: {first_date} 〜 {last_date}")

# ---- 購入履歴の入力 ----
st.subheader("購入履歴")
st.caption("購入日とg数を入れると，購入時と現在の価格を表示します．購入単価が分かる場合は入力すると正確になります．")
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

edited = st.data_editor(
    init,
    num_rows="dynamic",
    key="buys",
    use_container_width=True,
    column_config={
        "購入日": st.column_config.DateColumn(format="YYYY-MM-DD",
                                              min_value=first_date, max_value=last_date),
        "数量(g)": st.column_config.NumberColumn(min_value=0.0, step=0.1),
        "購入単価(円/g)": st.column_config.NumberColumn(help="空欄ならその日の相場価格を使用"),
    },
)
st.download_button("購入履歴をCSV保存", edited.to_csv(index=False).encode("utf-8-sig"), "gold_buys.csv")

buys = edited.dropna(subset=["購入日", "数量(g)"]).copy()
buys = buys[buys["数量(g)"] > 0]
has_buys = not buys.empty

if has_buys:
    buys["購入日"] = pd.to_datetime(buys["購入日"])
    market = buys["購入日"].map(lambda d: df["jpy_g"].asof(d))
    entered = buys["購入単価(円/g)"].astype(float)
    buys["相場で計算"] = entered.isna()
    buys["購入時単価"] = entered.fillna(market)
    buys["現在単価"] = now
    buys["取得額"] = buys["数量(g)"] * buys["購入時単価"]
    buys["評価額"] = buys["数量(g)"] * now
    buys["損益"] = buys["評価額"] - buys["取得額"]
    buys["損益率(%)"] = buys["損益"] / buys["取得額"] * 100
    buys = buys.sort_values("購入日")

    # ---- トータル損益 ----
    qty, cost, value = buys["数量(g)"].sum(), buys["取得額"].sum(), buys["評価額"].sum()
    pl = value - cost
    st.html(hero_card(pl, pl / cost * 100, cost, value, qty, cost / qty, now))

    if pl > 0 and not st.session_state.get("celebrated"):
        st.balloons()
        st.session_state["celebrated"] = True

    # ---- 購入ごとの明細 ----
    st.subheader("購入ごとの損益")
    st.html("".join(buy_card(r, now) for _, r in buys.iterrows()))

    with st.expander("表で見る"):
        table = buys[["購入日", "数量(g)", "購入時単価", "現在単価", "取得額", "評価額", "損益", "損益率(%)"]].copy()
        table["購入日"] = table["購入日"].dt.date
        st.dataframe(
            table.style
            .format({"購入時単価": "{:,.0f}", "現在単価": "{:,.0f}", "取得額": "{:,.0f}",
                     "評価額": "{:,.0f}", "損益": "{:+,.0f}", "損益率(%)": "{:+.2f}"})
            .map(lambda v: f"color:{pl_color(v)};font-weight:700", subset=["損益", "損益率(%)"]),
            use_container_width=True, hide_index=True,
        )
else:
    st.info("購入履歴を入力すると損益が表示されます．")

# ---- 価格推移チャート ----
st.subheader("価格推移")
unit = st.radio("価格単位", ["円/g", "USD/oz"], horizontal=True)
period = st.radio("表示期間", ["1年", "5年", "10年", "全期間", "指定"], index=3, horizontal=True)

if period == "指定":
    start = st.date_input("表示開始日", value=first_date,
                          min_value=first_date, max_value=last_date)
elif period == "全期間":
    start = first_date
else:
    years = int(period.removesuffix("年"))
    start = (pd.Timestamp(last_date) - pd.DateOffset(years=years)).date()

col = "jpy_g" if unit == "円/g" else "usd_oz"
view = df[df.index >= pd.Timestamp(start)]

fig = go.Figure()
fig.add_trace(go.Scatter(x=view.index, y=view[col], name="金価格",
                         line=dict(width=1.8, color="#d4a017")))
if has_buys:
    fig.add_trace(go.Scatter(
        x=buys["購入日"], y=buys["購入日"].map(lambda d: df[col].asof(d)),
        mode="markers", name="購入",
        marker=dict(size=13, symbol="triangle-up",
                    color=[pl_color(v) for v in buys["損益"]],
                    line=dict(width=1, color="white")),
        text=[f"{q:g} g / 損益 {p:+,.0f} 円" for q, p in zip(buys["数量(g)"], buys["損益"])],
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<br>%{text}<extra></extra>",
    ))
fig.update_xaxes(rangeselector=dict(buttons=[
    dict(count=1, label="1y", step="year", stepmode="backward"),
    dict(count=5, label="5y", step="year", stepmode="backward"),
    dict(step="all", label="All"),
]))
fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
st.plotly_chart(fig, use_container_width=True)

# ---- 保有資産の推移 ----
if has_buys:
    st.subheader("取得額・評価額の推移（円）")
    s = buys.groupby("購入日")[["数量(g)", "取得額"]].sum()
    s = s.reindex(df.index.union(s.index), fill_value=0).cumsum().reindex(df.index)
    hist = pd.DataFrame({"取得額": s["取得額"], "評価額": s["数量(g)"] * df["jpy_g"]})
    hist = hist[hist.index >= max(pd.Timestamp(start), buys["購入日"].min())]

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=hist.index, y=hist["取得額"], name="取得額",
                              line=dict(shape="hv", color="#9ca3af", width=2)))
    fig2.add_trace(go.Scatter(x=hist.index, y=hist["評価額"], name="評価額",
                              line=dict(color="#d4a017", width=2),
                              fill="tonexty", fillcolor="rgba(212,160,23,0.25)"))
    fig2.update_layout(margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    st.plotly_chart(fig2, use_container_width=True)
