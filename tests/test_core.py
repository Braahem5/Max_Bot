from __future__ import annotations

import unittest
from pathlib import Path

from benefits_data import check_benefits, get_benefit
from storage import Storage


class BenefitsRulesTest(unittest.TestCase):
    def test_city_profile_does_not_recommend_rural_tatarstan_payment(self):
        checks = check_benefits(
            "multichild",
            "tatarstan",
            {
                "young_child": "no",
                "school_child": "yes",
                "residence": "city",
            },
        )
        statuses = {check.benefit.id: check.status for check in checks}
        self.assertEqual(statuses["matkap_tatarstan"], "not_for_profile")
        self.assertEqual(statuses["tatarstan_transport_multichild"], "candidate")

    def test_unknown_answer_is_marked_for_review(self):
        benefit = get_benefit("tatarstan_medicine_multichild")
        self.assertIsNotNone(benefit)
        check = check_benefits(
            "multichild",
            "tatarstan",
            {"young_child": "unknown"},
        )
        medicine_check = next(
            item for item in check if item.benefit.id == benefit.id
        )
        self.assertEqual(medicine_check.status, "check")

    def test_tatarstan_demo_entries_have_official_links(self):
        for benefit_id in (
            "multichild_status",
            "matkap_tatarstan",
            "tatarstan_zhku_multichild",
            "tatarstan_transport_multichild",
            "tatarstan_medicine_multichild",
        ):
            benefit = get_benefit(benefit_id)
            self.assertIsNotNone(benefit)
            self.assertTrue(benefit.source_url)
            self.assertTrue(benefit.application_url)


class StorageTest(unittest.TestCase):
    def test_plan_status_and_due_reminder_are_persisted(self):
        db_path = Path.cwd() / "tests" / "test-runtime.sqlite3"
        try:
            storage = Storage(db_path)
            storage.save_plan(
                user_id=10,
                chat_id=20,
                benefit_id="multichild_status",
                title="Удостоверение многодетной семьи",
                category="multichild",
                region="tatarstan",
            )
            storage.set_status(
                user_id=10,
                benefit_id="multichild_status",
                status="done",
            )
            storage.set_reminder(
                user_id=10,
                chat_id=20,
                benefit_id="multichild_status",
                reminder_at=100,
            )

            plan = storage.list_plan(10)
            due = storage.due_reminders(101)

            self.assertEqual(plan[0]["status"], "done")
            self.assertEqual(due[0]["chat_id"], 20)
            self.assertEqual(due[0]["benefit_id"], "multichild_status")

            storage.mark_reminder_sent(due[0]["id"])
            self.assertEqual(storage.due_reminders(101), [])
        finally:
            for suffix in ("", "-wal", "-shm"):
                db_path.with_name(db_path.name + suffix).unlink(
                    missing_ok=True
                )


if __name__ == "__main__":
    unittest.main()
