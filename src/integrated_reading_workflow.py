#!/usr/bin/env python3
"""
統合読書ワークフロー（実運用モード・usage自動取得・進捗ログ強化）
--until 7 まで：Step6で中間サマリ確認 → Step7でノート生成
"""

import argparse
import sys
import os
import traceback
from dotenv import load_dotenv
import importlib.util
import json
import pathlib
import re
from pathlib import Path
from datetime import datetime, timedelta

# 既存仕様を尊重：Step1-3は変更しない（このファイルではそのまま保持）
import chatgpt_research as gemini_research
from gemini_recommend import GeminiConnector as GeminiRecommendConnector
try:
    from gemini_recommend import FLASH_MODEL
except Exception:
    FLASH_MODEL = getattr(gemini_research, "FLASH_MODEL", "gemini-2.5-flash")

import claude_infographic

# ============ 環境読み込みとディレクトリ保証 ============
# ============ 環境読み込みとディレクトリ保証 ============
# プロジェクトルート（srcの親ディレクトリ）
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
INBOX_DIR = PROJECT_DIR / "100_Inbox"
# CI環境（GITHUB_ACTIONS）の場合はリポジトリルートをVAULT_ROOTとみなす
if os.getenv("GITHUB_ACTIONS"):
    VAULT_ROOT = PROJECT_DIR.resolve()
    # INBOX_DIR は PROJECT_DIR / "artifacts" に変更（CIでコミット対象にするため）
    INBOX_DIR = PROJECT_DIR / "artifacts"
else:
    # ローカル環境: 環境変数または親ディレクトリからの推測
    VAULT_ROOT = Path(os.getenv("VAULT_ROOT", "/Users/seihoushouba/Documents/Oshomadesse-pc")).resolve()
    # ユーザー要望: ローカル実行時は oshomadesse-pc > 100_Inbox (つまり VAULT_ROOT 直下の 100_Inbox)
    # INBOX_DIR は PROJECT_DIR / "100_Inbox" で固定されるため、VAULT_ROOTからの推測は不要
    # ただし、互換性のため環境変数INBOX_DIRが設定されていればそれを優先
    INBOX_DIR = Path(os.getenv("INBOX_DIR", str(INBOX_DIR))).resolve()


try:
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))
except Exception:
    load_dotenv()

def _ensure_dirs():
    for d in (
        os.path.join(PROJECT_DIR, "infographics"),
        os.path.join(PROJECT_DIR, "data"),
        str(INBOX_DIR),
    ):
        os.makedirs(d, exist_ok=True)
_ensure_dirs()

LOG_DIR = os.path.join(PROJECT_DIR, "data", "integrated")
os.makedirs(LOG_DIR, exist_ok=True)
_DEFAULT_RUN_LOG = os.path.join(LOG_DIR, "integrated_run_" + datetime.now().strftime("%Y%m%d") + ".log")
def _make_printer(logfile):
    import builtins as _bi
    def _p(*a, **k):
        _bi.print(*a, **k)
        try:
            with open(logfile, "a", encoding="utf-8") as fp:
                _bi.print(*a, **k, file=fp)
        except Exception:
            pass
    return _p
RUN_LOG = os.environ.get("IRW_LOGFILE", _DEFAULT_RUN_LOG)
print = _make_printer(RUN_LOG)
print("Logging to: " + str(RUN_LOG))

CLAUDE_CREDIT_START = float(os.getenv("CLAUDE_START_CREDIT", "18.35"))
CHATGPT_CREDIT_START = float(os.getenv("CHATGPT_START_CREDIT", "4.92"))

def _should_use_responses(model: str) -> bool:
    """
    gpt-5 系は Responses API を優先。環境変数 OPENAI_USE_RESPONSES=1 でも強制。
    """
    if os.getenv("OPENAI_USE_RESPONSES","").strip().lower() in ("1","true"):
        return True
    m = (model or "").lower()
    return m.startswith("gpt-5")

def step0_diag_env(probe=False, model_hint=None):
    print("🔧 環境診断開始")
    keys = {
        "OPENAI_API_KEY": (os.getenv("OPENAI_API_KEY")[:6] + "...") if os.getenv("OPENAI_API_KEY") else "(unset)",
        "ANTHROPIC_API_KEY": (os.getenv("ANTHROPIC_API_KEY")[:6] + "...") if os.getenv("ANTHROPIC_API_KEY") else "(unset)",
        "GEMINI_API_KEY": (os.getenv("GEMINI_API_KEY")[:6] + "...") if os.getenv("GEMINI_API_KEY") else "(unset)",
        "CHATGPT_START_CREDIT": os.getenv("CHATGPT_START_CREDIT") or os.getenv("chatgpt_start_credit") or "(unset)",
        "CLAUDE_START_CREDIT": os.getenv("CLAUDE_START_CREDIT") or "(unset)"
    }
    try:
        print(json.dumps(keys, ensure_ascii=False, indent=2))
    except Exception:
        print(keys)

    for d in (
        str(PROJECT_DIR),
        os.path.join(PROJECT_DIR, "data"),
        os.path.join(PROJECT_DIR, "infographics"),
        str(INBOX_DIR),
    ):
        try:
            os.makedirs(d, exist_ok=True)
            print(f"DIR OK: {d}")
        except Exception as e:
            print(f"DIR NG: {d} -> {e}")

    if not probe:
        print("ℹ️ probe未実行（--probe指定で最小LLMリクエスト検証）")
        return

    # --- ここから: プローブ（Responses/ChatCompletions をモデルで自動切替） ---
    try:
        from openai import OpenAI
        client = OpenAI()
        model = model_hint or os.getenv("GPT5_MODEL", "gpt-5")
        print(f"OpenAI probe model={model}")

        if _should_use_responses(model):
            r = client.responses.create(
                model=model,
                input=[{"role":"user","content":"Return exactly the word: pong"}],
                max_output_tokens=32
            )
            txt = (getattr(r, "output_text", None) or "").strip()
            u = getattr(r, "usage", None)
            usage = {}
            if u:
                def _g(obj, name, alt=None):
                    return getattr(obj, name, None) if hasattr(obj, name) else (obj.get(name) if isinstance(obj, dict) else alt)
                inp = int(_g(u,"input_tokens",0) or 0)
                out = int(_g(u,"output_tokens",0) or 0)
                tot = int(_g(u,"total_tokens",inp+out) or (inp+out))
                usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": tot}
        else:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role":"user","content":"Return exactly the word: pong"}],
                max_tokens=32
            )
            txt = r.choices[0].message.content if getattr(r, "choices", None) else ""
            u = getattr(r, "usage", None)
            usage = {}
            if u:
                inp = int(getattr(u,"prompt_tokens",0) or 0)
                out = int(getattr(u,"completion_tokens",0) or 0)
                tot = int(getattr(u,"total_tokens", inp+out) or (inp+out))
                usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": tot}

        print(f"LLM応答: {txt!r}")
        print(f"usage: {usage}")
    except Exception as e:
        print("❌ OpenAIプローブ失敗:", e)

# Sheets は存在しない可能性あり（実運用時は置く）
try:
    import sheets_connector
except Exception:
    sheets_connector = None

# ============ Step1: 除外本取得 ============
def step1_get_excluded_books():
    print("📊 除外本リスト取得中（Google Sheets）...")
    if sheets_connector and hasattr(sheets_connector, "get_excluded_books"):
        try:
            excluded_books = sheets_connector.get_excluded_books()
            print(f"✅ 除外本リスト取得成功: {len(excluded_books)}冊")
            return excluded_books or []
        except Exception as e:
            print(f"⚠ Sheets取得に失敗: {e} → 空リストで継続")
            return []
    else:
        print("⚠ sheets_connector 未設定 → 空リストで継続")
        return []

# ============ Step2: Geminiで本推薦 ============
def step2_generate_recommendations(excluded, usage_records):
    """
    強化版 Step2:
    - excluded を強制的にタイトル文字列リストへ変換
    - API 生データを検証して整形
    - ローカルで二重除外（is_banned_title）を行い、モデルが除外を無視しても弾く
    """
    def _coerce_to_title_list(excluded_input):
        titles = []
        if not excluded_input:
            return titles
        if isinstance(excluded_input, dict):
            t = excluded_input.get("title") or excluded_input.get("name") or ""
            if t:
                titles.append(str(t).strip())
            return titles
        if isinstance(excluded_input, (list, tuple, set)):
            for e in excluded_input:
                if isinstance(e, dict):
                    t = e.get("title") or e.get("name") or ""
                else:
                    t = str(e)
                t = (t or "").strip()
                if t:
                    titles.append(t)
            return titles
        return [str(excluded_input).strip()]

    def _validate(raw, target=5):
        out = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title,
                "author": (item.get("author") or "").strip(),
                "category": (item.get("category") or "").strip(),
                "reason": (item.get("reason") or "").strip()
            })
            if len(out) >= target:
                break
        return out

    # 1) 入力の正規化（常に文字列タイトルリストに）
    excluded_titles = _coerce_to_title_list(excluded)
    print("DEBUG: excluded_titles ->", excluded_titles)

    # 2) コネクタ準備
    connector = GeminiRecommendConnector(verbose=True)
    print("✅ Gemini Connector 準備完了")
    print(f"   📚 本推薦: {FLASH_MODEL}")

    # 3) 推薦取得
    print("🔍 Gemini で本推薦中...")
    raw_result = connector.get_book_recommendations(excluded_titles)

    # 4) 検証・整形
    validated = _validate(raw_result, target=5)

    # 5) ローカルで二重除外（is_banned_title を利用）
    final = []
    for item in validated:
        title = item["title"]
        try:
            if is_banned_title(title, excluded_titles, final):
                if getattr(connector, "verbose", False):
                    print(f"DEBUG: filtered out (in excluded list or similar): {title}")
                continue
        except Exception:
            # 除外判定で問題が発生しても処理を継続する
            pass
        final.append(item)
        if len(final) >= 5:
            break

    # 6) 表示・usage記録
    titles = [i["title"] for i in final]
    print("📝 推薦（除外適用後）: " + ", ".join(titles))

    usage_records["gemini_step2"] = {
        "model": FLASH_MODEL,
        "input_tokens": 500,
        "output_tokens": 700,
        "rpm": 2, "tpm": 200000, "rpd": 50
    }
    return final

# 類似／同一判定（Step2内部から呼ばれるヘルパ。既存動作を邪魔しない素直判定）
def is_banned_title(candidate, excluded_titles, current_list):
    try:
        import re, unicodedata
        def norm(s):
            s = unicodedata.normalize("NFKC", str(s)).lower()
            s = re.sub(r"[\s\-_・、。.,/|]+", "", s)
            return s
        cn = norm(candidate)
        pool = [norm(t) for t in (excluded_titles or [])]
        pool += [norm(d.get("title")) for d in (current_list or []) if isinstance(d, dict) and d.get("title")]
        return cn in set(pool)
    except Exception:
        return False

# ============ Step3: 推薦から本をランダム選出 ============
def step3_select_book(recommendations):
    import random, os
    print("=========== Step3: 推薦から本をランダム選出 ===========")
    if not recommendations:
        raise ValueError("推薦が空です。Step2の出力を確認してください。")
    seed_env = os.getenv("RANDOM_SEED")
    if seed_env and seed_env.isdigit():
        random.seed(int(seed_env))
        print(f"🎲 ランダム選出（再現シード: {seed_env}）")
    else:
        print("🎲 ランダム選出（非決定）")
    for i, it in enumerate(recommendations, 1):
        title = (it.get("title") if isinstance(it, dict) else str(it))
        print(f"   候補{i}: {title}")
    idx = random.randrange(len(recommendations))
    sel = recommendations[idx]
    title = (sel.get("title") if isinstance(sel, dict) else str(sel))
    print(f"✅ 選択本: {idx+1}/{len(recommendations)} → {title}")
    return sel

# ============ Step4: Deep Research ============
def step4_deep_research(book, usage_records):
    print("   Deep Research: GPT-5")
    import json, traceback, chatgpt_research
    title = book.get("title") if isinstance(book, dict) else str(book)
    author = book.get("author") if isinstance(book, dict) else ""
    category = book.get("category") if isinstance(book, dict) else ""
    res = None
    try:
        ConnectorClass = (
            getattr(chatgpt_research, "ChatGPTConnector", None)
            or getattr(chatgpt_research, "GeminiConnector", None)
            or getattr(chatgpt_research, "GeminiResearchConnector", None)
        )
        connector = ConnectorClass(verbose=True) if ConnectorClass else None
        if connector and hasattr(connector, "get_deep_research_json"):
            try:
                res = connector.get_deep_research_json(title, author, category=category)
            except Exception:
                res = None
        if not res and connector and hasattr(connector, "deep_research"):
            try:
                res = connector.deep_research(title, author)
            except Exception:
                res = None
    except Exception:
        traceback.print_exc()
        res = None
    out = {}
    try:
        if isinstance(res, dict):
            out = res
        elif isinstance(res, str):
            s2 = res.strip()
            if s2:
                try:
                    out = json.loads(s2)
                except Exception:
                    out = {"overview": s2}
            else:
                out = {}
        elif res is None:
            out = {}
        else:
            try:
                out = json.loads(str(res))
            except Exception:
                out = {"overview": str(res)}
    except Exception as e:
        print("レスポンス正規化で例外: " + str(e))
        traceback.print_exc()
        out = {}
    u = {}
    try:
        u = out.get("usage") or {}
    except Exception:
        u = {}
    def _to_int(x, d=0):
        try:
            return int(x or d)
        except Exception:
            try:
                return int(float(x))
            except Exception:
                return d
    in_tok = _to_int(u.get("input_tokens"), 0)
    out_tok = _to_int(u.get("output_tokens"), 0)
    total_tok = _to_int(out.get("chatgpt_usaget", u.get("total_tokens") if isinstance(u, dict) else 0), in_tok + out_tok)
    if total_tok and (in_tok + out_tok) == 0:
        in_tok = int(total_tok * 6 // 10)
        out_tok = total_tok - in_tok
    usage_records["chatgpt_step4"] = {
        "model": str(getattr(chatgpt_research, "PRO_MODEL", "gpt-5")),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total_tok,
        "note": "via GPT-5 deep research"
    }
    # 返却に raw 長が無い場合に備え、ここで概算長を付与
    try:
        deep_len = len(str(out.get("raw") or "")) if isinstance(out, dict) else len(str(out))
    except Exception:
        deep_len = len(json.dumps(out, ensure_ascii=False))
    out["_raw_len"] = deep_len
    print(f"ℹ️ Deep Research length: {deep_len} chars")
    return out

# ============ Step5: Claudeでインフォグラフィック生成（公開URL/使用量/残高 取り込み） ============
def step5_generate_infographic(deep_research_text, book, usage_records):
    print("🛠 Claudeでインフォグラフィック生成中（deepを渡す）...")
    res = None
    try:
        title = (book.get("title") if isinstance(book, dict) else str(book)) or ""
        if hasattr(claude_infographic, "generate_infographic_complete"):
            try:
                res = claude_infographic.generate_infographic_complete(deep_research_text or {}, title)
            except TypeError:
                res = claude_infographic.generate_infographic(deep_research_text, title)
        else:
            res = claude_infographic.generate_infographic(deep_research_text, title)
    except Exception as e:
        print("⚠ インフォグラフィック生成で例外:", e)
        res = None
    print("✅ インフォグラフィック生成完了")

    # usage抽出（実値で上書き）
    in_tok, out_tok = 0, 0
    model = "claude-4-sonnet"
    if isinstance(res, dict):
        u = res.get("usage") or {}
        model = (u.get("model") or res.get("json", {}).get("model") or model)
        try:
            in_tok = int(u.get("input_tokens", u.get("prompt_tokens", in_tok)) or in_tok)
        except Exception:
            pass
        try:
            out_tok = int(u.get("output_tokens", u.get("completion_tokens", out_tok)) or out_tok)
        except Exception:
            pass
    usage_records["infographic"] = {
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok
    }

    # 追加: 公開URL / 残高
    infographic_url = ""
    claude_usaget = 0
    claude_credit = ""
    if isinstance(res, dict):
        infographic_url = (
            res.get("infographic_url")
            or res.get("{{infographic_url}}")
            or (res.get("json") or {}).get("public_url")
            or ""
        )
        
        # ★ User Request: Obsidianノート内のリンクもGitHub Pagesにする
        inf_path = res.get("html_path") or res.get("path") or ""
        if inf_path:
            import os
            from urllib.parse import quote
            fname = os.path.basename(inf_path)
            # docs直下なので /infographics/ は不要
            infographic_url = f"https://oshomadesse.github.io/books-summary/{quote(fname)}?openExternalBrowser=1"

        claude_usaget = res.get("claude_usaget") or res.get("{{claude_usaget}}") or claude_usaget
        claude_credit = res.get("claude_credit") or res.get("{{claude_credit}}") or ""

        # 呼び出し側（Step6）が使いやすいようにトップレベルに正規化キーを付与
        res["infographic_url"] = infographic_url
        res["claude_usaget"] = claude_usaget
        res["claude_credit"] = claude_credit

    if isinstance(res, dict):
        return res
    if isinstance(res, str):
        return {
            "path": "",
            "html_path": "",
            "json": {},
            "usage": {"model": model, "input_tokens": in_tok, "output_tokens": out_tok},
            "infographic_url": infographic_url,
            "claude_usaget": claude_usaget,
            "claude_credit": claude_credit,
        }
    return {
        "path": "",
        "html_path": "",
        "json": {},
        "usage": {"model": model, "input_tokens": in_tok, "output_tokens": out_tok},
        "infographic_url": infographic_url,
        "claude_usaget": claude_usaget,
        "claude_credit": claude_credit,
    }


# ============ Step6: 中間サマリ（ノート構成に必要な変数の値一覧） ============
def step6_mid_summary(book, deep_research, infographic_result):
    def _get(d, k, default=""):
        try:
            v = d.get(k)
            return v if v is not None else default
        except Exception:
            return default

    def _as_text(x):
        if isinstance(x, str):
            return x.strip()
        if isinstance(x, (list, tuple)):
            return " / ".join([_as_text(v) for v in x if v is not None and str(v).strip()])
        if isinstance(x, dict):
            for k in ("text","value","content","概要","要約","summary","message"):
                v = x.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            try:
                return json.dumps(x, ensure_ascii=False)
            except Exception:
                return str(x)
        return "" if x is None else str(x).strip()

    # === 追加: フォールバック抽出系（deep_researchが不完全でも自己修復） ===
    def _nfkc_lower(s: str) -> str:
        import unicodedata, re as _re
        s = unicodedata.normalize("NFKC", str(s)).lower()
        s = _re.sub(r"\s+", "", s)
        s = _re.sub(r"[0-9_]+", "", s)
        s = _re.sub(r"[ -/:-@\[-`{-~]", "", s)  # ASCII記号のみ除去（CJKは保持）
        return s

    def _dig_any(d, keys):
        if not isinstance(d, dict): return None
        targets = [_nfkc_lower(k) for k in keys if k]
        for k, v in d.items():
            nk = _nfkc_lower(k)
            for t in targets:
                if t and t in nk:
                    return v
            if isinstance(v, dict):
                r = _dig_any(v, keys)
                if r is not None: return r
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, dict):
                        r = _dig_any(it, keys)
                        if r is not None: return r
        return None

    def _strip_code_fences(t: str) -> str:
        import re as _re
        if not isinstance(t, str): return ""
        return _re.sub(r"```.*?```", "", t, flags=_re.S)

    def _grab_block(label_patterns, text: str) -> str:
        import re as _re
        head = r"(?:\d+[\.\)]\s*)?"
        labels = "|".join([_re.escape(lp) for lp in label_patterns])
        pat = rf"(?ms)^\s*{head}(?:{labels})(?:（.*?）)?\s*[:：]?\s*\n?(?P<body>.+?)(?=^\s*{head}(核心的メッセージ|核心メッセージ|エグゼクティブ・サマリー|エグゼクティブサマリー|Executive Summary|Core Message|関連書籍|Related Books)\b|\Z)"
        m = _re.search(pat, text or "")
        return _re.sub(r"^[\-\*\•・]\s*", "", m.group('body'), flags=_re.M).strip() if m else ""

    def _first_nonempty(*vals) -> str:
        for v in vals:
            s = _as_text(v)
            if s: return s
        return ""

    # 追加: JSON文字列を安全にdictへ
    def _json_from_text(text: str):
        if not isinstance(text, str) or not text.strip(): return None
        import re as _re
        s = _strip_code_fences(text).strip()
        s = s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
        s = _re.sub(r",\s*([\]\}])", r"\1", s)
        # 1) 全体がJSON
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                j = json.loads(s)
                if isinstance(j, dict): return j
                if isinstance(j, list) and j and isinstance(j[0], dict): return j[0]
            except Exception:
                pass
        # 2) 最初に見つかるJSONブロック
        m = _re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", s)
        if m:
            try:
                j = json.loads(m.group(1))
                if isinstance(j, dict): return j
                if isinstance(j, list) and j and isinstance(j[0], dict): return j[0]
            except Exception:
                return None
        return None

    # JSONっぽい文字列に対して、その場で再抽出
    def _maybe_json_fix(s: str, keys):
        if not isinstance(s, str): return s
        ss = s.strip()
        if ss.startswith("{") or ss.startswith("["):
            j = _json_from_text(ss)
            if isinstance(j, dict):
                v = _dig_any(j, list(keys))
                return _as_text(v) or s
        return s

    # related_books が配列/辞書の場合の整形
    def _format_related(x):
        if isinstance(x, str): return x.strip()
        buf=[]
        if isinstance(x, list):
            for it in x:
                if isinstance(it, str) and it.strip():
                    buf.append(it.strip())
                elif isinstance(it, dict):
                    t = it.get("書名") or it.get("title") or it.get("name") or ""
                    a = it.get("著者") or it.get("author") or it.get("authors") or ""
                    r = it.get("関連性") or it.get("reason") or it.get("説明") or ""
                    t = str(t).strip(); a = str(a).strip(); r = str(r).strip()
                    base = f"{t}（{a}）" if (t and a) else (t or a)
                    s = f"{base}: {r}" if r and base else (base or r)
                    if s: buf.append(s)
                else:
                    s = _as_text(it)
                    if s: buf.append(s)
        elif isinstance(x, dict):
            return _as_text(x)
        return " / ".join(buf).strip()

    # ==== book 基本 ====
    title = (book.get("title") if isinstance(book, dict) else str(book)) or ""
    author = (book.get("author") if isinstance(book, dict) else "") or ""
    category = (book.get("category") if isinstance(book, dict) else "") or ""

    # ==== deep_research 正規化 ====
    dr = deep_research if isinstance(deep_research, dict) else {}
    raw_text = _as_text(dr.get("raw")) if isinstance(dr, dict) else ""
    parsed = dr.get("parsed") if isinstance(dr, dict) else None

    # 重要: 空dictも無効扱いにして raw から再パース
    if (not isinstance(parsed, dict)) or (isinstance(parsed, dict) and not parsed):
        # chatgpt_research 側で JSONを返し損ねた場合の救済
        j = _json_from_text(raw_text)
        parsed = j if isinstance(j, dict) else {}

    # ==== 主要フィールド（確実に埋める） ====
    research_url = _first_nonempty(dr.get("research_url"), "")

    core_message = _first_nonempty(
        dr.get("core_message"),
        _dig_any(parsed, ["核心的メッセージ","核心メッセージ","core_message","coremessage","core_messeage"]),
        _grab_block(["核心的メッセージ","核心メッセージ","Core Message"], _strip_code_fences(raw_text)),
        _strip_code_fences(raw_text)[:350] if raw_text else ""
    )
    # JSONっぽければここで再抽出
    core_message = _maybe_json_fix(core_message, ["核心的メッセージ","核心メッセージ","core_message","coremessage"])

    executive_summary = _first_nonempty(
        dr.get("executive_summary"),
        _dig_any(parsed, ["エグゼクティブ・サマリー","エグゼクティブサマリー","executive_summary","execsummary","executivesummary"]),
        _grab_block(["エグゼクティブ・サマリー","エグゼクティブサマリー","Executive Summary","要約","概要","まとめ"], _strip_code_fences(raw_text)),
        _strip_code_fences(raw_text)[:600] if raw_text else ""
    )
    executive_summary = _maybe_json_fix(executive_summary, ["エグゼクティブ・サマリー","エグゼクティブサマリー","executive_summary","execsummary","executivesummary"])

    related_books = _first_nonempty(
        dr.get("related_books"),
        _dig_any(parsed, ["関連書籍","related_books","relatedbooks","参考文献","関連文献"]),
        _grab_block(["関連書籍","Related Books","参考文献","関連文献"], _strip_code_fences(raw_text))
    )
    # 配列・辞書にも対応
    if not isinstance(related_books, str):
        related_books = _format_related(related_books)
    else:
        related_books = _maybe_json_fix(related_books, ["関連書籍","related_books","relatedbooks","参考文献","関連文献"])

    practical_actions = _first_nonempty(
        dr.get("practical_actions"),
        _dig_any(parsed, ["今日できるアクション","今日できる行動","今日行えるアクション","todayactions","today_action","immediateactions","実践","アクション","具体行動","actions","recommendations"])
    )

    # ==== アクション3件抽出 ====
    if isinstance(practical_actions, str):
        actions = [a.strip() for a in practical_actions.split(" / ") if a.strip()]
    elif isinstance(practical_actions, (list, tuple)):
        actions = [str(a).strip() for a in practical_actions if str(a).strip()]
    else:
        actions = []
    actions = (actions + ["", "", ""])[:3]
    action_a, action_b, action_c = actions

    # インフォグラフィック
    inf_path = ""
    if isinstance(infographic_result, dict):
        inf_path = infographic_result.get("html_path") or infographic_result.get("path") or ""
    infographic_url = ""
    claude_usaget = 0
    claude_credit = ""
    if isinstance(infographic_result, dict):
        infographic_url = infographic_result.get("infographic_url") or infographic_result.get("{{infographic_url}}") or ""
        claude_usaget = infographic_result.get("claude_usaget") or infographic_result.get("{{claude_usaget}}") or 0
        claude_credit = infographic_result.get("claude_credit") or infographic_result.get("{{claude_credit}}") or ""

    chatgpt_usaget = int(_get(dr, "chatgpt_usaget", 0) or 0)
    chatgpt_credit = _get(dr, "chatgpt_credit", 0.0)

    print("\n========== Step6: 中間サマリ（ノート変数） ==========")
    print(f"🧠 タイトル : {title}")
    print(f"👤 著者     : {author}")
    print(f"🏷  カテゴリ : {category}")
    print(f"🔍 research_url       : {research_url or '(なし)'}")
    print(f"🖼  infographic_path   : {inf_path or '(なし)'}")
    print(f"🌐 infographic_url    : {infographic_url or '(なし)'}")
    print(f"📣 核心的メッセージ    : {(core_message[:120]+'...') if len(core_message)>120 else core_message or '(空)'}")
    print(f"🖊  エグゼクティブ要約  : {(executive_summary[:120]+'...') if len(executive_summary)>120 else executive_summary or '(空)'}")
    print(f"📚 関連書籍            : {related_books or '(空)'}")
    print(f"✅ アクション           : 1){action_a or '(空)'} / 2){action_b or '(空)'} / 3){action_c or '(空)'}")
    print(f"🧮 chatgpt_usaget      : {chatgpt_usaget}")
    try:
        print(f"💳 chatgpt_credit      : ${float(chatgpt_credit):.2f}")
    except Exception:
        print(f"💳 chatgpt_credit      : {chatgpt_credit}")
    print(f"🧮 claude_usaget       : {claude_usaget}")
    print(f"💳 claude_credit       : {claude_credit or '(不明)'}")
    print("==============================================\n")

    # >>> PATCH: Fix YAML front matter for Books note（既存維持）
    try:
        import re as _re, datetime as _dt
        from pathlib import Path as _P
        _vault = _P("/Users/seihoushouba/Documents/Oshomadesse-pc")
        _inbox = _vault / "100_Inbox"
        _today = _dt.datetime.now().strftime("%Y-%m-%d")
        _cands = sorted(_inbox.glob(f"Books-{_today}.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not _cands:
            _cands = sorted(_inbox.glob("Books-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if _cands:
            _note = _cands[0]
            _txt  = _note.read_text(encoding="utf-8")
            _rest = _re.sub(r'^\ufeff?\s*---\s*\n.*?\n---\s*\n', '', _txt, flags=_re.S)
            _fixed = '---\n' + 'tags: [books]\n' + '---\n' + _rest.lstrip()
            if _fixed != _txt:
                _note.write_text(_fixed, encoding="utf-8")
                print(f"🩹 Front matter fixed: {_note}")
    except Exception as _e:
        print(f"⚠️ front matter fix skipped: {_e!r}")
    # <<< PATCH END

    # >>> PATCH: persist remaining credits into .env（既存維持）
    try:
        import shutil, re, datetime as _dt2
        from pathlib import Path as _P
        _env_path = _P(__file__).resolve().parent / ".env"
        _lines = _env_path.read_text(encoding="utf-8").splitlines() if _env_path.exists() else []

        def _set_kv(_lines, _key, _val):
            _pat = re.compile(rf"^\s*{re.escape(_key)}\s*=")
            _out, _found = [], False
            for _ln in _lines:
                if _pat.match(_ln):
                    _out.append(f"{_key}={_val}"); _found = True
                else:
                    _out.append(_ln)
            if not _found:
                _out.append(f"{_key}={_val}")
            return _out

        def _to_float(x):
            try:
                s = str(x).strip()
                s = s.replace(",", "")
                if s.startswith("$"):
                    s = s[1:]
                return float(s)
            except Exception:
                return None

        _cg = _to_float(chatgpt_credit)
        _cl = _to_float(claude_credit if claude_credit not in (None, "(不明)") else "")

        _changed = False
        if _cg is not None:
            _lines = _set_kv(_lines, "CHATGPT_START_CREDIT", f"{_cg:.6f}"); _changed = True
        if _cl is not None:
            _lines = _set_kv(_lines, "CLAUDE_START_CREDIT", f"{_cl:.6f}"); _changed = True

        if _changed:
            _ts = _dt2.datetime.now().strftime("%Y%m%d_%H%M%S")
            if _env_path.exists():
                shutil.copyfile(str(_env_path), str(_env_path)+f".bak_{_ts}")
            _env_path.write_text("\n".join(_lines) + "\n", encoding="utf-8")
            print(f"📝 .env 更新: CHATGPT_START_CREDIT={_cg if _cg is not None else '(nochange)'} / CLAUDE_START_CREDIT={_cl if _cl is not None else '(nochange)'} (path={_env_path})")
    except Exception as _e:
        print(f"⚠️ .env更新スキップ: {_e!r}")
    # <<< PATCH END

    return {
        "title": title,
        "author": author,
        "category": category,
        "research_url": research_url,
        "infographic_path": inf_path,
        "infographic_url": infographic_url,
        "core_message": core_message,
        "executive_summary": executive_summary,
        "related_books": related_books,
        "action_a": action_a, "action_b": action_b, "action_c": action_c,
        "chatgpt_usaget": chatgpt_usaget,
        "chatgpt_credit": chatgpt_credit,
        "claude_usaget": claude_usaget,
        "claude_credit": claude_credit,
    }
# ============ Step7: Obsidianノート生成（指定フォーマットに完全準拠） ============
def step7_save_to_obsidian_simple(mid_summary):
    """
    Step7: Step6で集めた変数を、指定の「ノート構成」テンプレに流し込んで 100_Inbox に保存。
    ファイル名: Books-YYYY-MM-DD.md
    """
    # JST対応: GitHub Actions (UTC) でも日本時間の日付にする
    date_str = (datetime.now() + timedelta(hours=9)).strftime("%Y-%m-%d")
    note_path = INBOX_DIR / f"Books-{date_str}.md"

    g = lambda k: (mid_summary.get(k) if isinstance(mid_summary, dict) else "")
    title = g("title") or ""
    author = g("author") or ""
    category = g("category") or ""
    infographic_url = g("infographic_url") or ""
    research_url = g("research_url") or ""
    action_a = g("action_a") or ""
    action_b = g("action_b") or ""
    action_c = g("action_c") or ""
    core_message = g("core_message") or ""
    executive_summary = g("executive_summary") or ""
    related_books = g("related_books") or ""
    chatgpt_usaget = g("chatgpt_usaget") or 0
    chatgpt_credit = g("chatgpt_credit") or 0
    claude_usaget = g("claude_usaget") or 0
    claude_credit = g("claude_credit") or ""

    # 表示用クレジット（$xx.xx）
    try:
        chatgpt_credit_display = f"${float(chatgpt_credit):.2f}"
    except Exception:
        chatgpt_credit_display = str(chatgpt_credit)
    claude_credit_display = (claude_credit or "").strip()
    if claude_credit_display and not claude_credit_display.startswith("$"):
        try:
            claude_credit_display = f"${float(claude_credit_display):.2f}"
        except Exception:
            pass

    # 関連書籍を箇条書きへ正規化（" / " も改行も両対応、既存ハイフンは重複回避）
    def _to_bullets(s: str) -> str:
        raw = (s or "").strip()
        if not raw:
            return "- なし"
        if "\n" in raw:
            parts = [ln.strip() for ln in raw.splitlines()]
        elif " / " in raw:
            parts = [p.strip() for p in raw.split(" / ")]
        else:
            parts = [raw]
        out_lines = []
        for p in parts:
            if not p:
                continue
            if p[0] in "-*•・":
                p = p[1:].lstrip()
            out_lines.append(f"- {p}")
        return "\n".join(out_lines)

    related_books_md = _to_bullets(related_books)

    # ノート本文（指定フォーマット：フロントマター厳守、リンク化、1行コードフェンス）
    content = f"""---
tags: [books]
---
## 【 🧠 {title} 】

### 📚 基本情報 
- 👤 著者:{author}
- 🏷️ カテゴリー: [[{category}]]

### 🎨 生成コンテンツ
- 🖼️ インフォグラフィック: [{title}]({infographic_url})
- 🔍 リサーチレポート: [{title}]({research_url})

### ✅ 今日できるアクション 
- [ ] {action_a}
- [ ] {action_b}
- [ ] {action_c}
  
### 🗣️ 要約
- 📣 核心的メッセージ
```
{core_message}
```

- 🖊️ エグゼクティブ・サマリー
```
{executive_summary}
```


### 📚 関連書籍
{related_books_md}


""".rstrip() + "\n"

    try:
        note_path.write_text(content, encoding="utf-8")

        # >>> PATCH: Booksノートのフロントマターを Research と同形式で強制
        try:
            import re as _re, datetime as _dt
            from pathlib import Path as _P
            _vault = _P("/Users/seihoushouba/Documents/Oshomadesse-pc")
            _inbox = _vault / "100_Inbox"
            _today = _dt.datetime.now().strftime("%Y-%m-%d")
            # 生成対象（今日の Books-YYYY-MM-DD.md を優先、無ければ最新の Books-*.md）
            _cands = sorted(_inbox.glob(f"Books-{_today}.md"), key=lambda q: q.stat().st_mtime, reverse=True)
            if not _cands:
                _cands = sorted((_inbox.glob("Books-*.md")), key=lambda q: q.stat().st_mtime, reverse=True)
            if _cands:
                _note = _cands[0]
                _txt  = _note.read_text(encoding="utf-8")
                # 既存のフロントマター（--- ... ---）を剥がす
                _body = _re.sub(r'^\ufeff?\s*---\s*\n.*?\n---\s*\n', '', _txt, flags=_re.S)
                # Research と同じスタイル: 行配列→join で確実に整形
                _head = ["---", "tags: [books]", "---"]
                _fixed = "\n".join(_head) + "\n" + _body.lstrip()
                if _fixed != _txt:
                    _note.write_text(_fixed, encoding="utf-8")
                    print(f"🩹 Books front matter normalized -> {_note.name}")
        except Exception as _e:
            print(f"⚠️ Books front matter normalize skipped: {_e!r}")
        # <<< PATCH END
        print(f"✅ Step7: ノート生成完了 -> {note_path}")
        return {"success": True, "saved_path": str(note_path)}
    except Exception as e:
        print("❌ Step7: ノート生成に失敗しました:", e)
        return {"success": False, "error": str(e)}
# ============ Step8: link_books.py を実行（通知前のフック） ============
def step8_run_list_py(mid_summary=None):
    """
    Step8: link_books.py を実行（通知前のフック）。
    """
    print("=========== Step8: link_books.py 実行 ===========")
    try:
        import subprocess, sys
        env = os.environ.copy()
        env["VAULT_ROOT"] = str(VAULT_ROOT)

        # 非AIロジックのみ（正規表現リンク）
        script1 = str(Path(PROJECT_DIR) / "link_books.py")
        r1 = subprocess.run([sys.executable, script1], env=env, capture_output=True, text=True)
        if (r1.stdout or "").strip():
            print((r1.stdout or "").strip())
        if r1.returncode != 0:
            print(f"⚠️ Step8: link_books.py returncode={r1.returncode}")
            if (r1.stderr or "").strip():
                print((r1.stderr or "").strip())
        else:
            print("✅ Step8: link_books.py 実行 OK")

        return {
            "success": (r1.returncode == 0),
            "stdout": (r1.stdout or ""),
            "stderr": (r1.stderr or ""),
            "rc1": r1.returncode,
        }
    except Exception as e:
        print(f"⚠️ Step8: 例外発生: {e}")
        return {"success": False, "error": str(e)}

# ============ Step9: 選定した本を除外本リストに追加 ============
def step8_append_to_excluded_list(mid_summary):
    """
    Step9: Step8完了後、選定した本を除外本スプレッドシートに追記する。
    A:D = [YYYY-MM-DD(今日), title, author, category]
    値は Step6 の mid_summary をそのまま使用（必要最小限）。
    優先順:
      1) sheets_connector に append系があれば使用
      2) EXCLUDED_APPEND_WEBHOOK があれば POST
      3) gspread（サービスアカウント設定がある場合のみ）
    """
    print("=========== Step9: 除外本リストへ追記 ===========")
    try:
        import json as _json

        # 今日（Asia/Tokyo想定: UTC+9）
        date_str = (datetime.now() + timedelta(hours=9)).strftime("%Y-%m-%d")

        # Step6のサマリをそのまま利用
        g = (lambda k: (mid_summary.get(k) if isinstance(mid_summary, dict) else ""))  # 既存の書き方に合わせる
        title = str(g("title") or "").strip()
        author = str(g("author") or "").strip()
        category = str(g("category") or "").strip()

        # 最小の整形（必要性が高いもののみ）
        title = title.replace("【", "").replace("】", "").strip()
        category = category.replace("[[", "").replace("]]", "").strip()

        row = [date_str, title, author, category]
        print(f"📝 追記行: {row}")

        # 1) sheets_connector が append系を持っていればそれを使用
        if sheets_connector:
            for fn in ("append_excluded_row", "append_excluded_book", "append_excluded_books"):
                if hasattr(sheets_connector, fn):
                    try:
                        getattr(sheets_connector, fn)(row)
                        print(f"✅ Step9: sheets_connector.{fn} で追記に成功")
                        return {"success": True, "method": f"sheets_connector.{fn}", "row": row}
                    except TypeError:
                        # (date,title,author,category) 形式の可能性
                        try:
                            getattr(sheets_connector, fn)(date_str, title, author, category)
                            print(f"✅ Step9: sheets_connector.{fn}(4args) で追記に成功")
                            return {"success": True, "method": f"sheets_connector.{fn}(4args)", "row": row}
                        except Exception as e:
                            print(f"⚠ sheets_connector.{fn} 呼び出し失敗: {e}")
                    except Exception as e:
                        print(f"⚠ sheets_connector.{fn} 失敗: {e}")

        # 2) Webhook（Apps Script など）
        webhook = os.getenv("EXCLUDED_APPEND_WEBHOOK", "").strip()
        if webhook:
            try:
                try:
                    import requests
                    r = requests.post(webhook, json={"date": date_str, "title": title, "author": author, "category": category}, timeout=12)
                    ok = bool(getattr(r, "ok", False))
                    status = getattr(r, "status_code", None)
                    text = getattr(r, "text", "")
                except Exception:
                    import urllib.request, json as _j
                    req = urllib.request.Request(webhook, data=_j.dumps({"date": date_str, "title": title, "author": author, "category": category}).encode("utf-8"),
                                                 headers={"Content-Type":"application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        status = resp.getcode()
                        text = resp.read().decode("utf-8", "ignore")
                        ok = 200 <= status < 300
                if ok:
                    print("✅ Step9: Webhook で追記に成功")
                    return {"success": True, "method": "webhook", "row": row, "status": status}
                else:
                    print(f"⚠ Webhook 応答エラー: {status} {text[:200]}")
            except Exception as we:
                print(f"⚠ Webhook 送信に失敗: {we}")

        # 3) gspread フォールバック
        try:
            import gspread
            from google.oauth2.service_account import Credentials as _Creds
            _SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
            # 認証（環境変数に応じて3通り）
            creds = None
            json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
            json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "").strip()
            gac_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

            if json_str:
                print(f"DEBUG: GOOGLE_SERVICE_ACCOUNT_JSON is set (len={len(json_str)})")
                creds = _Creds.from_service_account_info(_json.loads(json_str), scopes=_SCOPES)
            elif json_path and os.path.exists(json_path):
                print(f"DEBUG: GOOGLE_SERVICE_ACCOUNT_JSON_PATH is set: {json_path}")
                creds = _Creds.from_service_account_file(json_path, scopes=_SCOPES)
            elif gac_path and os.path.exists(gac_path):
                # GOOGLE_APPLICATION_CREDENTIALS がある場合はそれを明示的に使う
                try:
                    size = os.path.getsize(gac_path)
                    with open(gac_path, 'r') as f:
                        head = f.read(20)
                    print(f"DEBUG: GOOGLE_APPLICATION_CREDENTIALS found: {gac_path} (size={size} bytes, head={head!r})")
                except Exception as e:
                    print(f"DEBUG: Failed to inspect GAC file: {e}")
                creds = _Creds.from_service_account_file(gac_path, scopes=_SCOPES)
            else:
                try:
                    import google.auth
                    creds, _ = google.auth.default(scopes=_SCOPES)
                except Exception:
                    creds = None

            if creds is None:
                raise RuntimeError("サービスアカウント認証情報が見つかりません（GOOGLE_SERVICE_ACCOUNT_JSON / _PATH / GOOGLE_APPLICATION_CREDENTIALS を設定してください）")

            SPREADSHEET_ID = os.getenv("EXCLUDED_SHEET_ID", "1aZ9VkAE3ZMfc6tkwfVPjolMZ4DU6SwodBUc2Yd13R10")
            SHEET_GID = int(os.getenv("EXCLUDED_SHEET_GID", "638408503"))

            gc = gspread.authorize(creds)
            sh = gc.open_by_key(SPREADSHEET_ID)
            ws = sh.get_worksheet_by_id(SHEET_GID)
            if ws is None:
                raise RuntimeError(f"gid={SHEET_GID} のワークシートが見つかりません")
            ws.append_row(row, value_input_option="USER_ENTERED")
            print("✅ Step9: gspread で追記に成功")
            return {"success": True, "method": "gspread", "row": row}
        except Exception as ge:
            print("❌ Step9: gspread での追記に失敗:", ge)
            return {"success": False, "error": str(ge), "row": row}

    except Exception as e:
        print("❌ Step9: 例外発生:", e)
        return {"success": False, "error": str(e)}
# ============ Step10: step10完了通知をユーザーへ送信 ============
import os, glob
from pathlib import Path as _Path
from urllib.parse import quote as _quote

def _build_obsidian_note_url(note_path: str) -> str:
    """
    Booksノートの絶対パス → obsidian://open?vault=...&file=... を返す
    """
    # if os.getenv("GITHUB_ACTIONS"):
    #     # GitHub Actions環境ではGitHubのリポジトリURLを返す（簡易実装）
    #     repo = os.getenv("GITHUB_REPOSITORY", "oshomadesse/books-summary")
    #     # note_path は絶対パスなので、リポジトリルートからの相対パスを取得
    #     try:
    #         rel_path = _Path(note_path).relative_to(Path(PROJECT_DIR)).as_posix()
    #         # URLエンコード
    #         rel_path_enc = _quote(rel_path)
    #         return f"https://github.com/{repo}/blob/main/{rel_path_enc}"
    #     except Exception:
    #         return f"https://github.com/{repo}"

    vault_root = _Path(os.getenv("VAULT_ROOT", "/Users/seihoushouba/Documents/Oshomadesse-pc")).resolve()
    vault_name = os.getenv("OBSIDIAN_VAULT_NAME", "Oshomadesse-main")
    note_abs = _Path(note_path).resolve()
    try:
        rel = note_abs.relative_to(vault_root).as_posix()
    except ValueError:
        # vault_root外にある場合はファイル名のみ
        rel = note_abs.name
    return f"obsidian://open?vault={_quote(vault_name)}&file={_quote(rel)}"

def _find_latest_books_note() -> str|None:
    """
    100_Inbox 内の Books-*.md のうち最終更新が最新の1件を返す
    """
    # グローバル変数の INBOX_DIR を使用する（CI環境では artifacts を指している）
    inbox = INBOX_DIR
    files = glob.glob(str(_Path(inbox) / "Books-*.md"))
    if not files:
        return None
    return max(files, key=lambda p: _Path(p).stat().st_mtime)

def step9_send_notification_to_user(mid_summary=None):
    """
    Step10: Step9完了後に、LINE Messaging APIで通知を送信する。
    Flex Message形式（novelist-interview準拠）で送信。
    """
    try:
        from line_messaging import line_push_text, line_push_flex
    except Exception as e:
        print(f"Step10: LINE通知スキップ（line_messaging未導入）: {e}")
        return

    note_path = _find_latest_books_note()
    if not note_path:
        print("Step10: Booksノートが見つからず通知スキップ")
        return

    url = _build_obsidian_note_url(note_path)
    
    # mid_summary がない場合は従来のテキスト通知
    if not mid_summary:
        msg = f"📚 本日の読書本はこちら！\n{url}"
        r = line_push_text(msg)
        if not r.get("ok"):
            print(f"❌ Step10: LINE通知エラー: {r}")
        else:
            print("✅ Step10: LINE通知送信 OK")
        return

    # Flex Message 構築
    title = mid_summary.get("title", "No Title")
    author = mid_summary.get("author", "")
    core_message = mid_summary.get("core_message", "")
    # 核心的メッセージを短く切り詰める
    if len(core_message) > 60:
        core_message = core_message[:60] + "..."
    
    # インフォグラフィックURLは Step5 -> Step6 で生成済み
    infographic_pages_url = mid_summary.get("infographic_url", "")

    # ★ User Request: 通知が来たら確実に開けるように、デプロイ完了（200 OK）を待つ
    if infographic_pages_url and infographic_pages_url.startswith("http"):
        print(f"⏳ Step10: GitHub Pagesの反映を待機中... ({infographic_pages_url})")
        import time
        import urllib.request
        # max_wait = 180  # 最大3分待機
        try:
            max_wait = int(os.getenv("PUBLIC_PAGES_WAIT_TIMEOUT", "180"))
        except Exception:
            max_wait = 180
        print(f"⏳ Step10: GitHub Pagesの反映を待機中... (timeout={max_wait}s)")
        
        start_time = time.time()
        
        while True:
            try:
                # HEADリクエストで確認
                req = urllib.request.Request(infographic_pages_url, method='HEAD')
                with urllib.request.urlopen(req, timeout=5) as response:
                    if 200 <= response.status < 300:
                        print(f"✅ GitHub Pages 反映確認完了 ({time.time() - start_time:.1f}s)")
                        break
            except Exception:
                pass
            
            if time.time() - start_time > max_wait:
                print("⚠️ Pages反映待ちタイムアウト（通知を送信します）")
                break
            
            time.sleep(10)  # 10秒間隔で確認

    # ヒーロー画像: 削除
    # hero_url = "https://via.placeholder.com/1024x500?text=Books+Summary"
    
    alt_text = f"📚 本日の読書本はこちら！: {title}"
    
    flex_obj = {
      "type": "bubble",
      "header": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "text",
            "text": "📚 本日の読書本はこちら！",
            "weight": "bold",
            "color": "#000000",
            "size": "sm"
          }
        ]
      },
      # hero ブロック削除
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "text",
            "text": title,
            "weight": "bold",
            "size": "xl",
            "wrap": True
          },
          {
            "type": "text",
            "text": author,
            "size": "sm",
            "color": "#666666",
            "wrap": True,
            "margin": "sm"
          },
          {
            "type": "text",
            "text": core_message,
            "size": "sm",
            "color": "#666666",
            "wrap": True,
            "margin": "md"
          }
        ]
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "action": {
              "type": "uri",
              "label": "図解を見る",
              "uri": infographic_pages_url if infographic_pages_url else url
            }
          }
        ],
        "flex": 0
      }
    }
    
    # インフォグラフィックボタン追加ロジック削除

    r = line_push_flex(flex_obj, alt_text=alt_text)
    if not r.get("ok"):
        print(f"❌ Step10: LINE Flex通知エラー: {r}")
    else:
        print("✅ Step10: LINE Flex通知送信 OK")


# ============ 実行制御 ============
def run_until(step):
    usage_records = {}
    excl = step1_get_excluded_books()
    if step == 1:
        return
    recs = step2_generate_recommendations(excl, usage_records)
    if step == 2:
        return
    sel = step3_select_book(recs)
    if step == 3:
        return
    deep = step4_deep_research(sel, usage_records)
    if step == 4:
        return
    # ① Step4の出力が薄いとき（<=1000 chars）はここで停止してクレジット節約
    try:
        deep_len = len(str(deep.get("raw") or "")) if isinstance(deep, dict) else len(str(deep or ""))
    except Exception:
        deep_len = len(json.dumps(deep, ensure_ascii=False))
    print(f"ℹ️ Deep Research length check (post-Step4): {deep_len} chars")
    if deep_len <= 1000:
        print("⚠️ Step4 Deep Research が 1000 chars 以下のため処理を停止します（Step5以降は実行しません）。")
        return

    infopath = step5_generate_infographic(deep, sel, usage_records)
    if step == 5:
        return
    mid = step6_mid_summary(sel, deep, infopath)
    if step == 6:
        return
    step7_save_to_obsidian_simple(mid)
    # Step7 の直後に Step8（list.py）を実行 → Step9（除外リスト追記）
    step8_run_list_py(mid)
    step8_append_to_excluded_list(mid)

    step9_send_notification_to_user(mid)

    # 計測終了: GitHub Actions開始からの経過時間を表示
    start_ts = os.getenv("WORKFLOW_START_TIME")
    if start_ts and start_ts.isdigit():
        try:
            import time
            now_ts = int(time.time())
            diff = now_ts - int(start_ts)
            print(f"⏱️ Total duration from workflow start to final notification: {diff} seconds")
        except Exception as e:
            print(f"⚠️ Time measurement failed: {e}")
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--diag", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--logfile", type=str, default=None)
    parser.add_argument("--until", type=int, default=10, help="指定ステップまで実行（デフォルト10）")
    args = parser.parse_args()
    # ログファイル設定（デフォルト）
    if not args.logfile:
        import datetime as dt
        today = dt.date.today().strftime("%Y%m%d")
        log_dir = os.path.join(PROJECT_DIR, "data", "integrated")
        os.makedirs(log_dir, exist_ok=True)
        args.logfile = os.path.join(log_dir, f"integrated_run_{today}.log")

    if args.logfile:
        os.environ["IRW_LOGFILE"] = args.logfile
        def _make_printer2(logfile):
            import builtins as _bi
            def _p(*a, **k):
                _bi.print(*a, **k)
                try:
                    with open(logfile, "a", encoding="utf-8") as fp:
                        _bi.print(*a, **k, file=fp)
                except Exception:
                    pass
            return _p
        globals()["print"] = _make_printer2(os.environ["IRW_LOGFILE"])
        print("Logging to: " + os.environ["IRW_LOGFILE"])
    if args.diag:
        try:
            step0_diag_env(probe=args.probe, model_hint=os.getenv("GPT5_MODEL","gpt-5"))
        except Exception as _e:
            print("診断で例外: " + str(_e))
        sys.exit(0)
    try:
        run_until(args.until)
    except Exception as e:
        print("\n❌ 実行時エラー")
        print("種類:", type(e).__name__)
        print("内容:", e)
        traceback.print_exc()
        # エラー時にLINE通知を試みる
        try:
            from line_messaging import line_push_text
            error_msg = f"📚 読書ワークフロー失敗\n種類: {type(e).__name__}\n内容: {e}"
            line_push_text(error_msg[:500])
        except Exception:
            pass
