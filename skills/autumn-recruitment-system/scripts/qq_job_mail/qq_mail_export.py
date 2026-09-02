from __future__ import annotations

import argparse
import base64
import ctypes
import email
import html
import imaplib
import json
import os
import re
import sys
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
MAX_MESSAGES_PER_RUN = 200
MAX_BODY_CHARS = 50000

JOB_KEYWORDS = (
    "招聘", "校招", "秋招", "春招", "职位", "岗位", "投递", "申请", "简历",
    "测评", "笔试", "面试", "录用", "拒绝", "未通过", "流程", "候选人",
    "宣讲", "offer", "candidate", "application", "interview", "assessment",
    "recruit", "recruitment", "career", "hiring", "job", "hr",
)

def _default_data_dir() -> Path:
    configured = os.environ.get("AUTUMN_RECRUITMENT_MAIL_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Codex" / "autumn-recruitment-system" / "qq_job_mail"


class RuntimePaths:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.state = self.data_dir / "state.json"
        self.pending = self.data_dir / "pending_mail.json"
        self.config = self.data_dir / "config.json"
        self.dpapi_secret = self.data_dir / "qq_auth.dpapi"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, encoding in decode_header(value):
        if isinstance(chunk, bytes):
            for candidate in (encoding, "utf-8", "gb18030", "latin-1"):
                if not candidate:
                    continue
                try:
                    parts.append(chunk.decode(candidate, errors="replace"))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset()
    for candidate in (charset, "utf-8", "gb18030", "latin-1"):
        if not candidate:
            continue
        try:
            return payload.decode(candidate, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(value)
        text = "\n".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_state(paths: RuntimePaths) -> dict:
    if not paths.state.exists():
        return {"last_committed_uid": 0}
    try:
        data = json.loads(paths.state.read_text(encoding="utf-8-sig"))
        return {"last_committed_uid": int(data.get("last_committed_uid", 0))}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"last_committed_uid": 0}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _dpapi_unprotect(encoded: str) -> str:
    if os.name != "nt":
        raise RuntimeError("Saved QQ credentials require Windows DPAPI; use environment variables on this system.")
    encrypted = base64.b64decode(encoded)
    buffer = ctypes.create_string_buffer(encrypted)
    in_blob = _DataBlob(len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw.decode("utf-8").strip()
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _credentials(paths: RuntimePaths) -> tuple[str, str]:
    address = os.environ.get("QQ_MAIL_ADDRESS", "").strip()
    auth_code = os.environ.get("QQ_MAIL_AUTH_CODE", "").strip()
    if not address or not auth_code:
        if not paths.config.exists() or not paths.dpapi_secret.exists():
            raise RuntimeError(
                "QQ Mail is not configured. Run setup_qq_credentials.ps1 or set "
                "QQ_MAIL_ADDRESS and QQ_MAIL_AUTH_CODE."
            )
        try:
            config = json.loads(paths.config.read_text(encoding="utf-8-sig"))
            address = str(config.get("email", "")).strip()
            auth_code = _dpapi_unprotect(
                paths.dpapi_secret.read_text(encoding="ascii").strip()
            )
        except Exception as exc:
            raise RuntimeError("Unable to decrypt the saved QQ Mail authorization code.") from exc
    if not address or not auth_code:
        raise RuntimeError("QQ 邮箱地址或授权码未配置。")
    return address, auth_code


def _open_mailbox(paths: RuntimePaths) -> imaplib.IMAP4_SSL:
    address, auth_code = _credentials(paths)
    client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    client.login(address, auth_code)
    status, _ = client.select("INBOX", readonly=True)
    if status != "OK":
        client.logout()
        raise RuntimeError("无法以只读方式打开 QQ 邮箱收件箱。")
    return client


def _parse_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        return value


def _extract_message(paths: RuntimePaths, uid: int, raw_message: bytes) -> dict:
    msg = email.message_from_bytes(raw_message)
    plain_parts: list[str] = []
    html_parts: list[str] = []

    # First pass reads only text. Attachment content is never saved locally.
    for part in msg.walk():
        content_type = part.get_content_type().lower()
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()

        if filename or "attachment" in disposition:
            continue

        if content_type == "text/plain":
            plain_parts.append(_decode_payload(part))
        elif content_type == "text/html":
            html_parts.append(_html_to_text(_decode_payload(part)))
        elif content_type == "text/calendar":
            plain_parts.append(_decode_payload(part))

    body = "\n\n".join(x.strip() for x in plain_parts if x.strip())
    if not body:
        body = "\n\n".join(x.strip() for x in html_parts if x.strip())
    body = re.sub(r"\n{3,}", "\n\n", body).strip()[:MAX_BODY_CHARS]

    subject = _decode_header(msg.get("Subject"))
    sender = _decode_header(msg.get("From"))
    searchable = f"{subject}\n{sender}\n{body}".lower()
    matched_keywords = sorted({keyword for keyword in JOB_KEYWORDS if keyword.lower() in searchable})
    is_job_related = bool(matched_keywords)

    attachments: list[dict] = []
    if is_job_related:
        for part in msg.walk():
            disposition = (part.get("Content-Disposition") or "").lower()
            filename = part.get_filename()
            if not filename and "attachment" not in disposition:
                continue
            if not filename:
                continue
            payload = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": _decode_header(filename),
                "content_type": part.get_content_type().lower(),
                "size": len(payload),
                "saved_path": None,
                "skipped_reason": "attachment_storage_disabled",
            })

    return {
        "uid": uid,
        "message_id": (msg.get("Message-ID") or "").strip(),
        "subject": subject,
        "from": sender,
        "to": _decode_header(msg.get("To")),
        "date": _parse_date(msg.get("Date")),
        "body": body,
        "attachments": attachments,
        "matched_keywords": matched_keywords,
        "is_job_related_candidate": is_job_related,
    }


def storage_report(paths: RuntimePaths) -> dict:
    categories = {
        "state": [paths.state],
        "pending": [paths.pending],
        "credentials": [paths.config, paths.dpapi_secret],
    }
    result: dict[str, dict] = {}
    total_files = 0
    total_bytes = 0
    for name, entries in categories.items():
        files = [entry for entry in entries if entry.is_file()]
        size = sum(entry.stat().st_size for entry in files)
        result[name] = {"files": len(files), "bytes": size}
        total_files += len(files)
        total_bytes += size
    return {
        "ok": True,
        "data_dir": str(paths.data_dir),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "categories": result,
    }


def test_login(paths: RuntimePaths) -> int:
    client = _open_mailbox(paths)
    try:
        status, data = client.uid("search", None, "ALL")
        count = len(data[0].split()) if status == "OK" and data and data[0] else 0
        print(json.dumps({"ok": True, "mailbox": "INBOX", "message_count": count}, ensure_ascii=False))
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


def fetch(paths: RuntimePaths, days: int) -> int:
    state = _load_state(paths)
    last_uid = int(state.get("last_committed_uid", 0))
    client = _open_mailbox(paths)
    try:
        if last_uid > 0:
            status, data = client.uid("search", None, f"UID {last_uid + 1}:*")
        else:
            since_date = (datetime.now() - timedelta(days=max(1, days))).strftime("%d-%b-%Y")
            status, data = client.uid("search", None, "SINCE", since_date)
        if status != "OK":
            raise RuntimeError("QQ 邮箱未返回可读取的邮件列表。")

        uid_values = [int(value) for value in (data[0].split() if data and data[0] else [])]
        uid_values = [value for value in uid_values if value > last_uid][-MAX_MESSAGES_PER_RUN:]
        messages: list[dict] = []
        for uid in uid_values:
            status, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not parts:
                continue
            raw = next((part[1] for part in parts if isinstance(part, tuple) and len(part) > 1), None)
            if not raw:
                continue
            parsed = _extract_message(paths, uid, raw)
            if parsed["is_job_related_candidate"]:
                messages.append(parsed)

        max_uid = max(uid_values, default=last_uid)
        result = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "last_committed_uid": last_uid,
            "max_uid": max_uid,
            "fetched_count": len(uid_values),
            "relevant_count": len(messages),
            "messages": messages,
        }
        _write_json(paths.pending, result)
        print(json.dumps({
            "ok": True,
            "pending_file": str(paths.pending),
            "fetched_count": len(uid_values),
            "relevant_count": len(messages),
            "max_uid": max_uid,
        }, ensure_ascii=False))
        return 0
    finally:
        try:
            client.logout()
        except Exception:
            pass


def commit(paths: RuntimePaths, uid: int) -> int:
    state = _load_state(paths)
    current = int(state.get("last_committed_uid", 0))
    if uid < current:
        raise RuntimeError("不能把已处理邮件位置回退。")
    _write_json(paths.state, {
        "last_committed_uid": uid,
        "committed_at": datetime.now().astimezone().isoformat(),
    })
    print(json.dumps({"ok": True, "last_committed_uid": uid}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="只读导出 QQ 邮箱中的求职相关新邮件。")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="运行数据目录；默认使用 AUTUMN_RECRUITMENT_MAIL_DATA_DIR 或本机应用数据目录。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--days", type=int, default=7)
    subparsers.add_parser("test-login")
    commit_parser = subparsers.add_parser("commit")
    commit_parser.add_argument("uid", type=int)
    subparsers.add_parser("storage-report")
    args = parser.parse_args()
    paths = RuntimePaths(args.data_dir or _default_data_dir())

    try:
        if args.command == "test-login":
            return test_login(paths)
        if args.command == "fetch":
            return fetch(paths, args.days)
        if args.command == "commit":
            return commit(paths, args.uid)
        if args.command == "storage-report":
            print(json.dumps(storage_report(paths), ensure_ascii=False))
            return 0
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

