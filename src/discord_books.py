#!/usr/bin/env python3
"""books-summary 固有の Discord 書籍深掘り interaction ハンドラ。"""

import asyncio
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOKS_PATH = ROOT / "state" / "books_read.json"
LATEST_PATH = ROOT / "state" / "latest.json"
RAW_LATEST_URL = "https://raw.githubusercontent.com/oshomadesse/books-summary/main/state/latest.json"
VAULT_INBOX = Path("/Users/seihoushouba/Oshomadesse-pc/100_Inbox")
INFOGRAPHIC_RE = re.compile(
    r"^.*インフォグラフィック.*?\[[^\]]*\]\((https?://[^)\s]+)\)",
    re.MULTILINE,
)
H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def modal_value(d: dict, custom_id: str) -> str:
    """Modal の components から指定した入力値を取り出す。"""
    for row in d.get("data", {}).get("components", []):
        for component in row.get("components", []):
            if component.get("custom_id") == custom_id:
                return str(component.get("value", "")).strip()
    return ""


def modal_question(d: dict) -> str:
    """Modal の components から question の入力値を取り出す。"""
    return modal_value(d, "question")


def load_entry(date: str) -> dict | None:
    """books_read.json から date に対応する書籍情報を返す。"""
    entries = json.loads(BOOKS_PATH.read_text(encoding="utf-8"))
    for entry in entries:
        if str(entry.get("date", "")) == date:
            return entry
    return None


def is_dataless(path: Path) -> bool:
    result = subprocess.run(
        ["ls", "-lO", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return "dataless" in result.stdout


def hydrate(path: Path) -> bool:
    """iCloud 上のノートを launchd から読める状態にする。"""
    if not path.is_file():
        return True
    subprocess.run(
        ["/usr/bin/brctl", "download", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if not is_dataless(path):
        return True
    for _ in range(12):
        time.sleep(5)
        if not is_dataless(path):
            return True
    return False


def note_metadata(date: str) -> tuple[str, str]:
    """ノートから書名とインフォグラフィック URL を返す。"""
    path = VAULT_INBOX / f"Books-{date}.md"
    if not path.is_file() or not hydrate(path):
        return "", ""
    text = path.read_text(encoding="utf-8")
    h2_match = H2_RE.search(text)
    title = h2_match.group(1).strip() if h2_match else ""
    if title.startswith("【") and title.endswith("】"):
        title = title[1:-1].strip()
    title = title.removeprefix("🧠").strip()
    url_match = INFOGRAPHIC_RE.search(text)
    return title, url_match.group(1) if url_match else ""


def remote_latest(date: str) -> tuple[str, str]:
    """リモートの latest.json から書名と図解 URL を返す。"""
    try:
        with urllib.request.urlopen(RAW_LATEST_URL, timeout=2.5) as response:
            data = json.load(response)
        if str(data.get("date", "")) == date:
            title = str(data.get("title", "")).strip()
            url = str(data.get("infographic_url", "")).strip()
            return title, url
    except Exception:
        pass
    return "", ""


def confirm_details(date: str) -> tuple[str, str]:
    """確認応答に使う書名と図解 URL を解決する。"""
    try:
        entry = load_entry(date)
    except (OSError, ValueError):
        entry = None
    title = str(entry.get("title", "")).strip() if entry else ""
    url = ""
    try:
        latest = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        if str(latest.get("date", "")) == date:
            url = str(latest.get("infographic_url", "")).strip()
    except (OSError, ValueError):
        pass
    if not url or not title:
        note_title, note_url = note_metadata(date)
        title = title or note_title
        url = url or note_url
    if not url or not title:
        remote_title, remote_url = remote_latest(date)
        title = title or remote_title
        url = url or remote_url
    return title, url


def build_prompt(date: str, entry: dict, question: str) -> str:
    """書籍台帳と質問から NEXUS チャットへ渡すプロンプトを組み立てる。"""
    note_path = VAULT_INBOX / f"Books-{date}.md"
    lines = [
        "Discord の 🔍詳細ボタンから来た書籍深掘りリクエストや。",
        f"書名: {entry.get('title', '')}",
        f"著者: {entry.get('author', '')}",
        f"カテゴリ: {entry.get('category', '')}",
        f"date: {date}",
        f"ノートパス: {note_path}",
        f"ノート（{note_path}）が存在しなければ bash ~/.local/bin/books-summary-pull.sh を先に走らせてから読むこと。",
        "このノートを読んでから答えること。ノート内にインフォグラフィックのURLもある。",
    ]
    if question:
        lines.append(f"しょーまの質問:「{question}」。これに最優先で答える。")
    else:
        lines.append(
            "しょーまの質問:「お任せ: 本の要点と、しょーまの生活にどう効くかを解説して」"
        )
    lines.append("返答はスマホ幅前提の Discord 書式（vault ルート CLAUDE.md の規定）で。")
    return "\n".join(lines)


def build_search_prompt(query: str) -> str:
    """検索リクエストを NEXUS チャットへ渡すプロンプトにする。"""
    return "\n".join([
        "Discord の常設「検索」メニューから来た本の検索・リクエストや。",
        f"しょーまの検索/リクエスト文:「{query}」",
        "次の手順で答えること。",
        f"① まず {BOOKS_PATH} を照合する。該当・関連する既読本があれば、その本のノート "
        f"{VAULT_INBOX}/Books-YYYY-MM-DD.md を読んで、要点＋図解URL＋ノートへの言及を提出する。",
        "② 既読に無ければ Web で該当書籍を特定・提案する。最大3冊、最有力を先頭にし、"
        "「明日の一冊として読むなら .claude/skills/daily-reading/SKILL.md の選書に指名を入れるで」と選択肢を出す。",
        "返答はスマホ幅前提の Discord 書式（vault ルート CLAUDE.md の規定）で。",
    ])


def menu_payload() -> dict:
    return {
        "content": "ーーーーーーー",
        "components": [{
            "type": 1,
            "components": [{
                "type": 2,
                "style": 2,
                "label": "🔎 検索",
                "custom_id": "booksearch",
            }],
        }],
    }


async def callback(d, context, payload) -> None:
    await asyncio.to_thread(
        context["api_call"],
        "POST",
        f"/interactions/{d['id']}/{d['token']}/callback",
        context["token"],
        payload,
    )


async def handle_interaction(d, context) -> bool:
    """書籍深掘り component / Modal を処理した時だけ True を返す。"""
    interaction_type = d.get("type")
    custom_id = d.get("data", {}).get("custom_id", "")
    api_call = context["api_call"]
    token = context["token"]
    log = context["log"]
    iid, itoken = d.get("id"), d.get("token")

    if interaction_type == 3 and custom_id.startswith("bookconfirm:"):
        date = custom_id.removeprefix("bookconfirm:")
        if not date:
            return False
        title, url = await asyncio.to_thread(confirm_details, date)
        content = (
            f"📖 『{title}』の図解はこちら → {url}"
            if url
            else "図解のURLが見つからんかった"
        )
        await callback(
            d,
            context,
            {"type": 4, "data": {"content": content, "flags": 64}},
        )
        log(f"書籍確認 応答: {date}")
        await context["repost_menu"]()
        return True

    if interaction_type == 3 and custom_id == "booksearch":
        await callback(
            d,
            context,
            {
                "type": 9,
                "data": {
                    "custom_id": "booksearchmodal",
                    "title": "本を探す・リクエストする",
                    "components": [{
                        "type": 1,
                        "components": [{
                            "type": 4,
                            "style": 2,
                            "custom_id": "query",
                            "label": "特定の本やテーマを伝える",
                            "required": True,
                            "max_length": 1000,
                            "placeholder": "例: アドラー心理学の本 / 嫌われる勇気 / 交渉術のおすすめ",
                        }],
                    }],
                },
            },
        )
        log("書籍検索 Modal 提示")
        return True

    if interaction_type == 5 and custom_id == "booksearchmodal":
        query = modal_value(d, "query")
        await callback(
            d,
            context,
            {"type": 4, "data": {"content": "🔎 探すで。ちょい待ち"}},
        )
        await context["repost_menu"]()
        asyncio.create_task(context["chat"](build_search_prompt(query)))
        log("書籍検索 チャット注入")
        return True

    if interaction_type == 3 and custom_id.startswith("bookdetail:"):
        date = custom_id.removeprefix("bookdetail:")
        if not date:
            return False
        await asyncio.to_thread(
            api_call,
            "POST",
            f"/interactions/{iid}/{itoken}/callback",
            token,
            {
                "type": 9,
                "data": {
                    "custom_id": f"bookmodal:{date}",
                    "title": "今日の本を深掘る",
                    "components": [{
                        "type": 1,
                        "components": [{
                            "type": 4,
                            "style": 2,
                            "custom_id": "question",
                            "label": "聞きたいこと・気になった点",
                            "required": False,
                            "max_length": 1000,
                            "placeholder": "空欄ならお任せで要点を解説するで",
                        }],
                    }],
                },
            },
        )
        log(f"書籍深掘り Modal 提示: {date}")
        return True

    if interaction_type == 5 and custom_id.startswith("bookmodal:"):
        date = custom_id.removeprefix("bookmodal:")
        if not date:
            return False
        question = modal_question(d)
        entry = await asyncio.to_thread(load_entry, date)
        if not entry:
            await asyncio.to_thread(
                api_call,
                "POST",
                f"/interactions/{iid}/{itoken}/callback",
                token,
                {
                    "type": 4,
                    "data": {
                        "content": "その日の記録が見つからんかった",
                        "flags": 64,
                    },
                },
            )
            log(f"書籍深掘り 台帳未検出: {date}")
            return True

        title = str(entry.get("title", ""))
        await asyncio.to_thread(
            api_call,
            "POST",
            f"/interactions/{iid}/{itoken}/callback",
            token,
            {
                "type": 4,
                "data": {
                    "content": f"🔍 『{title}』を深掘るで。ちょい待ち",
                },
            },
        )
        prompt = build_prompt(date, entry, question)
        asyncio.create_task(context["chat"](prompt))
        log(f"書籍深掘り チャット注入: {date}")
        return True

    return False
