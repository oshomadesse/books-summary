#!/usr/bin/env python3
"""最新ノートから参照された既読ノートへ逆リンクを追記する。"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LATEST = REPO / "state" / "latest.json"
INBOX = Path("/Users/seihoushouba/Oshomadesse-pc/100_Inbox")
RELATED_HEADING = "### 📚 関連書籍"


def log(message: str) -> None:
    print(f"[backlink_books] {message}", flush=True)


def is_dataless(path: Path) -> bool:
    result = subprocess.run(
        ["ls", "-lO", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return "dataless" in result.stdout


def hydrate(path: Path) -> bool:
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

    log(f"iCloud 退避を検出 → 実体を取り寄せ: {path}")
    for _ in range(12):
        time.sleep(5)
        if not is_dataless(path):
            return True
    log(f"iCloud 実体化がタイムアウト: {path}")
    return False


def add_backlink(path: Path, date: str, title: str, author: str) -> None:
    if not path.exists():
        log(f"対象ノートなし、skip: {path}")
        return
    if not hydrate(path):
        log(f"ハイドレーション失敗、skip: {path}")
        return

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == RELATED_HEADING)
    except StopIteration:
        start = -1

    if start >= 0:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")),
            len(lines),
        )
        if any(f"[[Books-{date}" in line for line in lines[start + 1 : end]):
            log(f"逆リンク済み、skip: {path.name}")
            return
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(RELATED_HEADING)
        insert_at = len(lines)

    link = f"- [[Books-{date}|{title}]]（{author}）: 関連書籍として本日のノートから参照"
    lines.insert(insert_at, link)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"逆リンク追記: {path.name}")


def main() -> int:
    try:
        if not hydrate(LATEST):
            return 0
        latest = json.loads(LATEST.read_text(encoding="utf-8"))
        related_dates = latest.get("related_dates") or []
        if not related_dates:
            log("related_dates が空のため処理なし")
            return 0

        date = str(latest["date"])
        title = str(latest["title"])
        author = str(latest["author"])
        for related_date in related_dates:
            try:
                target = INBOX / f"Books-{related_date}.md"
                add_backlink(target, date, title, author)
            except Exception as exc:  # 部分失敗で pull を止めない
                log(f"{related_date} の処理に失敗、skip: {exc}")
    except Exception as exc:
        log(f"全体処理に失敗: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
