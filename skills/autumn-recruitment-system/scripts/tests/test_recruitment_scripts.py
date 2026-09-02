from __future__ import annotations

import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "qq_job_mail"))

import recruitment_rules as rules
import qq_mail_export as mail


class RecruitmentRulesTests(unittest.TestCase):
    def test_normalize_mail_extracts_strict_fields(self) -> None:
        result = rules.normalize_mail({
            "uid": 42,
            "message_id": "<ABC@example.com>",
            "subject": "Re: 面试通知 Position ID: ABC-1234",
            "from": "HR Team <HR@Example.com>",
            "body": "请访问 https://careers.example.com/apply/42 ，截止 2026-09-10 18:00。",
        })
        self.assertEqual(result["message_id"], "abc@example.com")
        self.assertEqual(result["sender_address"], "hr@example.com")
        self.assertEqual(result["sender_domain"], "example.com")
        self.assertIn("ABC-1234", result["job_ids"])
        self.assertEqual(len(result["application_urls"]), 1)
        self.assertIn("2026-09-10 18:00", result["date_candidates"])

    def test_exact_match_requires_compound_evidence(self) -> None:
        candidate = rules.normalize_mail({
            "uid": 42,
            "message_id": "<ABC@example.com>",
            "subject": "面试通知",
            "from": "HR <hr@example.com>",
            "body": "",
        })
        unique = rules.exact_match_candidates([candidate], [{
            "id": "page-1",
            "message_id": "abc@example.com",
            "sender_address": "hr@example.com",
        }])
        self.assertEqual(unique["results"][0]["decision"], "unique_match")
        sender_only = rules.exact_match_candidates([candidate], [{
            "id": "page-2",
            "sender_address": "hr@example.com",
        }])
        self.assertEqual(sender_only["results"][0]["decision"], "no_match")

    def test_same_tier_multiple_matches_are_ambiguous(self) -> None:
        candidate = rules.normalize_mail({
            "uid": 42,
            "message_id": "<ABC@example.com>",
            "subject": "面试通知",
            "from": "HR <hr@example.com>",
            "body": "",
        })
        records = [
            {"id": "page-1", "message_id": "abc@example.com", "sender_address": "hr@example.com"},
            {"id": "page-2", "message_id": "abc@example.com", "sender_address": "hr@example.com"},
        ]
        result = rules.exact_match_candidates([candidate], records)
        self.assertEqual(result["results"][0]["decision"], "ambiguous")

    def test_plan_validator_checks_core_invariants(self) -> None:
        valid_plan = {
            "date": "2026-09-02",
            "daily_mock_tasks": [{"id": "m1", "kind": "targeted", "scheduled_date": "2026-09-02"}],
            "interviews": [{
                "id": "i1",
                "status": "active",
                "real_pages": ["r"],
                "simulation_pages": ["s"],
                "schedules": ["d"],
                "simulation_tasks": ["m"],
                "review_tasks": ["v"],
                "real_date": "2026-09-02",
                "simulation_date": "2026-09-02",
                "review_date": "2026-09-02",
            }],
            "report": {"sections": [
                {"name": "今日行动", "fact_ids": ["f1"]},
                {"name": "最新变化", "fact_ids": ["f2"]},
                {"name": "未来 7 天", "fact_ids": ["f3"]},
                {"name": "待补全与需确认", "fact_ids": ["f4"]},
            ]},
            "status_transitions": [],
            "commit": {"requested": True, "jobs_ok": True, "interviews_ok": True, "tasks_ok": True, "report_ok": True},
        }
        self.assertTrue(rules.validate_plan(valid_plan)["valid"])
        valid_plan["report"]["sections"][1]["fact_ids"] = ["f1"]
        report = rules.validate_plan(valid_plan)
        self.assertFalse(report["valid"])
        self.assertIn("DUPLICATE_REPORT_FACT", {item["code"] for item in report["errors"]})

    def test_focus_estimator_merges_overlaps(self) -> None:
        payload = {"records": [
            {"task_id": "a", "task_type": "模拟面试", "start": "2026-09-01T09:00:00+08:00", "end": "2026-09-01T10:00:00+08:00", "completed": True},
            {"task_id": "a", "task_type": "模拟面试", "start": "2026-09-01T09:30:00+08:00", "end": "2026-09-01T10:30:00+08:00", "completed": True},
            {"task_id": "b", "task_type": "模拟面试", "start": "2026-09-02T09:00:00+08:00", "end": "2026-09-02T10:00:00+08:00", "completed": True},
            {"task_id": "c", "task_type": "模拟面试", "start": "2026-09-03T09:00:00+08:00", "end": "2026-09-03T10:15:00+08:00", "completed": True},
        ]}
        estimate = rules.estimate_focus(payload)["estimates"][0]
        self.assertEqual(estimate["samples_minutes"], [60.0, 75.0, 90.0])
        self.assertEqual(estimate["estimate_minutes"], 75)
        self.assertEqual(estimate["confidence"], "高")


class MailStorageTests(unittest.TestCase):
    def test_default_data_dir_is_stable_when_local_app_data_is_virtualized(self) -> None:
        with mock.patch.dict(
            mail.os.environ,
            {
                "USERPROFILE": "C:/Users/example",
                "LOCALAPPDATA": "D:/virtualized/LocalCache/Local",
                "AUTUMN_RECRUITMENT_MAIL_DATA_DIR": "",
            },
            clear=False,
        ):
            self.assertEqual(
                mail._default_data_dir(),
                Path("C:/Users/example/AppData/Local/Codex/autumn-recruitment-system/qq_job_mail"),
            )

    def _message(self, body: str) -> bytes:
        message = EmailMessage()
        message["Subject"] = "通知"
        message["From"] = "service@example.com"
        message["To"] = "user@qq.com"
        message.set_content(body)
        message.add_attachment(b"sample", maintype="application", subtype="pdf", filename="sample.pdf")
        return message.as_bytes()

    def test_attachment_content_is_never_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = mail.RuntimePaths(Path(directory))
            ordinary = mail._extract_message(paths, 1, self._message("普通账户通知"))
            self.assertFalse(ordinary["is_job_related_candidate"])

            job = mail._extract_message(paths, 2, self._message("校招面试通知"))
            self.assertTrue(job["is_job_related_candidate"])
            attachment = job["attachments"][0]
            self.assertEqual(attachment["filename"], "sample.pdf")
            self.assertIsNone(attachment["saved_path"])
            self.assertEqual(attachment["skipped_reason"], "attachment_storage_disabled")


if __name__ == "__main__":
    unittest.main()

