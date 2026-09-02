from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REPORT_SECTIONS = ["今日行动", "最新变化", "未来 7 天", "待补全与需确认"]
TERMINAL_STATUSES = {"Offer", "拒信", "背调", "完成", "取消", "放弃", "已复盘"}

URL_RE = re.compile(r"https?://[^\s<>\"'）】》]+", re.IGNORECASE)
DATE_RES = (
    re.compile(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2})?\b"),
    re.compile(r"\b\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2})?\b"),
    re.compile(r"(?:20\d{2}年)?\d{1,2}月\d{1,2}日(?:\s*\d{1,2}(?::|：)\d{2})?"),
)
ID_PATTERNS = (
    ("job_id", re.compile(r"(?:岗位|职位|职务|job|position)\s*(?:编号|id|no\.?|code)?\s*[:：#]?\s*([A-Z0-9][A-Z0-9_-]{3,})", re.IGNORECASE)),
    ("requisition_id", re.compile(r"(?:requisition|req)\s*(?:id|no\.?)?\s*[:：#]?\s*([A-Z0-9][A-Z0-9_-]{3,})", re.IGNORECASE)),
    ("application_id", re.compile(r"(?:申请|应聘|candidate|application)\s*(?:编号|id|no\.?|code)?\s*[:：#]?\s*([A-Z0-9][A-Z0-9_-]{3,})", re.IGNORECASE)),
)
APPLICATION_URL_WORDS = ("apply", "application", "candidate", "career", "job", "position", "recruit", "ats")
MEETING_URL_WORDS = ("meeting", "zoom", "teams", "tencent", "voov", "feishu", "lark", "webex")


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def normalize_email_address(value: object) -> str:
    return parseaddr(_text(value))[1].strip().lower()


def sender_domain(value: object) -> str:
    address = normalize_email_address(value)
    return address.rsplit("@", 1)[1] if "@" in address else ""


def normalize_message_id(value: object) -> str:
    return _text(value).strip("<>").lower()


def normalize_subject(value: object) -> str:
    subject = _text(value)
    prefix = re.compile(r"^\s*(?:re|fw|fwd|回复|答复|转发)\s*[:：]\s*", re.IGNORECASE)
    while prefix.search(subject):
        subject = prefix.sub("", subject, count=1)
    subject = re.sub(r"\s+", " ", subject)
    return subject.strip().lower()


def canonical_url(value: object) -> str:
    raw = _text(value).rstrip(".,;:!?，。；：！？)]}）】》")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))
    except ValueError:
        return ""


def extract_urls(value: object) -> list[str]:
    urls = {canonical_url(match.group(0)) for match in URL_RE.finditer(_text(value))}
    return sorted(url for url in urls if url)


def extract_identifiers(value: object) -> list[dict]:
    text = _text(value)
    found: set[tuple[str, str]] = set()
    for kind, pattern in ID_PATTERNS:
        for match in pattern.finditer(text):
            identifier = match.group(1).strip().upper()
            found.add((kind, identifier))
    return [{"type": kind, "value": value} for kind, value in sorted(found)]


def extract_date_candidates(value: object) -> list[str]:
    text = _text(value)
    found: set[str] = set()
    for pattern in DATE_RES:
        found.update(re.sub(r"\s+", " ", match.group(0)).strip() for match in pattern.finditer(text))
    return sorted(found)


def _url_kind(url: str) -> str:
    lowered = url.lower()
    if any(word in lowered for word in MEETING_URL_WORDS):
        return "meeting"
    if any(word in lowered for word in APPLICATION_URL_WORDS):
        return "application"
    return "other"


def normalize_mail(message: dict) -> dict:
    sender_raw = _text(message.get("from") or message.get("sender"))
    address = normalize_email_address(sender_raw)
    domain = sender_domain(sender_raw)
    subject_raw = _text(message.get("subject"))
    body = _text(message.get("body"))
    urls = extract_urls(f"{subject_raw}\n{body}")
    identifiers = extract_identifiers(f"{subject_raw}\n{body}")
    message_id = normalize_message_id(message.get("message_id"))
    uid = message.get("uid")
    normalized_subject = normalize_subject(subject_raw)
    job_ids = sorted({item["value"] for item in identifiers if item["type"] in {"job_id", "requisition_id"}})
    application_ids = sorted({item["value"] for item in identifiers if item["type"] == "application_id"})
    application_urls = sorted(url for url in urls if _url_kind(url) == "application")
    meeting_urls = sorted(url for url in urls if _url_kind(url) == "meeting")

    evidence_keys = {
        "message_id_sender": f"{message_id}|{address}" if message_id and address else "",
        "uid_sender": f"{uid}|{address}" if uid is not None and address else "",
        "subject_sender": f"{normalized_subject}|{address}" if normalized_subject and address else "",
        "job_id_domain": [f"{item}|{domain}" for item in job_ids if domain],
        "application_url_domain": [f"{item}|{domain}" for item in application_urls if domain],
    }
    return {
        "uid": uid,
        "message_id": message_id,
        "subject_raw": subject_raw,
        "normalized_subject": normalized_subject,
        "sender_raw": sender_raw,
        "sender_address": address,
        "sender_domain": domain,
        "date": _text(message.get("date")),
        "job_ids": job_ids,
        "application_ids": application_ids,
        "application_urls": application_urls,
        "meeting_urls": meeting_urls,
        "other_urls": sorted(url for url in urls if _url_kind(url) == "other"),
        "date_candidates": extract_date_candidates(f"{subject_raw}\n{body}"),
        "matched_keywords": sorted({_text(item) for item in message.get("matched_keywords", []) if _text(item)}),
        "attachments": message.get("attachments", []),
        "evidence_keys": evidence_keys,
    }


def normalize_mail_batch(payload: dict | list) -> dict:
    messages = payload.get("messages", []) if isinstance(payload, dict) else payload
    normalized = [normalize_mail(item) for item in messages if isinstance(item, dict)]
    metadata = {
        key: payload.get(key)
        for key in ("generated_at", "last_committed_uid", "max_uid", "fetched_count", "relevant_count")
        if isinstance(payload, dict) and key in payload
    }
    return {**metadata, "normalized_count": len(normalized), "messages": normalized}


def _record_view(record: dict) -> dict:
    sender = record.get("sender_address") or record.get("sender") or record.get("from")
    domain = _text(record.get("sender_domain")) or sender_domain(sender)
    identifiers = record.get("job_ids", [])
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    urls = record.get("application_urls", [])
    if isinstance(urls, str):
        urls = [urls]
    return {
        "id": _text(record.get("id") or record.get("url") or record.get("page_url")),
        "message_id": normalize_message_id(record.get("message_id")),
        "uid": record.get("uid"),
        "sender_address": normalize_email_address(sender),
        "sender_domain": domain.lower(),
        "job_ids": {str(item).strip().upper() for item in identifiers if _text(item)},
        "application_urls": {canonical_url(item) for item in urls if canonical_url(item)},
        "company": _text(record.get("company")).lower(),
        "job_title": _text(record.get("job_title") or record.get("position")).lower(),
        "normalized_subject": normalize_subject(record.get("normalized_subject") or record.get("subject")),
    }


def _candidate_view(candidate: dict) -> dict:
    result = dict(candidate)
    result["job_ids"] = {str(item).strip().upper() for item in candidate.get("job_ids", []) if _text(item)}
    result["application_urls"] = {canonical_url(item) for item in candidate.get("application_urls", []) if canonical_url(item)}
    result["company"] = _text(candidate.get("company")).lower()
    result["job_title"] = _text(candidate.get("job_title") or candidate.get("position")).lower()
    return result


def _match_evidence(candidate: dict, record: dict) -> list[dict]:
    evidence: list[dict] = []
    if candidate.get("message_id") and candidate.get("sender_address"):
        if candidate["message_id"] == record["message_id"] and candidate["sender_address"] == record["sender_address"]:
            evidence.append({"tier": 1, "rule": "message_id+sender"})
    if candidate.get("uid") is not None and candidate.get("sender_address"):
        if candidate["uid"] == record["uid"] and candidate["sender_address"] == record["sender_address"]:
            evidence.append({"tier": 2, "rule": "uid+sender"})
    if candidate.get("sender_domain") and candidate["sender_domain"] == record["sender_domain"]:
        shared_ids = sorted(candidate["job_ids"] & record["job_ids"])
        if shared_ids:
            evidence.append({"tier": 3, "rule": "job_id+sender_domain", "values": shared_ids})
        shared_urls = sorted(candidate["application_urls"] & record["application_urls"])
        if shared_urls:
            evidence.append({"tier": 3, "rule": "application_url+sender_domain", "values": shared_urls})
    same_sender = candidate.get("sender_address") == record["sender_address"] or (
        candidate.get("sender_domain") and candidate.get("sender_domain") == record["sender_domain"]
    )
    if candidate.get("company") and candidate.get("job_title") and same_sender:
        if candidate["company"] == record["company"] and candidate["job_title"] == record["job_title"]:
            evidence.append({"tier": 4, "rule": "company+job_title+sender"})
    if candidate.get("normalized_subject") and candidate.get("sender_address"):
        if candidate["normalized_subject"] == record["normalized_subject"] and candidate["sender_address"] == record["sender_address"]:
            evidence.append({"tier": 5, "rule": "normalized_subject+sender"})
    return evidence


def exact_match_candidates(candidates: list[dict], records: list[dict]) -> dict:
    normalized_records = [_record_view(item) for item in records if isinstance(item, dict)]
    results: list[dict] = []
    for raw_candidate in candidates:
        candidate = _candidate_view(raw_candidate)
        matches: list[dict] = []
        for record in normalized_records:
            evidence = _match_evidence(candidate, record)
            if evidence:
                best_tier = min(item["tier"] for item in evidence)
                matches.append({"record_id": record["id"], "best_tier": best_tier, "evidence": evidence})
        if not matches:
            decision = "no_match"
            selected = None
        else:
            best = min(item["best_tier"] for item in matches)
            strongest = [item for item in matches if item["best_tier"] == best]
            decision = "unique_match" if len(strongest) == 1 else "ambiguous"
            selected = strongest[0]["record_id"] if decision == "unique_match" else None
        results.append({
            "candidate_uid": candidate.get("uid"),
            "candidate_message_id": candidate.get("message_id"),
            "decision": decision,
            "selected_record_id": selected,
            "matches": sorted(matches, key=lambda item: (item["best_tier"], item["record_id"])),
        })
    counts = {key: sum(1 for item in results if item["decision"] == key) for key in ("unique_match", "ambiguous", "no_match")}
    return {"counts": counts, "results": results}


def validate_plan(plan: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    run_date = _text(plan.get("date"))
    mocks = [item for item in plan.get("daily_mock_tasks", []) if _text(item.get("scheduled_date")) == run_date]
    if len(mocks) != 1:
        errors.append({"code": "DAILY_MOCK_COUNT", "expected": 1, "actual": len(mocks)})
    kinds = {_text(item.get("kind")).lower() for item in mocks}
    if "targeted" in kinds and "generic" in kinds:
        errors.append({"code": "DUPLICATE_DAILY_MOCK_KIND"})

    for interview in plan.get("interviews", []):
        interview_id = _text(interview.get("id"))
        expected = {
            "real_pages": 1,
            "simulation_pages": 1,
            "schedules": 1,
            "simulation_tasks": 1,
            "review_tasks": 1,
        }
        for field, count in expected.items():
            actual = len(interview.get(field, []))
            if actual != count:
                errors.append({"code": "INTERVIEW_OBJECT_COUNT", "interview_id": interview_id, "field": field, "expected": count, "actual": actual})
        if _text(interview.get("status")).lower() not in {"cancelled", "取消", "放弃"}:
            real_date = _text(interview.get("real_date"))
            for field in ("simulation_date", "review_date"):
                value = _text(interview.get(field))
                if real_date and value and value != real_date:
                    errors.append({"code": "INTERVIEW_DATE_MISMATCH", "interview_id": interview_id, "field": field, "expected": real_date, "actual": value})

    report = plan.get("report", {})
    sections = report.get("sections", []) if isinstance(report, dict) else []
    section_names = [_text(item.get("name")) for item in sections]
    if section_names != REPORT_SECTIONS:
        errors.append({"code": "REPORT_SECTION_ORDER", "expected": REPORT_SECTIONS, "actual": section_names})
    fact_locations: dict[str, list[str]] = defaultdict(list)
    for section in sections:
        name = _text(section.get("name"))
        for fact_id in section.get("fact_ids", []):
            fact_locations[_text(fact_id)].append(name)
    for fact_id, locations in fact_locations.items():
        if fact_id and len(locations) > 1:
            errors.append({"code": "DUPLICATE_REPORT_FACT", "fact_id": fact_id, "sections": locations})

    for transition in plan.get("status_transitions", []):
        old = _text(transition.get("from"))
        new = _text(transition.get("to"))
        evidence = transition.get("evidence")
        if old in TERMINAL_STATUSES and new != old:
            errors.append({"code": "TERMINAL_STATUS_CHANGED", "entity": transition.get("entity"), "from": old, "to": new})
        if new in TERMINAL_STATUSES and not evidence:
            errors.append({"code": "TERMINAL_STATUS_WITHOUT_EVIDENCE", "entity": transition.get("entity"), "to": new})

    commit = plan.get("commit", {})
    required = ("jobs_ok", "interviews_ok", "tasks_ok", "report_ok")
    missing = [field for field in required if commit.get(field) is not True]
    if commit.get("requested") and missing:
        errors.append({"code": "UID_COMMIT_NOT_READY", "missing": missing})
    elif not commit.get("requested") and all(commit.get(field) is True for field in required):
        warnings.append({"code": "UID_COMMIT_READY_NOT_REQUESTED"})
    return {"valid": not errors, "error_count": len(errors), "warning_count": len(warnings), "errors": errors, "warnings": warnings}


def _parse_datetime(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: item[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _percentile(values: list[float], fraction: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (position - low)


def estimate_focus(payload: dict | list) -> dict:
    records = payload.get("records", []) if isinstance(payload, dict) else payload
    by_task: dict[tuple[str, str], list[tuple[datetime, datetime]]] = defaultdict(list)
    rejected: list[dict] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("completed") is not True:
            continue
        start = _parse_datetime(record.get("start"))
        end = _parse_datetime(record.get("end"))
        if not start or not end or end <= start:
            rejected.append({"index": index, "reason": "invalid_interval"})
            continue
        minutes = (end - start).total_seconds() / 60
        if minutes <= 0 or minutes > 240 or start.date() != end.date():
            rejected.append({"index": index, "reason": "low_confidence_interval", "minutes": round(minutes, 1)})
            continue
        task_id = _text(record.get("task_id")) or f"record-{index}"
        task_type = _text(record.get("task_type")) or "未分类"
        by_task[(task_type, task_id)].append((start, end))

    samples_by_type: dict[str, list[float]] = defaultdict(list)
    for (task_type, _), intervals in by_task.items():
        merged = _merge_intervals(intervals)
        total = sum((end - start).total_seconds() / 60 for start, end in merged)
        if 0 < total <= 240:
            samples_by_type[task_type].append(total)

    estimates: list[dict] = []
    for task_type, raw_values in sorted(samples_by_type.items()):
        values = sorted(raw_values)
        median = statistics.median(values)
        spread = (max(values) - min(values)) / median if median else 0
        basis = _percentile(values, 0.75) if len(values) >= 3 and spread >= 0.5 else median
        estimate = int(math.ceil(basis / 15.0) * 15)
        confidence = "高" if len(values) >= 3 else "中"
        estimates.append({
            "task_type": task_type,
            "sample_count": len(values),
            "samples_minutes": [round(value, 1) for value in values],
            "median_minutes": round(median, 1),
            "estimate_minutes": estimate,
            "confidence": confidence,
            "basis": "upper_quartile" if basis != median else "median",
        })
    return {"estimates": estimates, "rejected_records": rejected}


def _command_normalize(args: argparse.Namespace) -> dict:
    result = normalize_mail_batch(_load_json(args.input))
    _write_json(args.output, result)
    return {"ok": True, "output": str(args.output.resolve()), "normalized_count": result["normalized_count"]}


def _command_match(args: argparse.Namespace) -> dict:
    candidate_payload = _load_json(args.candidates)
    record_payload = _load_json(args.records)
    candidates = candidate_payload.get("messages", []) if isinstance(candidate_payload, dict) else candidate_payload
    records = record_payload.get("records", []) if isinstance(record_payload, dict) else record_payload
    result = exact_match_candidates(candidates, records)
    _write_json(args.output, result)
    return {"ok": True, "output": str(args.output.resolve()), **result["counts"]}


def _command_validate(args: argparse.Namespace) -> dict:
    result = validate_plan(_load_json(args.input))
    _write_json(args.output, result)
    return {"ok": True, "output": str(args.output.resolve()), "valid": result["valid"], "errors": result["error_count"]}


def _command_estimate(args: argparse.Namespace) -> dict:
    result = estimate_focus(_load_json(args.input))
    _write_json(args.output, result)
    return {"ok": True, "output": str(args.output.resolve()), "task_types": len(result["estimates"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="秋招系统确定性标准化、匹配、校验和耗时估算。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize_parser = subparsers.add_parser("normalize-mails")
    normalize_parser.add_argument("--input", type=Path, required=True)
    normalize_parser.add_argument("--output", type=Path, required=True)
    normalize_parser.set_defaults(handler=_command_normalize)

    match_parser = subparsers.add_parser("match")
    match_parser.add_argument("--candidates", type=Path, required=True)
    match_parser.add_argument("--records", type=Path, required=True)
    match_parser.add_argument("--output", type=Path, required=True)
    match_parser.set_defaults(handler=_command_match)

    validate_parser = subparsers.add_parser("validate-plan")
    validate_parser.add_argument("--input", type=Path, required=True)
    validate_parser.add_argument("--output", type=Path, required=True)
    validate_parser.set_defaults(handler=_command_validate)

    estimate_parser = subparsers.add_parser("estimate-focus")
    estimate_parser.add_argument("--input", type=Path, required=True)
    estimate_parser.add_argument("--output", type=Path, required=True)
    estimate_parser.set_defaults(handler=_command_estimate)

    args = parser.parse_args()
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

