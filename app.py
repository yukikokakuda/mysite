# -*- coding: utf-8 -*-
"""
LPジェネレーター（教材版・統一版）
- 依存: pip install -U openai streamlit python-dotenv
- 使い方:
    1) .env に OPENAI_API_KEY を保存（例: OPENAI_API_KEY=sk-xxxx）
    2) streamlit run app.py
"""

from __future__ import annotations
import os, io, re, json, base64, zipfile, random
from typing import Dict, List, Tuple

import streamlit as st
import streamlit.components.v1 as components

from openai import OpenAI
from openai import RateLimitError, APIStatusError

# --- .env 読み込み（無ければ何もしない）---
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ─────────────────────────────────────────────
# セクション1: APIキー取得（環境変数優先 / secrets は任意）
# ─────────────────────────────────────────────
def get_api_key(env_key: str = "OPENAI_API_KEY") -> str | None:
    key = os.getenv(env_key)
    if key:
        return key
    try:
        return st.secrets[env_key]  # secrets.toml が無い環境でも例外安全に
    except Exception:
        return None

API_KEY = get_api_key()
if not API_KEY:
    st.error(
        "OpenAI APIキーが見つかりません。\n\n"
        "■ 推奨（ローカル学習向け）\n"
        "  1) .env を作成し OPENAI_API_KEY=sk-xxxx を記載\n"
        "  2) このアプリを再実行\n\n"
        "■ 参考（secrets を使う場合）\n"
        "  .streamlit/secrets.toml に OPENAI_API_KEY を記載（※リポジトリにコミットしない）\n"
        "  公式: st.secrets / secrets.toml の使い方はドキュメント参照"
    )
    st.stop()

client = OpenAI(api_key=API_KEY)


# ─────────────────────────────────────────────
# セクション2: ページ設定 / サイドバー
# ─────────────────────────────────────────────
st.set_page_config(page_title="LPジェネレーター（AI + Editor）", page_icon="✨", layout="wide")
st.title("✨ LPジェネレーター（AI生成 + Editor）")
st.caption("“毎回ちがうUI”をAIが生成 → 右ペインで色/文字/画像を編集 → AI差分編集 → ZIP出力")

with st.sidebar:
    st.header("基本情報")
    site_title = st.text_input("サイトタイトル / 名前", "Yamada Studio")
    tagline    = st.text_input("キャッチコピー", "Design that ships.")
    meta_desc  = st.text_input("meta description", "小さく速く出して、継続的に磨くためのデザインと実装。")
    email      = st.text_input("連絡先メール", "hello@example.com")

    st.divider()
    st.header("世界観（テーマ）")
    theme = st.selectbox(
        "Style Theme（生成AIに渡します）",
        [
            "シンプル","ビジネス","可愛い","スタイリッシュ","メルヘン","アメコミ風",
            "和風","和モダン","ミニマル","未来的（サイバー風）","レトロポップ",
            "エレガント","ナチュラル","ダークモード","雑誌風","クール",
        ],
        index=0
    )

    st.divider()
    st.header("コンテンツ")
    about_text   = st.text_area("About本文", height=90, value="大阪を拠点に、スピードと品質を両立するデザイン＆実装を提供しています。小さく出して、早く学び、継続的に磨く。")
    features_csv = st.text_input("特徴（カンマ区切り）", "高速検証, 明快なUI, スケール設計, 運用しやすい, 品質とスピード, データドリブン")
    works_csv    = st.text_input("実績（カンマ区切り）", "SaaSダッシュボード, EC特集, 採用サイト")
    testi_raw    = st.text_area(
        "お客様の声（名前|肩書き|コメント を改行）",
        height=80,
        value="佐藤 花子|PM|意思決定が圧倒的に速くなりました。\n鈴木 次郎|BizDev|初速から質まで、バランスが素晴らしい。"
    )

    st.divider()
    st.header("AI 生成設定")
    temperature = st.slider("多様性（temperature）", 0.2, 1.4, 1.0, 0.1)


# ─────────────────────────────────────────────
# セクション3: ユーティリティ
# ─────────────────────────────────────────────
def split_csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]

def parse_testimonials(s: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for line in s.splitlines():
        p = [u.strip() for u in line.split("|")]
        if len(p) >= 3:
            rows.append({"name": p[0], "role": p[1], "text": "|".join(p[2:])})
    return rows

def sanitize_html(html: str) -> str:
    html = re.sub(r"(?is)<script.*?>.*?</script>", "", html)
    html = re.sub(r'(?is)\son\w+\s*=\s*(["\']).*?\1', "", html)  # onClick 等
    return html


# ─────────────────────────────────────────────
# セクション4: 生成プロンプト & OpenAI 呼び出し
# ─────────────────────────────────────────────
def build_generate_prompt(theme: str, site_title: str, tagline: str, meta_desc: str,
                          email: str, about: str, feats: list, works: list, testi: list, seed: int) -> str:
    return f"""
あなたはLPデザイナー兼フロントエンドです。以下の要件で “完成HTML/CSS” をゼロから生成してください。

# ゴール
- 「{theme}」の世界観で、**初見で違いが分かる**LPを作る
- **HTMLの構造**・**装飾**・**タイポ**・**余白**・**モーション**まで変える
- 外部CDN/JSは使わない（純HTML+CSS）。画像はダミー矩形でOK。

# 入力データ
- title: {site_title}
- tagline: {tagline}
- meta_description: {meta_desc}
- email: {email}
- about: {about}
- features: {feats}
- works: {works}
- testimonials: {testi}
- style_seed: {seed}

# 厳格仕様
- 出力は JSON 1個のみ。スキーマ:
  {{
    "title": "string",
    "meta": {{"description":"string"}},
    "css": "string",
    "body_html": "string"
  }}

# デザイン指針
- 同じ {theme} でも毎回**配置・形・装飾**が異なること
- セクション数/順序/グリッド/装飾を**毎回変える**
- :root で色/角丸/影/線/背景のトークンを定義
- 画像は <div aria-label="image" class="img img--X"> のダミー
- mailto: のCTAを1つ以上

# 禁止
- <script>、外部URL、@import、実ファイル画像の読み込み
"""

def ai_generate(theme: str, site_title: str, tagline: str, meta_desc: str, email: str,
                about: str, feats: list, works: list, testi: list, temperature: float) -> Tuple[str, str]:
    if client is None:
        raise RuntimeError("OpenAI クライアントが初期化されていません。APIキー設定を確認してください。")
    seed = random.randint(1, 10_000_000)
    prompt = build_generate_prompt(theme, site_title, tagline, meta_desc, email, about, feats, works, testi, seed)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    data = json.loads(resp.choices[0].message.content)
    css  = data["css"]
    body = sanitize_html(data["body_html"])
    return css, body


# ─────────────────────────────────────────────
# セクション5: CSSトークン / 画像 / テキスト編集ユーティリティ
# ─────────────────────────────────────────────
VAR_RE = re.compile(r":root\s*\{([^}]*)\}", re.S)
IMG_PLACEHOLDER_RE = re.compile(
    r'(<div[^>]*?(aria-label\s*=\s*"image"[^>]*|class\s*=\s*"[^"]*img[^"]*"[^>]*)>)(.*?)</div>',
    re.I | re.S
)

def extract_root_vars(css_text: str) -> Dict[str, str]:
    m = VAR_RE.search(css_text)
    if not m: return {}
    vars_: Dict[str, str] = {}
    for line in m.group(1).split(";"):
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip().startswith("--") and v.strip():
                vars_[k.strip()] = v.strip()
    return vars_

def replace_root_vars(css_text: str, new_vars: Dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        block = match.group(1)
        pairs, seen = [], set()
        for ln in block.split(";"):
            if ":" in ln:
                k, v = ln.split(":", 1)
                key = k.strip()
                if key in new_vars:
                    pairs.append(f"{key}: {new_vars[key]}"); seen.add(key)
                else:
                    pairs.append(f"{key}:{v}")
        for k, v in new_vars.items():
            if k not in seen: pairs.append(f"{k}: {v}")
        return ":root{" + ";".join(pairs) + "}"
    if not VAR_RE.search(css_text):
        head = ":root{" + ";".join([f"{k}:{v}" for k, v in new_vars.items()]) + "}"
        return head + css_text
    return VAR_RE.sub(repl, css_text, count=1)

def find_image_placeholders(html: str) -> List[Tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in IMG_PLACEHOLDER_RE.finditer(html)]

def data_uri_from_file(file) -> Tuple[str, str]:
    data = file.read()
    mime = file.type or "application/octet-stream"
    ext = "png"
    if "jpeg" in mime: ext = "jpg"
    elif "webp" in mime: ext = "webp"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}", ext

def replace_placeholder_with_img(html: str, placeholder_html: str, data_uri: str) -> str:
    cls = ""
    m = re.search(r'class\s*=\s*"([^"]+)"', placeholder_html, re.I)
    if m: cls = m.group(1)
    alt = "image"
    m2 = re.search(r'aria-label\s*=\s*"([^"]+)"', placeholder_html, re.I)
    if m2: alt = m2.group(1)
    img = f'<img src="{data_uri}" alt="{alt}" class="{cls}"/>'
    return html.replace(placeholder_html, img, 1)

def extract_first_h1(html: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    return (m.group(1).strip() if m else "")

def replace_first_h1(html: str, new_text: str) -> str:
    return re.sub(r"(<h1[^>]*>)(.*?)(</h1>)", r"\1" + re.escape(new_text) + r"\3",
                  html, count=1, flags=re.S | re.I)

def extract_subtext(html: str) -> str:
    m = re.search(r"<(p|div)\s+class=[\'\"](sub|lead)[\'\"][^>]*>(.*?)</\1>", html, re.S | re.I)
    return (m.group(3).strip() if m else "")

def replace_subtext(html: str, new_text: str) -> str:
    return re.sub(r"(<(p|div)\s+class=[\'\"](sub|lead)[\'\"][^>]*>)(.*?)(</\2>)",
                  r"\1" + re.escape(new_text) + r"\5",
                  html, count=1, flags=re.S | re.I)


# ─────────────────────────────────────────────
# セクション6: 生成ボタン & セッション状態
# ─────────────────────────────────────────────
if "gen_html" not in st.session_state: st.session_state.gen_html = ""
if "gen_css"  not in st.session_state: st.session_state.gen_css  = ""
if "img_slots" not in st.session_state: st.session_state.img_slots = []

gen_clicked = st.button("🎯 LPを生成（AI）", type="primary")

if gen_clicked:
    try:
        css, body = ai_generate(
            theme, site_title, tagline, meta_desc, email,
            about_text, split_csv(features_csv), split_csv(works_csv), parse_testimonials(testi_raw),
            temperature
        )
    except RateLimitError as e:
        st.error("レート上限/クォータに達しました。"); st.exception(e)
    except APIStatusError as e:
        st.error(f"APIステータスエラー: {e.status_code}"); st.exception(e)
    except Exception as e:
        st.error("AI生成に失敗しました（インストール/キー/モデル権限をご確認ください）。"); st.exception(e)
    else:
        html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{site_title} – Landing</title>
  <meta name="description" content="{meta_desc}">
  <link rel="stylesheet" href="./styles.css" />
</head>
<body>
{body}
</body>
</html>"""
        st.session_state.gen_html  = html
        st.session_state.gen_css   = css
        st.session_state.img_slots = find_image_placeholders(body)


# ─────────────────────────────────────────────
# セクション7: プレビュー & エディタ
# ─────────────────────────────────────────────
if st.session_state.gen_html and st.session_state.gen_css:
    colA, colB = st.columns([7, 5])

    # プレビュー
    with colA:
        st.subheader("🔎 プレビュー")
        body_only = st.session_state.gen_html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
        inline = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<style>{st.session_state.gen_css}</style></head><body>{body_only}</body></html>"""
        components.html(inline, height=960, scrolling=True)

    # エディタ
    with colB:
        st.subheader("🛠 エディタ")

        # :root 変数編集（色は #RGB / #RRGGBB の時のみ color_picker）
        vars_now = extract_root_vars(st.session_state.gen_css)
        color_keys  = [k for k in vars_now if any(x in vars_now[k] for x in ["#", "rgb", "hsl"])]
        radius_keys = [k for k in vars_now if "radius" in k or k == "--r"]

        with st.expander("🎨 カラー & トークン編集", expanded=True):
            new_vars: Dict[str, str] = {}
            for k in color_keys:
                v = vars_now[k].strip()
                new_vars[k] = st.color_picker(k, v) if v.startswith("#") and len(v) in (4, 7) else st.text_input(k, v)
            for k in radius_keys:
                m = re.search(r"(\d+)", vars_now[k]); init = int(m.group(1)) if m else 12
                px = st.slider(f"{k}（px）", 0, 40, init); new_vars[k] = f"{px}px"
            if st.button("⬆ CSSに反映"):
                st.session_state.gen_css = replace_root_vars(st.session_state.gen_css, new_vars)
                st.success("CSS（:root）を更新しました。")

        # テキスト編集
        with st.expander("✏️ テキスト編集（最初のH1 & サブ）", expanded=False):
            curr_body = st.session_state.gen_html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
            curr_h1  = extract_first_h1(curr_body)
            curr_sub = extract_subtext(curr_body)
            new_h1   = st.text_input("H1", curr_h1 or site_title)
            new_sub  = st.text_input("サブ（.sub / .lead）", curr_sub or tagline)
            if st.button("⬆ テキスト反映"):
                body = curr_body
                if curr_h1:  body = replace_first_h1(body, new_h1)
                if curr_sub: body = replace_subtext(body, new_sub)
                st.session_state.gen_html = st.session_state.gen_html.replace(curr_body, body)
                st.success("テキストを更新しました。")

        # 画像差し替え
        with st.expander("🖼 画像差し替え（プレースホルダ）", expanded=False):
            curr_body = st.session_state.gen_html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
            if not st.session_state.img_slots:
                st.info("プレースホルダ（aria-label=\"image\" または class=\"img ...\"）が見つかりません。")
            else:
                updated = False
                for idx, (_, ph_html) in enumerate(st.session_state.img_slots):
                    up = st.file_uploader(f"画像 {idx+1}", type=["png", "jpg", "jpeg", "webp"], key=f"u_{idx}")
                    if up is not None:
                        data_uri, _ = data_uri_from_file(up)
                        curr_body = replace_placeholder_with_img(curr_body, ph_html, data_uri)
                        updated = True
                if updated:
                    st.session_state.gen_html = st.session_state.gen_html.replace(
                        st.session_state.gen_html.split("<body>", 1)[1].rsplit("</body>", 1)[0],
                        curr_body
                    )
                    st.session_state.img_slots = find_image_placeholders(curr_body)
                    st.success("画像を反映しました。")

        # AI差分編集
        with st.expander("🤖 AIに指示して再編集（差分適用）", expanded=False):
            st.write("例）『ヒーローを左右2カラムに』『可愛い方向に』『角丸20px』『余白を広め』『ネオン感強め』など")
            ai_edit_prompt = st.text_area("AIへの指示（日本語でOK）", height=90)

            def ai_edit(css_text: str, body_html: str, instruction: str) -> Tuple[str, str]:
                if client is None:
                    raise RuntimeError("OpenAI クライアントが初期化されていません。APIキー設定を確認してください。")
                sys = "あなたはフロントエンド/デザインの専門家です。安全な純HTML+CSSのみで編集します。"
                user = f"""
これから与える 'css' と 'body_html' を、指示に従って**直接書き換え**てください。
- 返答は JSON 1個、スキーマは {{ "css": "...", "body_html": "..." }} のみ
- scriptタグ・外部CDN・@import は禁止
- onClick 等の on* は使わない
- 画像は既存のダミーdiv/imgを整形（新規読み込み禁止）

[現在のCSS]
{css_text}

[現在のBODY]
{body_html}

[編集指示]
{instruction}
"""
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.8,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
                )
                dat = json.loads(resp.choices[0].message.content)
                return dat.get("css", ""), sanitize_html(dat.get("body_html", ""))

            if st.button("🪄 指示どおりAIで再編集する"):
                try:
                    curr_body = st.session_state.gen_html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
                    new_css, new_body = ai_edit(
                        st.session_state.gen_css,
                        curr_body,
                        ai_edit_prompt.strip() or "全体を洗練。余白と階層のコントラストを調整。"
                    )
                    if new_css:
                        st.session_state.gen_css = new_css
                    if new_body:
                        st.session_state.gen_html = st.session_state.gen_html.replace(curr_body, new_body)
                        st.session_state.img_slots = find_image_placeholders(new_body)
                    st.success("AI差分編集を反映しました。")
                except Exception as e:
                    st.error("AI差分編集に失敗しました。"); st.exception(e)

# ─────────────────────────────────────────────
# セクション8: コード表示 & ダウンロード
# ─────────────────────────────────────────────
if st.session_state.gen_html and st.session_state.gen_css:
    st.subheader("🧩 生成コード / ダウンロード")
    tabs = st.tabs(["index.html", "styles.css"])

    with tabs[0]:
        st.code(st.session_state.gen_html, language="html")
        st.download_button("index.html をDL", st.session_state.gen_html.encode("utf-8"), file_name="index.html")

    with tabs[1]:
        st.code(st.session_state.gen_css, language="css")
        st.download_button("styles.css をDL", st.session_state.gen_css.encode("utf-8"), file_name="styles.css")

    def to_zip(html: str, css: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            html_out = html
            matches = re.findall(r'src="data:[^"]+"', html_out)
            img_index = 1
            for m in matches:
                data_uri = m.split('"', 1)[1].rsplit('"', 1)[0]
                if ";base64," in data_uri:
                    parts = data_uri.split(";base64,", 1)
                    if len(parts) == 1:
                        parts = data_uri.split(";base64;", 1)  # フォールバック
                    head, b64 = parts[0], parts[1]
                    if "image/png" in head: ext = "png"
                    elif "image/webp" in head: ext = "webp"
                    elif "image/jpeg" in head or "image/jpg" in head: ext = "jpg"
                    else: ext = "png"
                    data = base64.b64decode(b64)
                    path = f"assets/img_{img_index}.{ext}"
                    z.writestr(path, data)
                    html_out = html_out.replace(data_uri, "./" + path, 1)
                    img_index += 1
            z.writestr("index.html", html_out)
            z.writestr("styles.css", css)
            z.writestr("script.js", "")
        buf.seek(0)
        return buf.getvalue()

    st.download_button(
        "📦 一括ZIP（Netlify用）",
        to_zip(st.session_state.gen_html, st.session_state.gen_css),
        file_name="lp_site_ai_editable.zip"
    )
else:
    st.info("左サイドバーを設定して［🎯 LPを生成（AI）］を押してください。生成後に右側で色/テキスト/画像/AI差分編集ができます。")
