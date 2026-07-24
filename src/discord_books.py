#!/usr/bin/env python3
"""books-summary 固有の Discord 書籍深掘り interaction ハンドラ。"""

import asyncio
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOKS_PATH = ROOT / "state" / "books_read.json"
VAULT_INBOX = Path("/Users/seihoushouba/Oshomadesse-pc/100_Inbox")


def modal_question(d: dict) -> str:
    """Modal の components から question の入力値を取り出す。"""
    for row in d.get("data", {}).get("components", []):
        for component in row.get("components", []):
            if component.get("custom_id") == "question":
                return str(component.get("value", "")).strip()
    return ""


def load_entry(date: str) -> dict | None:
    """books_read.json から date に対応する書籍情報を返す。"""
    entries = json.loads(BOOKS_PATH.read_text(encoding="utf-8"))
    for entry in entries:
        if str(entry.get("date", "")) == date:
            return entry
    return None


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


async def handle_interaction(d, context) -> bool:
    """書籍深掘り component / Modal を処理した時だけ True を返す。"""
    interaction_type = d.get("type")
    custom_id = d.get("data", {}).get("custom_id", "")
    api_call = context["api_call"]
    token = context["token"]
    log = context["log"]
    iid, itoken = d.get("id"), d.get("token")

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
