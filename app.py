# app.py
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from secrets import token_hex

import gspread
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf

TROY_OZ_G = 31.1034768  # 1トロイオンス = 31.1035 g
UP = "#16a34a"    # 利益の色（緑）
DOWN = "#dc2626"  # 損失の色（赤）
GOLD = "#d4a017"

# 日付入力欄そのものは広い範囲を許可し，範囲チェックはアプリ側で行う（標準の赤いエラーを出さないため）
DATE_INPUT_MIN = date(1900, 1, 1)
DATE_INPUT_MAX = date(2100, 12, 31)

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

# スプレッドシートのシートと列
RECORDS_SHEET = "records"
RECORDS_HEADER = ("user", "id", "date", "grams", "paid", "auto", "created_at")
USERS_SHEET = "users"
USERS_HEADER = ("id", "created_at", "moved_to")

# URLの ?u= に入るID（旧方式のランダムIDと，合言葉から作るハッシュIDの両方を許可）
USER_PATTERN = re.compile(r"[A-Za-z0-9_-]{6,32}")
HASH_ID_PATTERN = re.compile(r"h[0-9a-f]{24}")
WORD_MIN_LEN = 6
PBKDF2_ITERATIONS = 200_000

st.set_page_config(page_title="金の値段と損益", layout="wide")

# 1回の画面更新でポップアップを2つ同時に出さないためのフラグ（毎回の実行でリセットされる）
_dialog_shown = [False]


# ======================================================================
# 金の値段の取得
# ======================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def load_prices() -> pd.DataFrame:
    gold = yf.download("GC=F", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    fx = yf.download("JPY=X", start="2000-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
    df = pd.concat({"usd_oz": gold, "usdjpy": fx}, axis=1).sort_index().ffill().dropna()
    df["jpy_g"] = df["usd_oz"] * df["usdjpy"] / TROY_OZ_G
    return df


# ======================================================================
# 合言葉 → ID（ハッシュ化）
# ======================================================================
def normalize_word(w: str) -> str:
    """全角/半角，大文字/小文字，空白の違いを吸収する"""
    w = unicodedata.normalize("NFKC", w or "")
    return re.sub(r"\s+", "", w).lower()


def word_to_id(w: str) -> str:
    """合言葉を，Secretsのペッパーを使ってPBKDF2でハッシュ化し，URL用のIDにする"""
    pepper = st.secrets["auth"]["pepper"].encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", normalize_word(w).encode("utf-8"), pepper, PBKDF2_ITERATIONS)
    return "h" + dk.hex()[:24]


# ======================================================================
# スプレッドシート
# ======================================================================
@st.cache_resource(show_spinner=False)
def get_book() -> gspread.Spreadsheet:
    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    return gc.open_by_key(st.secrets["sheet"]["key"])


@st.cache_resource(show_spinner=False)
def get_ws(name: str, header: tuple) -> gspread.Worksheet:
    sh = get_book()
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=1000, cols=len(header))
    if ws.row_values(1) != list(header):
        ws.update(range_name="A1", values=[list(header)])
    return ws


def records_ws() -> gspread.Worksheet:
    return get_ws(RECORDS_SHEET, RECORDS_HEADER)


def users_ws() -> gspread.Worksheet:
    return get_ws(USERS_SHEET, USERS_HEADER)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- 利用者（合言葉ID）の管理 ----
def user_lookup(uid: str) -> dict | None:
    rows = users_ws().get_all_records(expected_headers=list(USERS_HEADER), numericise_ignore=["all"])
    for r in rows:
        if r["id"] == uid:
            return r
    return None


def user_register(uid: str) -> None:
    users_ws().append_row([uid, now_iso(), ""], value_input_option="RAW")


def user_resolve(uid: str) -> str:
    """合言葉を変えた古いIDなら，新しいIDをたどる（ホーム画面の古いアイコン対策）"""
    rows = users_ws().get_all_records(expected_headers=list(USERS_HEADER), numericise_ignore=["all"])
    moved = {r["id"]: r["moved_to"] for r in rows if r["moved_to"]}
    for _ in range(10):  # 念のため循環を防ぐ
        if uid not in moved:
            break
        uid = moved[uid]
    return uid


def user_rekey(old: str, new: str) -> None:
    """記録を新しいIDに付け替え，古いIDに移転先を記録する"""
    user_register(new)

    ws = records_ws()
    users_col = ws.col_values(1)
    updates = [{"range": f"A{i + 1}", "values": [[new]]}
               for i, u in enumerate(users_col) if i > 0 and u == old]
    if updates:
        ws.batch_update(updates, value_input_option="RAW")

    uws = users_ws()
    ids = uws.col_values(1)
    if old in ids:
        uws.update(range_name=f"C{ids.index(old) + 1}", values=[[new]])
    else:  # 旧方式のランダムIDはusersに未登録なので，移転先つきで登録する
        uws.append_row([old, now_iso(), new], value_input_option="RAW")


# ---- 記録の管理 ----
def db_load(user: str) -> list[dict]:
    rows = records_ws().get_all_records(expected_headers=list(RECORDS_HEADER), numericise_ignore=["all"])
    out = []
    for r in rows:
        if r["user"] != user:
            continue
        out.append({"id": r["id"], "date": r["date"], "grams": float(r["grams"]),
                    "paid": float(r["paid"]), "auto": r["auto"]})
    return out


def _to_row(user: str, rec: dict) -> list:
    return [user, rec["id"], rec["date"], rec["grams"], rec["paid"], rec["auto"], now_iso()]


def db_add(user: str, rec: dict) -> None:
    records_ws().append_row(_to_row(user, rec), value_input_option="RAW")


def db_delete(user: str, rid: str | None = None) -> None:
    """rid を指定するとその1件，指定しなければそのIDの記録をすべて削除"""
    ws = records_ws()
    users, ids = ws.col_values(1), ws.col_values(2)
    targets = [i + 1 for i, (u, r) in enumerate(zip(users, ids))
               if i > 0 and u == user and (rid is None or r == rid)]
    for row in sorted(targets, reverse=True):  # 下の行から消さないと行番号がずれる
        ws.delete_rows(row)


def db_replace(user: str, records: list[dict]) -> None:
    db_delete(user)
    if records:
        records_ws().append_rows([_to_row(user, r) for r in records], value_input_option="RAW")


# ======================================================================
# 共通の関数
# ======================================================================
def fmt_g(v: float) -> str:
    """g数を小数点以下2桁まで，不要な0を省いて表示"""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def fmt_date(d) -> str:
    return pd.Timestamp(d).strftime("%Y年%m月%d日")


def pl_color(v: float) -> str:
    return UP if v >= 0 else DOWN


def show_error(msg: str, e: Exception) -> None:
    """利用者向けのやわらかい案内＋管理者向けの詳細"""
    st.error(msg)
    with st.expander("くわしい内容（管理者向け）"):
        st.exception(e)


def set_db_error(msg: str, e: Exception) -> None:
    st.session_state["db_error"] = (msg, e)


def show_pending_messages() -> None:
    if err := st.session_state.pop("db_error", None):
        show_error(*err)
    if msg := st.session_state.pop("auth_msg", None):
        st.warning(msg)
    if msg := st.session_state.pop("word_dialog", None):
        if not _dialog_shown[0]:
            _dialog_shown[0] = True
            word_dialog(msg)


def market_price(df: pd.DataFrame, d) -> float:
    """指定日（休日やデータ未反映の日なら直前の営業日）の1gあたりの相場"""
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
    return {"id": "r" + token_hex(4), "date": pd.Timestamp(d).date().isoformat(),
            "grams": float(grams), "paid": float(paid), "auto": auto}


def amounts_entered(g, p) -> tuple[bool, bool]:
    return (g is not None and g > 0), (p is not None and p > 0)


def reset_date(key: str) -> None:
    st.session_state[key] = None


@st.dialog("📅 日付を選び直してください")
def date_dialog(key: str, msg: str) -> None:
    st.write(msg)
    if st.button("OK", type="primary", use_container_width=True, on_click=reset_date, args=(key,)):
        st.rerun()


@st.dialog("🔑 合言葉を入れ直してください")
def word_dialog(msg: str) -> None:
    st.write(msg)
    if st.button("OK", type="primary", use_container_width=True):
        st.rerun()


def check_date(d, key: str) -> bool:
    """日付が計算できる範囲内か確認．範囲外ならやわらかいポップアップで案内する"""
    if d is None:
        return False
    if d < first_date:
        msg = (f"金の値段のデータは **{fmt_date(first_date)}** からしかありません．\n\n"
               f"それより後の日付を選んでください．")
    elif d > today:
        msg = "まだ来ていない日付が選ばれています．\n\n今日か，それより前の日付を選んでください．"
    else:
        return True

    st.info(f"この日付（{fmt_date(d)}）では計算できません．日付を選び直してください．")
    warned_key = f"warned_{key}"
    if st.session_state.get(warned_key) != d and not _dialog_shown[0]:
        st.session_state[warned_key] = d
        _dialog_shown[0] = True
        date_dialog(key, msg)
    return False


def show_preview(df: pd.DataFrame, d, g, p, verb: str) -> None:
    """入力中に，相場と自動計算される値を表示"""
    has_g, has_p = amounts_entered(g, p)
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
    else:
        st.caption(f"この日の相場は1gあたり {price:,.0f} 円です．量か金額を入れてください．")


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
<b>{fmt_date(r['購入日'])} に {fmt_g(r['数量(g)'])} g{g_note}</b>
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
    b["購入日"] = pd.to_datetime(b["date"])
    b["数量(g)"] = b["grams"].astype(float)
    b["取得額"] = b["paid"].astype(float)
    b["購入時単価"] = b["取得額"] / b["数量(g)"]
    b["評価額"] = b["数量(g)"] * now
    b["損益"] = b["評価額"] - b["取得額"]
    b["損益率(%)"] = b["損益"] / b["取得額"] * 100
    return b.sort_values("購入日")


# ======================================================================
# 合言葉（URLの ?u=...）の決定
# ======================================================================
def get_user() -> str | None:
    """URLのIDを返す．合言葉を変えた古いIDなら新しいIDに置き換える．IDが無ければ None"""
    u = st.query_params.get("u")
    if not (u and USER_PATTERN.fullmatch(u)):
        return None
    if st.session_state.get("loaded_user") == u:  # 読み込み済みなら毎回シートを見に行かない
        return u
    resolved = user_resolve(u)
    if resolved != u:
        st.query_params["u"] = resolved
    return resolved


def enter(uid: str) -> None:
    st.query_params["u"] = uid
    st.session_state.pop("loaded_user", None)
    st.session_state["celebrated"] = False


def read_word(key: str) -> str | None:
    """入力された合言葉を確認し，短すぎるときはポップアップで案内して None を返す（保存しない）"""
    w = st.session_state.get(key, "")
    n = len(normalize_word(w))
    if n < WORD_MIN_LEN:
        st.session_state["word_dialog"] = (
            f"合言葉は **{WORD_MIN_LEN}文字以上** にしてください（今は{n}文字です）．\n\n"
            "空白は文字数に数えません．合言葉はまだ保存されていません．")
        return None
    return w


def open_word():
    """「開く」：登録済みの合言葉で記録をよび出す"""
    if (w := read_word("login_word")) is None:
        return
    uid = word_to_id(w)
    try:
        rec = user_lookup(uid)
    except Exception as e:
        set_db_error("合言葉を確認できませんでした．電波の良いところで，もう一度押してください．", e)
        return
    if rec is None:
        st.session_state["auth_msg"] = ("その合言葉は見つかりませんでした．入力をもう一度確かめてください．"
                                        "はじめて使う場合は「この合言葉ではじめる」を押してください．")
        return
    if rec["moved_to"]:
        st.session_state["auth_msg"] = "この合言葉は変更されています．新しい合言葉を入れてください．"
        return
    st.session_state["login_word"] = ""
    enter(uid)
    st.session_state["toast"] = "🔑 記録をよび出しました"


def start_word():
    """「この合言葉ではじめる」：新しい合言葉を登録する"""
    if (w := read_word("login_word")) is None:
        return
    uid = word_to_id(w)
    try:
        if user_lookup(uid) is not None:
            st.session_state["auth_msg"] = "その合言葉はすでに使われています．別の合言葉にしてください．"
            return
        user_register(uid)
    except Exception as e:
        set_db_error("合言葉を登録できませんでした．電波の良いところで，もう一度押してください．", e)
        return
    st.session_state["login_word"] = ""
    enter(uid)
    st.session_state["show_onboarding"] = True
    st.session_state["toast"] = "✅ 新しい合言葉ではじめました"


def login_screen() -> None:
    st.title("金の値段と損益")
    show_pending_messages()
    st.metric(f"今日の金の値段（1gあたり，{fmt_date(last_date)}時点）", f"{now:,.0f} 円")
    st.subheader("🔑 合言葉を入れてください")
    st.caption(f"合言葉は，記録をよび出すための鍵です（{WORD_MIN_LEN}文字以上）．"
               "全角・半角，大文字・小文字，空白の違いは区別しません．"
               "ほかの人に知られないものにしてください．")
    st.text_input("合言葉", key="login_word", type="password")
    c1, c2 = st.columns(2)
    c1.button("開く", type="primary", use_container_width=True, on_click=open_word)
    c2.button("この合言葉ではじめる", use_container_width=True, on_click=start_word)
    st.stop()


# ======================================================================
# データ読み込み・状態の初期化
# ======================================================================
with st.spinner("金の値段を読み込んでいます…（少し時間がかかることがあります）"):
    try:
        df = load_prices()
    except Exception as e:
        show_error("金の値段を読み込めませんでした．少し時間をおいてから，ページを開き直してください．", e)
        st.stop()

first_date = df.index.min().date()
last_date = df.index.max().date()
today = pd.Timestamp.now(tz="Asia/Tokyo").date()  # サーバーはUTCなので日本時間で判定
now = float(df["jpy_g"].iloc[-1])

try:
    user = get_user()
except Exception as e:
    show_error("合言葉を確認できませんでした．電波の良いところで，ページを開き直してください．", e)
    st.stop()
if user is None:
    login_screen()

if st.session_state.get("loaded_user") != user:
    with st.spinner("記録を読み込んでいます…"):
        try:
            st.session_state["buys"] = db_load(user)
            st.session_state["loaded_user"] = user
        except Exception as e:
            show_error("記録を読み込めませんでした．電波の良いところで，ページを開き直してください．", e)
            st.stop()

st.session_state.setdefault("confirm_clear", False)
st.session_state.setdefault("sim_nonce", 0)


# ======================================================================
# ボタンの処理（コールバック）
# ======================================================================
def add_record():
    """「追加する」：保存できたら記録に加えて入力欄を初期状態に戻す"""
    rec = make_record(df, st.session_state["in_date"],
                      st.session_state["in_grams"], st.session_state["in_paid"])
    try:
        db_add(user, rec)
    except Exception as e:
        set_db_error("記録を保存できませんでした．電波の良いところで，もう一度「追加する」を押してください．", e)
        return
    st.session_state["buys"].append(rec)
    st.session_state["in_date"] = None
    st.session_state["in_grams"] = 0.0
    st.session_state["in_paid"] = 0.0
    st.session_state["toast"] = "✅ 記録を追加しました"


def delete_one(rid: str):
    try:
        db_delete(user, rid)
    except Exception as e:
        set_db_error("記録を消せませんでした．電波の良いところで，もう一度押してください．", e)
        return
    st.session_state["buys"] = [r for r in st.session_state["buys"] if r["id"] != rid]
    st.session_state["toast"] = "🗑 記録を1件消しました"


def clear_all():
    """「はい，全部消す」"""
    try:
        db_delete(user)
    except Exception as e:
        set_db_error("記録を消せませんでした．電波の良いところで，もう一度押してください．", e)
        return
    st.session_state["buys"] = []
    st.session_state["confirm_clear"] = False
    st.session_state["celebrated"] = False
    st.session_state.pop("loaded_file", None)
    st.session_state["toast"] = "🗑 記録を全部消しました"


def run_sim():
    """「計算してみる」：結果を保存し，演出を再生させるために番号を進める"""
    st.session_state["sim"] = make_record(
        df, st.session_state["sim_date"], st.session_state["sim_grams"], st.session_state["sim_paid"])
    st.session_state["sim_nonce"] += 1


def change_word():
    """合言葉を変える（旧方式のIDから合言葉に切り替えるときも使う）"""
    if (w := read_word("new_word")) is None:
        return
    new = word_to_id(w)
    if new == user:
        st.session_state["auth_msg"] = "今の合言葉と同じです．"
        return
    try:
        if user_lookup(new) is not None:
            st.session_state["auth_msg"] = "その合言葉はすでに使われています．別の合言葉にしてください．"
            return
        user_rekey(user, new)
    except Exception as e:
        set_db_error("合言葉を変えられませんでした．電波の良いところで，もう一度押してください．", e)
        return
    st.session_state["new_word"] = ""
    st.query_params["u"] = new
    st.session_state["loaded_user"] = new  # 記録の中身は同じなので読み込み直さない
    st.session_state["toast"] = "🔑 合言葉を変えました"


def logout():
    """合言葉の入力画面にもどる"""
    st.query_params.pop("u", None)
    for k in ("loaded_user", "buys", "sim", "confirm_clear"):
        st.session_state.pop(k, None)
    st.session_state["celebrated"] = False


# ======================================================================
# 画面
# ======================================================================
if msg := st.session_state.pop("toast", None):
    st.toast(msg)

st.title("金の値段と損益")

if st.session_state.get("show_onboarding"):
    st.success("📱 はじめての方へ：この画面のまま「ホーム画面に追加」をしてください．"
               "次からはそのアイコンから開くと，入れた記録がそのまま表示されます．")
    if st.button("わかった", use_container_width=True):
        st.session_state["show_onboarding"] = False
        st.rerun()

show_pending_messages()

if not HASH_ID_PATTERN.fullmatch(user):  # 旧方式のランダムID
    st.info("🔑 この記録にはまだ合言葉がありません．下の「合言葉」から決めておくと，"
            "別のスマホからでも記録をよび出せます．")

st.metric(f"今日の金の値段（1gあたり，{fmt_date(last_date)}時点）", f"{now:,.0f} 円")

tab_mine, tab_sim, tab_chart = st.tabs(["📒 わたしの金", "🔮 もしも計算", "📈 値段の移り変わり"])

# ----------------------------------------------------------------------
# タブ1：わたしの金
# ----------------------------------------------------------------------
with tab_mine:
    st.subheader("買った金を記録する")
    st.caption("「買った日」と，「買った量」か「支払った金額」のどちらか一方を入れてください．"
               "もう一方はその日の相場から計算します．")

    d = st.date_input("買った日", value=None, min_value=DATE_INPUT_MIN, max_value=DATE_INPUT_MAX,
                      format="YYYY/MM/DD", key="in_date")
    d_ok = check_date(d, "in_date")
    g = st.number_input("買った量（g）", min_value=0.0, step=1.0, value=0.0, key="in_grams")
    p = st.number_input("支払った金額（円・合計）", min_value=0.0, step=1000.0, value=0.0, key="in_paid")

    has_g, has_p = amounts_entered(g, p)
    if d_ok:
        show_preview(df, d, g, p, "記録します")
    st.button("追加する", type="primary", use_container_width=True,
              disabled=not (d_ok and (has_g or has_p)), on_click=add_record)

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
            st.button("🗑 この記録を消す", key=f"del_{r['id']}", on_click=delete_one, args=(r["id"],))

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

    # ---- 合言葉 ----
    with st.expander("🔑 合言葉（記録をよび出す鍵）"):
        st.caption("合言葉を変えると，今までの記録は新しい合言葉に引き継がれます．"
                   "ホーム画面のアイコンはそのまま使えます．")
        st.text_input("新しい合言葉", key="new_word", type="password")
        st.button("合言葉を変える", use_container_width=True, on_click=change_word)
        st.divider()
        st.caption("別の合言葉の記録を見たいときは，いったん合言葉の入力画面にもどってください．")
        st.button("合言葉の入力画面にもどる", use_container_width=True, on_click=logout)

    # ---- 記録の保存・読み込み（予備） ----
    with st.expander("記録のファイル保存・読み込み（予備）"):
        st.caption("記録は自動で保存されています．念のため手元にも残したいときに使ってください．"
                   "ファイルを読み込むと，今の記録はファイルの内容に置き換わります．")
        if has_buys:
            out = pd.DataFrame(records)[["date", "grams", "paid", "auto"]].rename(columns={
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
            new_records, skipped = [], 0
            for i, (dt, gr, pd_) in enumerate(zip(csv["購入日"], grams, paid)):
                if not (first_date <= pd.Timestamp(dt).date() <= today):
                    skipped += 1
                    continue
                rec = make_record(df, dt, gr, pd_)
                if "自動計算" in csv.columns and isinstance(csv["自動計算"].iloc[i], str):
                    rec["auto"] = csv["自動計算"].iloc[i]
                new_records.append(rec)
            st.session_state["loaded_file"] = (uploaded.name, uploaded.size)
            try:
                db_replace(user, new_records)
            except Exception as e:
                set_db_error("ファイルの内容を保存できませんでした．電波の良いところで，もう一度読み込んでください．", e)
                st.session_state.pop("loaded_file", None)
                st.rerun()
            st.session_state["buys"] = new_records
            st.session_state["toast"] = (f"📂 {len(new_records)}件の記録を読み込みました"
                                         + (f"（計算できない日付の{skipped}件は飛ばしました）" if skipped else ""))
            st.rerun()

# ----------------------------------------------------------------------
# タブ2：もしも計算
# ----------------------------------------------------------------------
with tab_sim:
    st.subheader("🔮 もしこの日にこれだけ買っていたら？")
    st.caption("日付と，「量」か「金額」のどちらか一方を入れて「計算してみる」を押してください．"
               "ここで計算した内容は記録には残りません．")

    sd = st.date_input("買ったことにする日", value=None, min_value=DATE_INPUT_MIN,
                       max_value=DATE_INPUT_MAX, format="YYYY/MM/DD", key="sim_date")
    sd_ok = check_date(sd, "sim_date")
    sg = st.number_input("量（g）", min_value=0.0, step=1.0, value=0.0, key="sim_grams")
    sp = st.number_input("金額（円）", min_value=0.0, step=10000.0, value=0.0, key="sim_paid")

    s_has_g, s_has_p = amounts_entered(sg, sp)
    if sd_ok:
        show_preview(df, sd, sg, sp, "計算します")
    st.button("✨ 計算してみる", type="primary", use_container_width=True,
              disabled=not (sd_ok and (s_has_g or s_has_p)), on_click=run_sim)

    sim = st.session_state.get("sim")
    if sim is not None:
        sim_value = sim["grams"] * now
        sim_cost = sim["paid"]
        nonce = st.session_state["sim_nonce"]
        date_str = fmt_date(sim["date"])

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
