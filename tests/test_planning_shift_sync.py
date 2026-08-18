from datetime import date

from odoo.tests.common import TransactionCase


class TestPlanningShiftSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "Technician Monday Schedule",
            "tz": "America/El_Salvador",
            "attendance_ids": [
                (0, 0, {
                    "name": "Monday morning",
                    "dayofweek": "0",
                    "day_period": "morning",
                    "hour_from": 7.5,
                    "hour_to": 12.0,
                }),
                (0, 0, {
                    "name": "Monday lunch",
                    "dayofweek": "0",
                    "day_period": "lunch",
                    "hour_from": 12.0,
                    "hour_to": 13.0,
                }),
                (0, 0, {
                    "name": "Monday afternoon",
                    "dayofweek": "0",
                    "day_period": "afternoon",
                    "hour_from": 13.0,
                    "hour_to": 17.5,
                }),
            ],
        })
        cls.technician = cls.env["hr.employee"].create({
            "name": "Planning Sync Technician",
            "company_id": cls.env.company.id,
            "resource_calendar_id": cls.calendar.id,
        })
        cls.other_employee = cls.env["hr.employee"].create({
            "name": "Employee Outside FSM Teams",
            "company_id": cls.env.company.id,
            "resource_calendar_id": cls.calendar.id,
        })
        cls.team = cls.env["fsm.team"].create({
            "member_ids": [(6, 0, [cls.technician.id])],
        })
        cls.slot_model = cls.env["planning.slot"]
        cls.monday = date(2026, 8, 17)

    def test_sync_creates_one_timezone_correct_shift_and_assigns_role(self):
        result = self.slot_model.sync_fsm_technician_shifts(
            self.monday, self.monday
        )

        slot = self.slot_model.search([
            ("fsm_employee_schedule_generated", "=", True),
            ("resource_id", "=", self.technician.resource_id.id),
            ("fsm_schedule_date", "=", self.monday),
        ])
        role = self.env.ref("fsm_guided_intake.planning_role_fsm_technician")
        self.assertEqual(result["created"], 1)
        self.assertEqual(slot.role_id, role)
        self.assertEqual(slot.fsm_team_id, self.team)
        self.assertEqual(str(slot.start_datetime), "2026-08-17 13:30:00")
        self.assertEqual(str(slot.end_datetime), "2026-08-17 23:30:00")
        self.assertIn(role, self.technician.planning_role_ids)
        self.assertEqual(self.technician.default_planning_role_id, role)
        self.assertFalse(self.slot_model.search([
            ("resource_id", "=", self.other_employee.resource_id.id),
            ("fsm_employee_schedule_generated", "=", True),
        ]))

    def test_sync_is_idempotent(self):
        first = self.slot_model.sync_fsm_technician_shifts(self.monday, self.monday)
        second = self.slot_model.sync_fsm_technician_shifts(self.monday, self.monday)

        slots = self.slot_model.search([
            ("fsm_employee_schedule_generated", "=", True),
            ("resource_id", "=", self.technician.resource_id.id),
            ("fsm_schedule_date", "=", self.monday),
        ])
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertGreaterEqual(second["updated"], 1)
        self.assertEqual(len(slots), 1)

    def test_sync_removes_generated_shift_when_day_is_no_longer_worked(self):
        self.slot_model.sync_fsm_technician_shifts(self.monday, self.monday)
        self.calendar.attendance_ids.unlink()

        result = self.slot_model.sync_fsm_technician_shifts(self.monday, self.monday)

        self.assertEqual(result["removed"], 1)
        self.assertFalse(self.slot_model.search([
            ("fsm_employee_schedule_generated", "=", True),
            ("resource_id", "=", self.technician.resource_id.id),
            ("fsm_schedule_date", "=", self.monday),
        ]))

    def test_sync_does_not_overwrite_or_remove_published_shift(self):
        self.slot_model.sync_fsm_technician_shifts(self.monday, self.monday)
        slot = self.slot_model.search([
            ("fsm_employee_schedule_generated", "=", True),
            ("resource_id", "=", self.technician.resource_id.id),
            ("fsm_schedule_date", "=", self.monday),
        ])
        original_start = slot.start_datetime
        slot.state = "published"
        self.calendar.attendance_ids.unlink()

        result = self.slot_model.sync_fsm_technician_shifts(
            self.monday, self.monday
        )

        self.assertEqual(result["removed"], 0)
        self.assertTrue(slot.exists())
        self.assertEqual(slot.state, "published")
        self.assertEqual(slot.start_datetime, original_start)
