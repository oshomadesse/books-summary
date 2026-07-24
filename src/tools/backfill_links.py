#!/usr/bin/env python3
"""過去の書籍ノートへ関連リンクと逆リンクを一括反映する道具。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INBOX = Path("/Users/seihoushouba/Oshomadesse-pc/100_Inbox")
DECISIONS_PATH = ROOT / "state" / "backfill" / "decisions.json"
BACKUP_ROOT = ROOT / "state" / "backfill" / "backup"

PUNCT_RE = re.compile(
    r"[\s\-‐‑–—―〜~:：、。・,.;/\\()（）\[\]{}【】「」『』\"'!！?？·•°]+"
)
EDITION_RE = re.compile(r"(新版|改訂|増補|決定版|図解|完全版|要約|入門)$")
TITLE_RE = re.compile(r"^##\s*【\s*(?P<title>.+?)\s*】\s*$")
AUTHOR_RE = re.compile(r"^\s*[-*]\s*[^\n]*?著者\s*[:：]\s*(?P<author>.+?)\s*$")
DATE_RE = re.compile(r"^Books-(\d{4}-\d{2}-\d{2})\.md$")
LINKED_TITLE_RE = re.compile(r"^Books-\d{4}-\d{2}-\d{2}")


def hydrate(path: Path) -> None:
    """iCloud の dataless ファイルを最大60秒待って実体化する。"""
    if not path.exists():
        return
    subprocess.run(
        ["/usr/bin/brctl", "download", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for attempt in range(13):
        result = subprocess.run(
            ["ls", "-lO", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if "dataless" not in result.stdout:
            return
        if attempt == 12:
            raise TimeoutError(f"iCloud hydration timeout: {path}")
        time.sleep(5)


def read_text(path: Path) -> str:
    hydrate(path)
    return path.read_text(encoding="utf-8")


def nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def norm_author(value: str) -> str:
    return PUNCT_RE.sub("", nfkc(value).lower())


def strip_subtitle(title: str) -> str:
    value = nfkc(title)
    for separator in [":", "：", "-", "ー", "—", "―", "–", "〜", "~"]:
        if separator in value:
            value = value.split(separator, 1)[0]
    value = re.sub(r"[（(].*?[）)]$", "", value)
    return EDITION_RE.sub("", value).strip()


def norm_title(value: str) -> str:
    return PUNCT_RE.sub("", strip_subtitle(value).lower())


def clean_title(value: str) -> str:
    """見出し先頭の絵文字・記号だけを除く。"""
    return re.sub(r"^[^\w]+", "", value, flags=re.UNICODE).strip()


def parse_note(path: Path) -> dict:
    text = read_text(path)
    lines = text.splitlines(keepends=True)
    title = ""
    author = ""
    for line in lines:
        title_match = TITLE_RE.match(line.rstrip("\r\n"))
        if title_match and not title:
            title = clean_title(title_match.group("title"))
        author_match = AUTHOR_RE.match(line.rstrip("\r\n"))
        if author_match and not author:
            author = author_match.group("author").strip()
        if title and author:
            break
    date_match = DATE_RE.match(path.name)
    return {
        "date": date_match.group(1) if date_match else "",
        "title": title,
        "author": author,
        "path": path,
        "text": text,
    }


def related_bounds(lines: list[str]) -> tuple[int | None, int | None]:
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("###") and "関連書籍" in line:
            start = index
            break
    if start is None:
        return None, None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].strip().startswith("### "):
            end = index
            break
    return start, end


def split_segments(line: str) -> list[str]:
    value = line.strip()
    if value.startswith(("-", "*")):
        value = value[1:].strip()
    return re.split(r"\s*/\s*", value) if value else []


def parse_segment(segment: str) -> tuple[str, str, str] | None:
    match = re.match(
        r"^(?P<title>.+?)[（(](?P<author>[^）)]+)[）)](?P<tail>\s*[:：].*)$",
        segment.strip(),
    )
    if not match:
        return None
    return (
        match.group("title").strip(),
        match.group("author").strip(),
        match.group("tail"),
    )


def is_linked_segment(segment: str) -> bool:
    if "[[" in segment or "]]" in segment:
        return True
    parsed = parse_segment(segment)
    return bool(parsed and LINKED_TITLE_RE.match(parsed[0]))


def related_entries(note: dict, stats: dict[str, int] | None = None) -> list[dict]:
    lines = note["text"].splitlines(keepends=True)
    start, end = related_bounds(lines)
    if start is None:
        return []
    entries = []
    for line in lines[start + 1:end]:
        if not line.lstrip().startswith(("-", "*")):
            continue
        for segment in split_segments(line):
            if is_linked_segment(segment):
                if stats is not None:
                    stats["linked_excluded"] = stats.get("linked_excluded", 0) + 1
                continue
            parsed = parse_segment(segment)
            if parsed:
                title, author, _ = parsed
                entries.append({"raw": segment.strip(), "title": title, "author": author})
    return entries


def classify(src_date: str, title: str, author: str, notes: list[dict]) -> dict:
    title_key = norm_title(title)
    author_key = norm_author(author)
    ranked = []
    for note in notes:
        if note["date"] == src_date or not note["title"]:
            continue
        candidate_title = norm_title(note["title"])
        candidate_author = norm_author(note["author"])
        score = SequenceMatcher(None, title_key, candidate_title).ratio()
        exact_title = bool(title_key) and title_key == candidate_title
        substring = bool(title_key and candidate_title) and (
            title_key in candidate_title or candidate_title in title_key
        )
        same_author = bool(author_key) and author_key == candidate_author
        if exact_title or (substring and same_author):
            level = 2
        elif score >= 0.55 or same_author:
            level = 1
        else:
            level = 0
        if level:
            ranked.append((level, score, note))

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]["date"]))
    if not ranked:
        return {"status": "nomatch", "match_date": None, "candidates": []}

    best_level, _, best = ranked[0]
    candidates = [
        {
            "date": note["date"],
            "title": note["title"],
            "author": note["author"],
            "score": round(score, 2),
        }
        for _, score, note in ranked[:5]
    ]
    return {
        "status": "auto" if best_level == 2 else "ambiguous",
        "match_date": best["date"],
        "candidates": candidates,
    }


def scan() -> None:
    notes = [parse_note(path) for path in sorted(INBOX.glob("Books-*.md"))]
    decisions = []
    stats = {"linked_excluded": 0}
    for note in notes:
        for entry in related_entries(note, stats):
            result = classify(note["date"], entry["title"], entry["author"], notes)
            decisions.append(
                {
                    "src_date": note["date"],
                    "raw": entry["raw"],
                    "title": entry["title"],
                    "author": entry["author"],
                    **result,
                }
            )

    payload = {"generated_from": "scan", "entries": decisions}
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    hydrate(DECISIONS_PATH)
    DECISIONS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = {
        status: sum(entry["status"] == status for entry in decisions)
        for status in ("auto", "ambiguous", "nomatch")
    }
    print(
        f"scan: notes={len(notes)} entries={len(decisions)} "
        f"linked_excluded={stats['linked_excluded']} "
        f"auto={counts['auto']} ambiguous={counts['ambiguous']} "
        f"nomatch={counts['nomatch']}"
    )


def replace_forward(text: str, entry: dict) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    start, end = related_bounds(lines)
    if start is None:
        return text, False
    for index in range(start + 1, end):
        if not lines[index].lstrip().startswith(("-", "*")):
            continue
        segments = split_segments(lines[index])
        changed = False
        for segment_index, segment in enumerate(segments):
            if segment.strip() != entry["raw"] or "[[" in segment:
                continue
            parsed = parse_segment(segment)
            if not parsed:
                continue
            title, author, tail = parsed
            segments[segment_index] = (
                f"[[Books-{entry['match_date']}|{title}]]（{author}）{tail}"
            )
            changed = True
            break
        if changed:
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = "- " + " / ".join(segments) + newline
            return "".join(lines), True
    return text, False


def add_reverse(text: str, src: dict) -> tuple[str, bool]:
    if f"[[Books-{src['date']}" in text:
        return text, False
    lines = text.splitlines(keepends=True)
    start, end = related_bounds(lines)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append("### 📚 関連書籍\n")
        end = len(lines)
    lines.insert(
        end,
        f"- [[Books-{src['date']}|{src['title']}]]（{src['author']}）: 逆リンク\n",
    )
    return "".join(lines), True


def apply(dry_run: bool) -> None:
    payload = json.loads(read_text(DECISIONS_PATH))
    selected = [
        entry
        for entry in payload.get("entries", [])
        if entry.get("status") in {"auto", "accepted"} and entry.get("match_date")
        and entry.get("match_date") != entry.get("src_date")
    ]
    dates = {
        str(entry[key])
        for entry in selected
        for key in ("src_date", "match_date")
    }
    notes = {}
    for date in sorted(dates):
        path = INBOX / f"Books-{date}.md"
        if path.exists():
            notes[date] = parse_note(path)

    changed: set[str] = set()
    forward_count = 0
    reverse_count = 0
    for entry in selected:
        src = notes.get(str(entry["src_date"]))
        target = notes.get(str(entry["match_date"]))
        if not src or not target:
            continue
        new_text, did_forward = replace_forward(src["text"], entry)
        if did_forward:
            src["text"] = new_text
            changed.add(src["date"])
            forward_count += 1
        if did_forward:
            new_text, did_reverse = add_reverse(target["text"], src)
            if did_reverse:
                target["text"] = new_text
                changed.add(target["date"])
                reverse_count += 1

    print(
        f"apply{' --dry-run' if dry_run else ''}: "
        f"forward={forward_count} reverse={reverse_count} files={len(changed)}"
    )
    if dry_run or not changed:
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = BACKUP_ROOT / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    for date in sorted(changed):
        source = notes[date]["path"]
        hydrate(source)
        shutil.copy2(source, backup_dir / source.name)
    for date in sorted(changed):
        path = notes[date]["path"]
        hydrate(path)
        path.write_text(notes[date]["text"], encoding="utf-8")
    print(f"backup: {backup_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="関連書籍を照合して decisions.json を生成")
    apply_parser = subparsers.add_parser("apply", help="承認済みリンクをノートへ反映")
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="変更予定だけを表示し、バックアップも書き込みも行わない",
    )
    args = parser.parse_args()
    if args.command == "scan":
        scan()
    else:
        apply(args.dry_run)


if __name__ == "__main__":
    main()
