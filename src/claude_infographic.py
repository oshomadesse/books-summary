# -*- coding: utf-8 -*-
import os, json, re, datetime, unicodedata
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
# プロジェクトルート（srcの親ディレクトリ）
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
INF_DIR  = PROJECT_DIR / "infographics"  # テンプレ配置用に存続
TPL_PATH = INF_DIR / "infographic_template.html"

# ★ Vault ルートと 100_Inbox（HTMLの保存先）
if os.getenv("GITHUB_ACTIONS"):
    VAULT_ROOT = PROJECT_DIR
    INBOX_DIR = VAULT_ROOT / "100_Inbox"
else:
    VAULT_ROOT = Path(os.getenv("VAULT_ROOT", "/Users/seihoushouba/Documents/Oshomadesse-pc")).resolve()
    INBOX_DIR  = Path(os.getenv("INBOX_DIR", str(VAULT_ROOT / "100_Inbox"))).resolve()

for d in (DATA_DIR, INF_DIR, INBOX_DIR):
    os.makedirs(d, exist_ok=True)
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

try:
    from anthropic import Anthropic
except Exception as e:
    raise RuntimeError("anthropic パッケージが必要です: pip install anthropic") from e

MODEL       = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS  = int(os.getenv("ANTHROPIC_MAX_TOKENS", "16384"))
TEMPERATURE = float(os.getenv("ANTHROPIC_TEMPERATURE", "0"))

client = None

def _get_client():
    global client
    if client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY が未設定です")
        client = Anthropic(api_key=api_key)
    return client

def _slug(s, n=80):
    if not s:
        s = "infographic"
    s = unicodedata.normalize("NFKC", str(s))
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w\-]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:n] or "infographic")

def _atomic_write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (".tmp." + p.name)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)

def _latest_nonempty_raw():
    pdir = Path(DATA_DIR)
    if not pdir.exists():
        return (None, "")
    raws = sorted(pdir.glob("deep*raw.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    ts_pattern = re.compile(r"^deep_.+_\d{8}_\d{6}__raw\.txt$")
    ts_candidates = [p for p in raws if ts_pattern.match(p.name)]
    search_list = ts_candidates + [p for p in raws if p not in ts_candidates]
    for p in search_list:
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if t and t.strip():
            return p, t
    return (None, "")

def _coerce_deep_text(deep, title_hint=""):
    if isinstance(deep, dict):
        for k in ("raw","fulltext","text","body","overview"):
            v = deep.get(k)
            if isinstance(v, (str, bytes)) and str(v).strip():
                s = str(v)
                print("🔎 deep provided via dict (key:", k, ") (len=", len(s), "chars )")
                return s
        try:
            s = json.dumps(deep, ensure_ascii=False, indent=2)
            if s and s.strip():
                print("🔎 deep provided as dict -> json-serialized (len=", len(s), "chars )")
                return s
        except Exception:
            pass

    if isinstance(deep, (str, bytes)) and str(deep).strip():
        s = str(deep)
        print("🔎 deep provided directly (len=", len(s), "chars )")
        return s

    p, t = _latest_nonempty_raw()
    if p:
        print(f"🔎 deep/raw 探索: {p} ({len(t)} chars)")
    if t and t.strip():
        return t

    if title_hint:
        return str(title_hint)
    return ""

def _read_template():
    try:
        return Path(TPL_PATH).read_text(encoding="utf-8")
    except Exception:
        print(f"⚠ テンプレート未読込: {TPL_PATH}")
        return ""

def _extract_meta_from_text(text: str):
    meta = {"title":"", "author":"", "category":""}
    try:
        obj = json.loads(text)
        for k in ("書籍名","タイトル","title"):
            if k in obj and isinstance(obj[k], str):
                meta["title"] = obj[k]
                break
        for k in ("著者名","著者","author","authors"):
            if k in obj:
                v = obj[k]
                meta["author"] = v if isinstance(v,str) else (", ".join(v) if isinstance(v,list) else str(v))
                break
    except Exception:
        pass
    if not meta["title"]:
        m = re.search(r'"(?:書籍名|タイトル|title)"\s*:\s*"([^"]+)"', text)
        if m:
            meta["title"] = m.group(1).strip()
    if not meta["author"]:
        m = re.search(r'"(?:著者名|著者|author)"\s*:\s*"([^"]+)"', text)
        if m:
            meta["author"] = m.group(1).strip()
    return meta

def _prefill_template(template_html: str, meta: dict):
    html = template_html
    repl = {
        "【書籍タイトル】": meta.get("title") or "不明",
        "【著者名】": meta.get("author") or "不明",
        "【カテゴリー】": meta.get("category") or "不明",
    }
    for k,v in repl.items():
        html = html.replace(k, v)
    return html

def _build_user_text(deep_text, book_title):
    template_html = _read_template()
    if template_html:
        meta = _extract_meta_from_text(deep_text)
        if not meta["title"] and book_title:
            meta["title"] = book_title
        # テンプレの事前整形は現状プロンプトには未埋め込み（将来拡張用）
        _prefill_template(template_html, meta)

    content_block = f"\n=== 以下の内容 ===\n{deep_text}\n=== 以上 ===\n"

    prompt = """【Infographic指示（最終版）】
あなたは視覚化のプロのデザイナー兼フロントエンド実装者です。以下の必須要件に従って、書籍リサーチの内容をすべて読み込み、書籍を「本を読んでいない人でも全体像が掴める」単一HTMLインフォグラフィックに変換してください。出力は単一の完結したHTML（内部に CSS と JavaScript を含む）だけを返してください。説明文・注釈・コードフェンスは禁止します。


タブ構成（固定）：
  - 主要概念の詳細：細かく、初見でも理解できるよう噛み砕いた説明をカード形式で整理し、各概念について「理由（なぜ重要か）」を明示する。
  - 各章の要約：各章のポイントを短い見出し＋箇条でまとめ、章ごとに「理由（なぜその章が必要か／章のメッセージの根拠）」を付す。章立てが不明な場合は論点ごとの疑似章を作る。
  - 具体例：核心メッセージが伝わる実際のケースやメタファーを少なくとも3件提示し、背景・示唆に加えて「理由（この例が示すこと）」を必ず書く。
  - 重要な引用：原典の重要フレーズを選び、引用文＋一言補足で並べる（引用がない場合は要旨で代替し“要旨”と明記）。
  - 今日のアクション：15〜30分で実行できる具体的な行動を最低3つ提示し、それぞれ目的と期待効果を付す。

必須要件：
	1. レイアウト／テキスト
	   - テキストはタブ／カード／列で分割すること。各カードは「見出し（1行）」＋「要点箇条（3〜5行）」にまとめ、必要に応じて絵文字や要点ごとに短い説明（各1行程度）を添えて咀嚼を助けること。 長文の段落は禁止。
	   - ファーストビューに「要約=問い×答え×根拠（Why＝なぜそうなのか？&How=そのためには？）」を明示する専用ブロックを配置し、1ブロック内に問い・答え・Why・Howを短く記述すること。
	2. 数値と指標
	   - 重要な数字・指標は視覚化（横棒／円グラフ／数値バッジ等）で表現し、短いラベルを付すこと。
	3. カラーパレット（メタデータ）
	   - 使用する色は「プライマリー / セカンダリー / アクセント」の3色とし、3色のうちでグラデーション使用は可。カラーコード（例：#667eea）をメタデータとしてHTML内に含めること。
	4. 網羅性と視覚化
	   - 与えられたリサーチ内容は抜けなく反映する（要約は可）。テキストの羅列で終わらせず、図や絵文字などの視覚要素を多用して情報を伝えること。
	5. レスポンシブ＆アクセシビリティ
	   - スマホ表示を最優先のレスポンシブ設計とする。タブ切替は JavaScript で実装し、適切な `aria-` 属性を付与すること。
	6. 技術的制約
	   - 外部 CDN や外部画像リンクは禁止（全てインラインで完結）。出力は HTML/CSS/JavaScript のコードのみ。最終出力は先頭に `<!DOCTYPE html>`、末尾に `</html>` を含み、タグ整合（未閉じタグなし）を保証すること。

出力形式の補足：
- 単一の自己完結 HTML（`<style>` と `<script>` を内部に含む）。
- 必須の視覚要素（SVG/HTML/CSS）を少なくとも1つ含めること。
- HTML 内のコメントやメタ領域にカラーコード（プライマリー／セカンダリー／アクセント）を明記すること。

=== 書籍リサーチ（ここに置換） ===
"""
    return prompt + content_block

def _save_raw_resp(resp, ts):
    try:
        dbg = Path(DATA_DIR) / "modules" / "claude_infographic" / f"claude_resp_raw_{ts}.txt"
        dbg.parent.mkdir(parents=True, exist_ok=True)
        try:
            s = json.dumps(resp, ensure_ascii=False, default=lambda o: getattr(o, '__dict__', str(o)))
        except Exception:
            s = repr(resp)
        dbg.write_text(s, encoding="utf-8")
        print(f"📝 Claude raw response saved: {dbg}")
    except Exception as e:
        print("⚠️ failed to save raw resp:", e)

def _call_claude(user_text):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dbg_path = Path(DATA_DIR) / "modules" / "claude_infographic" / f"claude_prompt_{ts}.txt"
    dbg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dbg_path.write_text(user_text, encoding="utf-8")
        print(f"📝 Claude user_text saved: {dbg_path}")
    except Exception:
        print(f"⚠️ prompt save failed: {dbg_path}")
    
    system_text = (
        "あなたはインフォグラフィック生成のためのコードジェネレータです。"
        "出力は単一の完結したHTML文書のみとし、先頭は'<!DOCTYPE html>'、末尾は'</html>'で終えてください。"
        "説明文・注釈・コードフェンスは一切出力しないでください。"
        "与えられた本文に明確にない情報を勝手に追加せず、曖昧な箇所は「不明」と記載してください。"
    )

    resp = _get_client().messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "16384")),
        temperature=float(os.getenv("ANTHROPIC_TEMPERATURE", "0")),
        system=system_text,
        messages=[{"role":"user","content":user_text}],
    )

    try:
        _save_raw_resp(resp, ts)
    except Exception:
        pass

    out = ""
    try:
        for part in (getattr(resp, "content", []) or []):
            if getattr(part, "type", None) == "text":
                out += getattr(part, "text", "") or ""
            else:
                out += getattr(part, "text", "") or ""
    except Exception:
        pass

    if not out:
        out = getattr(resp, "text", "") or getattr(resp, "output_text", "") or ""
    if not out:
        out = repr(resp)

    return out, getattr(resp, "usage", None)

def _extract_fields_for_template(deep_text:str):
    fields = {}
    try:
        obj = json.loads(deep_text)
        fields["核心的メッセージ"] = obj.get("核心的メッセージ") or obj.get("1) 核心的メッセージ") or obj.get("1)") or ""
        fields["エグゼクティブ・サマリー"] = obj.get("エグゼクティブ・サマリー") or obj.get("2) エグゼクティブ・サマリー") or ""
        concepts = obj.get("3) 主要概念・キーワード") or obj.get("主要概念・キーワード") or []
        fields["概念_list"] = concepts
    except Exception:
        fields["核心的メッセージ"] = deep_text[:1000]
        fields["エグゼクティブ・サマリー"] = deep_text[:1000]
        fields["概念_list"] = []
    return fields

def _vault_relative(p: Path) -> str:
    try:
        return p.resolve().relative_to(VAULT_ROOT).as_posix()
    except Exception:
        return p.as_posix()

def _obsidian_uri_for(vault_rel_path: str) -> str:
    vault = os.getenv("OBSIDIAN_VAULT_NAME") or VAULT_ROOT.name
    return f"obsidian://open?vault={quote(vault, safe='')}&file={quote(vault_rel_path, safe='')}"

def _app_local_uri(vault_rel_path: str) -> str:
    """Obsidianの内部Webビューに直接読み込ませるスキーム。iOSでHTMLが“サイトのように”開く想定"""
    vault = os.getenv("OBSIDIAN_VAULT_NAME") or VAULT_ROOT.name
    return f"app://local/{quote(vault, safe='')}/{quote(vault_rel_path, safe='')}"

# 旧：複数リンク追記関数（互換のため残置・未使用）
def _write_infographic_note(vault_rel_path: str, ob_uri: str, app_uri: str):
    pass  # 今回は Web 公開リンクのみを日次ノートに記載する方針のため未使用

# === GitHub Pages 公開ユーティリティ ==========================================
import shutil, subprocess, time
import requests

def _wait_until_http_200(url: str, timeout_sec: int = 180, interval_sec: float = 2.5) -> bool:
    """GitHub Pages のCDN伝播が終わり 200 を返すまで待機"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            r = requests.head(url, allow_redirects=True, timeout=6)
            if r.status_code == 200:
                return True
            if r.status_code in (403, 404):
                r = requests.get(url, allow_redirects=True, timeout=6)
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(interval_sec)
    return False

def _read_public_env():
    export_dir = os.getenv("PUBLIC_EXPORT_DIR", "").strip()
    base_url   = os.getenv("PUBLIC_BASE_URL", "").strip()
    auto_push  = os.getenv("PUBLIC_GIT_AUTO_PUSH", "0").strip() in ("1","true","True")
    branch     = os.getenv("PUBLIC_GIT_BRANCH", "main").strip() or "main"
    commit_tmpl= os.getenv("PUBLIC_GIT_COMMIT_MSG", "[pages] add {filename}")
    if base_url and not base_url.endswith("/"):
        base_url += "/"
    return export_dir, base_url, auto_push, branch, commit_tmpl

def _append_web_link_to_daily_note(local_html_path: str, public_url: str, title_text: str):
    # 仕様変更：日次ノートは作らない（互換のため空実装を維持）
    return

def _git_auto_push(export_dir: str, filename: str, branch: str, commit_tmpl: str) -> bool:
    try:
        rel_name = Path(filename).name
        cmds = [
            (["git", "add", "-f", rel_name], True),  # *.html を .gitignore していても強制追加
            (["git", "commit", "-m", commit_tmpl.format(filename=rel_name)], False),
            (["git", "push", "origin", branch], True),
        ]
        ok = True
        for args, noisy in cmds:
            proc = subprocess.run(args, cwd=export_dir, capture_output=True, text=True)
            if proc.returncode != 0:
                ok = False
                if noisy:
                    print(f"⚠️ git コマンド失敗: {' '.join(args)}\n{proc.stderr.strip()}")
        return ok
    except Exception as e:
        print(f"⚠️ git push に失敗: {e}")
        return False

def _publish_to_github_pages(local_html_path: str, filename: str, vault_rel: str=None, file_url: str=None, title_for_note: str = "Open on Web"):
    """
    HTML を GitHub Pages の docs へコピーし、公開URLを返す。
    必須: PUBLIC_EXPORT_DIR, PUBLIC_BASE_URL
    ポリシー: export_dir/base_url が揃っていれば、200応答確認に失敗しても public_url を返す（非厳格）。
             厳格な待機は PUBLIC_PAGES_STRICT_200=1 で有効化。
    """
    export_dir, base_url, auto_push, branch, commit_tmpl = _read_public_env()
    wait_timeout = int(os.getenv("PUBLIC_PAGES_WAIT_TIMEOUT", "180"))
    strict_200   = os.getenv("PUBLIC_PAGES_STRICT_200", "0").strip().lower() in ("1","true","yes")
    fallback_url = file_url or vault_rel

    if not export_dir or not base_url:
        print("⚠️ PUBLIC_EXPORT_DIR または PUBLIC_BASE_URL が未設定のためフォールバックします。")
        return fallback_url, "warn_env_missing"

    try:
        os.makedirs(export_dir, exist_ok=True)
        dst_path = Path(export_dir) / Path(filename).name

        # 生成先が既に export_dir の場合はコピー不要（SameFileError回避）
        if Path(local_html_path).resolve() != dst_path.resolve():
            shutil.copyfile(local_html_path, dst_path)
        else:
            print("ℹ️ local_html_path は export_dir と同一のためコピー省略")

        public_url = base_url + quote(dst_path.name)

        if auto_push:
            # export_dir が Git 管理下か確認
            proc = subprocess.run(["git","rev-parse","--is-inside-work-tree"], cwd=export_dir, capture_output=True, text=True)
            in_repo = (proc.returncode == 0 and proc.stdout.strip() == "true")
            if not in_repo:
                print("⚠️ export_dir が Git リポジトリではありません。public_url を返します（未プッシュの可能性あり）。")
                return public_url, "warn_no_repo"
            pushed = _git_auto_push(str(Path(export_dir).resolve()), dst_path.name, branch, commit_tmpl)
            if not pushed:
                print("⚠️ push 失敗。public_url を返します（CDN未反映の可能性あり）。")
                return public_url, "warn_push_failed"

        if strict_200:
            if _wait_until_http_200(public_url, timeout_sec=wait_timeout):
                print(f"✅ GitHub Pages 200 確認: {public_url}")
                return public_url, None
            else:
                print(f"⚠️ 200応答を待てずタイムアウトしましたが public_url を返します: {public_url}")
                return public_url, "pending"

        # 非厳格モード: 即 public_url 返す
        print(f"➡️ GitHub Pages URL（非厳格モード）: {public_url}")
        return public_url, None

    except Exception as e:
        print(f"⚠️ GitHub Pages 公開処理で例外が発生しました（public_urlにできないためフォールバック）：{e}")
        return fallback_url, "warn_publish_error"
# ============================================================================

def generate_infographic(deep, book_title):
    deep_text = _coerce_deep_text(deep, book_title)
    print(f"🧪 deep_text chars = {len(deep_text)}")
    user_text = _build_user_text(deep_text, book_title)
    html, _usage = _call_claude(user_text)
    return html

def generate_infographic_complete(deep, book_title):
    deep_text = _coerce_deep_text(deep, book_title)
    print(f"🧪 deep_text chars = {len(deep_text)}")
    user_text = _build_user_text(deep_text, book_title)
    html, usage = _call_claude(user_text)

    start = re.search(r'(?is)(<!DOCTYPE\s+html[^>]*>|<html\b[^>]*>)', html)
    if start:
        html = html[start.start():]
    end = re.search(r'(?is)</html\s*>', html)
    if end:
        html = html[:end.end()]

    if (not html) or (len(html.strip()) < 200) or ('<html' not in html.lower()):
        print("⚠️ 生成HTMLが不十分なためテンプレートベースのフォールバックを作成します。")
        tpl = _read_template()
        if tpl:
            fields = _extract_fields_for_template(deep_text)
            tpl = tpl.replace("【核心的メッセージをここに記載】", fields.get("核心的メッセージ","不明"))
            tpl = tpl.replace("【エグゼクティブサマリーをここに記載】", fields.get("エグゼクティブ・サマリー","不明"))
            try:
                if fields.get("概念_list"):
                    first = fields["概念_list"][0]
                    term = first.get("概念") or first.get("term") or ""
                    definition = first.get("解説") or first.get("definition") or ""
                    tpl = tpl.replace("【概念名】", term or "不明")
                    tpl = tpl.replace("【概念の定義・説明】", definition or "不明")
            except Exception:
                pass
            html = tpl
        else:
            html = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>{}</title></head><body><pre>{}</pre></body></html>".format(
                book_title or "infographic", (deep_text[:10000] + "...") if len(deep_text)>10000 else deep_text
            )

    name = f"{_slug(book_title)}_infographic.html"
    name = f"{_slug(book_title)}_infographic.html"
    
    # ★ HTML は infographics 直下に保存（GitHub Pages公開用）
    # DOCS_DIR = os.path.join(PROJECT_DIR, "docs")
    # os.makedirs(DOCS_DIR, exist_ok=True)
    out_path = Path(INF_DIR) / name
    
    _atomic_write(str(out_path), html)
    print(f"🗂 出力保存: {out_path}")

    # ★ User Request: Step5の段階で即時PushしてPages反映を早める
    # try:
    #     if os.getenv("GITHUB_ACTIONS"):
    #         print("🚀 GitHub Actions環境検出: 生成されたHTMLを即時Pushします...")
    #         subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
    #         subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)
    #         
    #         # docs/filename.html を追加
    #         subprocess.run(["git", "add", "-f", str(out_path)], check=True)
    #         subprocess.run(["git", "commit", "-m", f"feat: add infographic {name} (immediate push)"], check=False)
    #         subprocess.run(["git", "push"], check=True)
    #         print("✅ 即時Push完了")
    # except Exception as e:
    #     print(f"⚠️ 即時Push失敗 (処理は継続します): {e}")

    # 互換: 絶対 file:// URL
    file_url = "file://" + quote(str(out_path.resolve()))
    # Vault相対リンク
    vault_rel = _vault_relative(out_path)
    # 2種のURI（旧フロー互換: 返却用に保持するがノートには使わない）
    ob_uri  = _obsidian_uri_for(vault_rel)
    app_uri = _app_local_uri(vault_rel)

    # === GitHub Pages へ公開（フォールバック付き） ===
    public_url, _warn = _publish_to_github_pages(
        local_html_path=str(out_path),
        filename=name,
        vault_rel=vault_rel,
        file_url=file_url,
        title_for_note=(book_title or name)
    )
    infographic_url = public_url  # 以降の返却/テンプレ変数はこのURL

    # === Usage 抽出（input/output tokens）===
    usage_dict = {}
    if usage:
        def _get(d,k,default=0):
            try:
                return int(getattr(d,k)) if hasattr(d,k) else int(d.get(k, default))
            except Exception:
                return default
        usage_dict = {
            "model": MODEL,
            "input_tokens": _get(usage,"input_tokens"),
            "output_tokens": _get(usage,"output_tokens"),
        }
    in_tok  = int((usage_dict or {}).get("input_tokens", 0) or 0)
    out_tok = int((usage_dict or {}).get("output_tokens", 0) or 0)

    # === コストとクレジット（$/百万トークン基準） ===
    rate_in_le  = float(os.getenv("ANTHROPIC_PRICE_INPUT_LE200K",  "3"))
    rate_in_gt  = float(os.getenv("ANTHROPIC_PRICE_INPUT_GT200K",  "6"))
    rate_out_le = float(os.getenv("ANTHROPIC_PRICE_OUTPUT_LE200K", "15"))
    rate_out_gt = float(os.getenv("ANTHROPIC_PRICE_OUTPUT_GT200K", "22.50"))

    rate_in  = rate_in_gt  if in_tok  > 200_000 else rate_in_le
    rate_out = rate_out_gt if out_tok > 200_000 else rate_out_le

    cost_usd = (in_tok/1_000_000.0)*rate_in + (out_tok/1_000_000.0)*rate_out

    try:
        credit_start = float(((os.getenv("CLAUDE_START_CREDIT") or "19.19")).replace(",", ""))
    except Exception:
        credit_start = 19.19
    credit_remain = max(credit_start - cost_usd, 0.0)

    # ★ Obsidian ノートの変数
    obs_vars = {
        "{{infographic_url}}": infographic_url,
        "{{claude_usaget}}":  out_tok,
        "{{claude_credit}}": f"${credit_remain:.2f}",
    }

    # === JSON 保存（variables / cost_usd / public_url を含めて保存） ===
    # 変更: data/infographics.json -> data/modules/claude_infographic/infographics.json
    old_agg_path = Path(DATA_DIR) / "infographics.json"
    agg_path = Path(DATA_DIR) / "modules" / "claude_infographic" / "infographics.json"
    
    # 移行ロジック: 旧ファイルがあり、新ファイルがない場合は移動する
    if old_agg_path.exists() and not agg_path.exists():
        try:
            agg_path.parent.mkdir(parents=True, exist_ok=True)
            old_agg_path.rename(agg_path)
            print(f"🚚 infographics.json を移動しました: {old_agg_path} -> {agg_path}")
        except Exception as e:
            print(f"⚠️ infographics.json の移動に失敗: {e}")


    try:
        if agg_path.exists():
            arr = json.loads(agg_path.read_text(encoding="utf-8"))
            if not isinstance(arr, list):
                arr = []
        else:
            arr = []
        meta = {
            "title": book_title or "",
            "file_url": file_url,
            "vault_rel": vault_rel,
            "obsidian_uri": ob_uri,
            "app_local_uri": app_uri,
            "html_path": str(out_path),
            "model": MODEL,
            "usage": usage_dict,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "public_url": infographic_url,
            "variables": obs_vars,
            "cost_usd": cost_usd,
        }
        arr.append(meta)
        _atomic_write(str(agg_path), json.dumps(arr, ensure_ascii=False, indent=2))
        print(f"🗂 JSON保存: {agg_path}")
    except Exception as e:
        print(f"⚠️ JSON保存エラー: {e}")

    return {
        "path": str(agg_path),
        "html_path": str(out_path),
        "json": meta,
        "usage": usage_dict,
        "file_url": file_url,
        "vault_rel": vault_rel,
        "obsidian_uri": ob_uri,
        "app_local_uri": app_uri,
        "{{infographic_url}}": infographic_url,
        "{{claude_usaget}}":  out_tok,
        "{{claude_credit}}": f"${credit_remain:.2f}",
    }

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python claude_infographic.py <deep_text_file> <book_title>")
        sys.exit(1)

    deep_file = Path(sys.argv[1])
    book_title = sys.argv[2]
    deep_arg = ""
    if deep_file.exists():
        txt = deep_file.read_text(encoding="utf-8")
        print(f"🔎 deep/raw 読み込み: {deep_file} ({len(txt)} chars)")
        deep_arg = txt
    else:
        print(f"⚠ deep file not found: {deep_file}")

    res = generate_infographic_complete(deep_arg, book_title)
    print("✅ インフォグラフィック生成完了")
