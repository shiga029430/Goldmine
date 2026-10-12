# app.py
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
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


# ======================================================================
# 共通の関数
# ======================================================================
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


def input_ready(d, g, p) -> tuple[bool, bool, bool]:
    has_g = g is not None and g > 0
    has_p = p is not None and p > 0
    return d is not None and (has_g or has_p), has_g, has_p


def show_preview(df: pd.DataFrame, d, g, p, verb: str) -> None:
    """入力中に，相場と自動計算される値を表示"""
    if d is None:
        return
    _, has_g, has_p = input_ready(d, g, p)
    price = market_price(df, d)
    if has_g and has_p:
        st.info(f"入力どおり {fmt_g(g)} g を {p:,.0f} 円（1gあたり {p / g:,.0f} 円）で{verb}．"
                f"（この日の相場は1gあたり {price:,.0f} 円）")
    elif has_g:
        st.info(f"この日の相場は1gあたり {price:,.0f} 円です．"
                f"→ 金額は 約 {g * price:,.0f} 円 として{verb}．")
    elif has_p:
        st.info(f"この日の相場は1gあたり {price:,.0f} 円です．"
                f"→ 量は 約 {fmt_g(p / price)} g として{verb}．")


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


def value_chart(df: pd.DataFrame, start: pd.Timestamp, grams_cum: pd.Series,
                cost_cum: pd.Series) -> go.Figure:
    """買った金額（階段状）と今の価値の推移グラフ"""
    hist = pd.DataFrame({"取得額": cost_cum, "評価額": grams_cum * df["jpy_g"]})
    hist = hist[hist.index >= start]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist.index, y=hist["取得額"], name="買った金額",
                             line=dict(shape="hv", color="#9ca3af", width=2),
                             hovertemplate="買った金額 %{y:,.0f} 円<extra></extra>"))
    fig.add_trace(go.Scatter(x=hist.index, y=hist["評価額"], name="その時の価値",
                             line=dict(color=GOLD, width=2),
                             fill="tonexty", fillcolor="rgba(212,160,23,0.25)",
                             hovertemplate="価値 %{y:,.0f} 円<extra></extra>"))
    return style_chart(fig)


# ======================================================================
# 表示用HTML
# ======================================================================
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


def sim_effect_html(date_str: str, grams: float, cost: float, value: float, nonce: int) -> str:
    """もしも計算の演出：カウントアップ → 結果がポンと出る → 絵文字が降る"""
    pl = value - cost
    ratio = value / cost
    if pl >= 0:
        bg = "linear-gradient(135deg,#a16207,#eab308 55%,#fde047)"
        emojis = ["💰", "🪙", "✨", "💴", "🎉", "👑"]
        if ratio >= 3:
            msg = f"🎊 大当たり！ なんと {ratio:.1f} 倍 🎊"
        elif ratio >= 2:
            msg = f"🎉 すごい！ {ratio:.1f} 倍に増えました"
        else:
            msg = f"😊 {ratio:.2f} 倍に増えました"
        result = f"{pl:+,.0f} 円のもうけ"
        drops = 45 if ratio >= 2 else 25
    else:
        bg = "linear-gradient(135deg,#1e3a8a,#3b82f6)"
        emojis = ["💧", "😢", "🍂", "☔"]
        msg = f"😢 {ratio:.2f} 倍に減ってしまいました"
        result = f"{pl:,.0f} 円の損"
        drops = 20
    return f"""<!-- run {nonce} -->
<style>
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif}}
.card{{position:relative;overflow:hidden;border-radius:18px;padding:22px 20px;color:#fff;
background:{bg};height:300px;box-sizing:border-box;box-shadow:0 6px 18px rgba(0,0,0,.25)}}
.q{{font-size:1.05rem;line-height:1.6;text-shadow:0 1px 3px rgba(0,0,0,.3)}}
.label{{margin-top:14px;font-size:1rem;opacity:.95}}
.num{{font-size:2.7rem;font-weight:800;line-height:1.2;text-shadow:0 2px 6px rgba(0,0,0,.3)}}
.res{{margin-top:10px;opacity:0;transform:scale(.4);
transition:all .6s cubic-bezier(.2,1.8,.4,1);text-shadow:0 2px 6px rgba(0,0,0,.35)}}
.res.show{{opacity:1;transform:scale(1)}}
.res .a{{font-size:1.7rem;font-weight:800}}
.res .b{{font-size:1.15rem;font-weight:700;margin-top:4px}}
.drop{{position:absolute;top:-50px;font-size:30px;animation:fall linear forwards;pointer-events:none}}
@keyframes fall{{to{{transform:translateY(380px) rotate(540deg);opacity:.15}}}}
</style>
<div class="card" id="card">
<div class="q">もし <b>{date_str}</b> に<br><b>{fmt_g(grams)} g</b>（{cost:,.0f} 円分）の金を買っていたら…</div>
<div class="label">今の価値は</div>
<div class="num"><span id="n">{cost:,.0f}</span> 円</div>
<div class="res" id="res"><div class="a">{result}</div><div class="b">{msg}</div></div>
</div>
<script>
const from = {cost}, to = {value}, dur = 2800;
const el = document.getElementById("n");
const emojis = {json.dumps(emojis, ensure_ascii=False)};
let t0 = null;
function step(t) {{
  if (t0 === null) t0 = t;
  const k = Math.min((t - t0) / dur, 1);
  const e = 1 - Math.pow(1 - k, 3);  // だんだんゆっくりになる
  el.textContent = Math.round(from + (to - from) * e).toLocaleString("ja-JP");
  if (k < 1) {{ requestAnimationFrame(step); }}
  else {{ document.getElementById("res").classList.add("show"); rain(); }}
}}
function rain() {{
  const card = document.getElementById("card");
  for (let i = 0; i < {drops}; i++) {{
    const s = document.createElement("span");
    s.className = "drop";
    s.textContent = emojis[Math.floor(Math.random() * emojis.length)];
    s.style.left = (Math.random() * 95) + "%";
    s.style.animationDuration = (1.6 + Math.random() * 1.6) + "s";
    s.style.animationDelay = (Math.random() * 1.0) + "s";
    card.appendChild(s);
  }}
}}
setTimeout(() => requestAnimationFrame(step), 400);
</script>"""


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


# ======================================================================
# データ読み込み・状態の初期化
# ======================================================================
df = load_prices()
first_date = df.index.min().date()
last_date = df.index.max().date()
now = float(df["jpy_g"].iloc[-1])

st.session_state.setdefault("buys", [])
st.session_state.setdefault("confirm_clear", False)
st.session_state.setdefault("sim_nonce", 0)


# ======================================================================
# ボタンの処理（コールバック）
# ======================================================================
def add_record():
    """「追加する」：記録を追加して入力欄を空に戻す"""
    st.session_state["buys"].append(make_record(
        df, st.session_state["in_date"], st.session_state["in_grams"], st.session_state["in_paid"]))
    for k in ("in_date", "in_grams", "in_paid"):
        st.session_state[k] = None
    st.session_state["added_msg"] = True


def clear_all():
    """「はい，全部消す」"""
    st.session_state["buys"] = []
    st.session_state["confirm_clear"] = False
    st.session_state["celebrated"] = False
    st.session_state.pop("loaded_file", None)
    st.session_state["cleared_msg"] = True


def run_sim():
    """「計算してみる」：結果を保存し，演出を再生させるために番号を進める"""
    st.session_state["sim"] = make_record(
        df, st.session_state["sim_date"], st.session_state["sim_grams"], st.session_state["sim_paid"])
    st.session_state["sim_nonce"] += 1


# ======================================================================
# 画面
# ======================================================================
st.title("金の値段と損益")
st.metric(f"今日の金の値段（1gあたり，{last_date.strftime('%Y年%m月%d日')}時点）", f"{now:,.0f} 円")

tab_mine, tab_sim, tab_chart = st.tabs(["📒 わたしの金", "🔮 もしも計算", "📈 値段の移り変わり"])

# ----------------------------------------------------------------------
# タブ1：わたしの金
# ----------------------------------------------------------------------
with tab_mine:
    st.subheader("買った金を記録する")
    st.caption("「買った日」と，「買った量」か「支払った金額」のどちらか一方を入れてください．"
               "もう一方はその日の相場から計算します．")

    d = st.date_input("買った日", value=None, min_value=first_date, max_value=last_date,
                      format="YYYY/MM/DD", key="in_date")
    g = st.number_input("買った量（g）", min_value=0.0, step=1.0, value=None,
                        placeholder="例：10", key="in_grams")
    p = st.number_input("支払った金額（円・合計）", min_value=0.0, step=1000.0, value=None,
                        placeholder="例：120000", key="in_paid")
    show_preview(df, d, g, p, "記録します")
    ready, _, _ = input_ready(d, g, p)
    st.button("追加する", type="primary", use_container_width=True,
              disabled=not ready, on_click=add_record)

    if st.session_state.pop("added_msg", False):
        st.success("記録を追加しました．")
    if st.session_state.pop("cleared_msg", False):
        st.success("記録を全部消しました．")

    records = st.session_state["buys"]
    has_buys = len(records) > 0
    buys = records_to_df(records, now) if has_buys else None

    if has_buys:
        # ---- トータル損益 ----
        qty, cost, value = buys["数量(g)"].sum(), buys["取得額"].sum(), buys["評価額"].sum()
        pl = value - cost
        st.html(hero_card(pl, pl / cost * 100, cost, value, qty))

        if pl > 0 and not st.session_state.get("celebrated"):
            st.balloons()
            st.session_state["celebrated"] = True

        # ---- 買った分ごとの損益 ----
        st.subheader("買った分ごとの損益")
        for _, r in buys.iterrows():
            st.html(buy_card(r, now))
            if st.button("🗑 この記録を消す", key=f"del_{r['idx']}"):
                st.session_state["buys"].pop(int(r["idx"]))
                st.rerun()

        # ---- 全部消す（確認つき） ----
        st.divider()
        if not st.session_state["confirm_clear"]:
            if st.button("🗑 全部の記録を消す", use_container_width=True):
                st.session_state["confirm_clear"] = True
                st.rerun()
        else:
            st.warning("本当に全部の記録を消しますか？ 消した記録は元に戻せません．"
                       "残しておきたい場合は，先に下の「記録をファイルに保存」を押してください．")
            c1, c2 = st.columns(2)
            c1.button("はい，全部消す", type="primary", use_container_width=True, on_click=clear_all)
            if c2.button("やめる", use_container_width=True):
                st.session_state["confirm_clear"] = False
                st.rerun()

        # ---- 保有資産の推移 ----
        st.subheader("買った金額と今の価値の移り変わり")
        s = buys.groupby("購入日")[["数量(g)", "取得額"]].sum()
        s = s.reindex(df.index.union(s.index), fill_value=0).cumsum().reindex(df.index)
        st.plotly_chart(value_chart(df, buys["購入日"].min(), s["数量(g)"], s["取得額"]),
                        use_container_width=True, config=CHART_CONFIG)
    else:
        st.info("上の欄に入力して「追加する」を押すと，もうけ・損が表示されます．")

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
                if "自動計算" in csv.columns and isinstance(csv["自動計算"].iloc[i], str):
                    rec["auto"] = csv["自動計算"].iloc[i]
                new_records.append(rec)
            st.session_state["buys"] = new_records
            st.session_state["loaded_file"] = (uploaded.name, uploaded.size)
            st.rerun()

# ----------------------------------------------------------------------
# タブ2：もしも計算
# ----------------------------------------------------------------------
with tab_sim:
    st.subheader("🔮 もしこの日にこれだけ買っていたら？")
    st.caption("日付と，「量」か「金額」のどちらか一方を入れて「計算してみる」を押してください．"
               "ここで計算した内容は記録には残りません．")

    sd = st.date_input("買ったことにする日", value=None, min_value=first_date, max_value=last_date,
                       format="YYYY/MM/DD", key="sim_date")
    sg = st.number_input("量（g）", min_value=0.0, step=1.0, value=None,
                         placeholder="例：100", key="sim_grams")
    sp = st.number_input("金額（円）", min_value=0.0, step=10000.0, value=None,
                         placeholder="例：1000000", key="sim_paid")
    show_preview(df, sd, sg, sp, "計算します")
    sim_ready, _, _ = input_ready(sd, sg, sp)
    st.button("✨ 計算してみる", type="primary", use_container_width=True,
              disabled=not sim_ready, on_click=run_sim)

    sim = st.session_state.get("sim")
    if sim is not None:
        sim_value = sim["grams"] * now
        sim_cost = sim["paid"]
        nonce = st.session_state["sim_nonce"]
        date_str = pd.Timestamp(sim["date"]).strftime("%Y年%m月%d日")

        components.html(sim_effect_html(date_str, sim["grams"], sim_cost, sim_value, nonce), height=310)

        # 新しく計算したときだけ，結果に合わせて画面全体の演出も出す
        if st.session_state.get("sim_fired") != nonce:
            st.session_state["sim_fired"] = nonce
            if sim_value / sim_cost >= 2:
                st.balloons()
            elif sim_value < sim_cost:
                st.snow()

        st.markdown(f"**その後の価値の移り変わり**（{date_str} 〜 今日）")
        sim_start = pd.Timestamp(sim["date"])
        grams_cum = pd.Series(sim["grams"], index=df.index).where(df.index >= sim_start, 0)
        cost_cum = pd.Series(sim_cost, index=df.index).where(df.index >= sim_start, 0)
        st.plotly_chart(value_chart(df, sim_start, grams_cum, cost_cum),
                        use_container_width=True, config=CHART_CONFIG)

# ----------------------------------------------------------------------
# タブ3：値段の移り変わり
# ----------------------------------------------------------------------
with tab_chart:
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
