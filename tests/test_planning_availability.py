from datetime import datetime

from odoo.tests.common import TransactionCase


class TestPlanningAvailability(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.role = cls.env.ref(
            "fsm_guided_intake.planning_role_fsm_technician"
        )
        cls.user_one = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Planning Availability Technician One",
            "login": "planning.availability.one@gmail.com",
            "email": "planning.availability.one@gmail.com",
        })
        cls.employee_one = cls.env["hr.employee"].create({
            "name": "Planning Availability Technician One",
            "company_id": cls.env.company.id,
            "user_id": cls.user_one.id,
            "planning_role_ids": [(4, cls.role.id)],
            "default_planning_role_id": cls.role.id,
        })
        cls.user_two = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Planning Availability Technician Two",
            "login": "planning.availability.two@gmail.com",
            "email": "planning.availability.two@gmail.com",
        })
        cls.employee_two = cls.env["hr.employee"].create({
            "name": "Planning Availability Technician Two",
            "company_id": cls.env.company.id,
            "user_id": cls.user_two.id,
            "planning_role_ids": [(4, cls.role.id)],
            "default_planning_role_id": cls.role.id,
        })
        # No static members: the dated Planning records own the roster.
        cls.team = cls.env["fsm.team"].create({})
        cls.engine = cls.env["fsm.slot.engine"].with_context(
            tz="America/El_Salvador"
        )

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param(
            "fsm_guided_intake.availability_source", "planning"
        )

    def _shift(self, employee, start_utc, end_utc, state="published", team=None):
        return self.env["planning.slot"].create({
            "name": "Technician roster",
            "resource_id": employee.resource_id.id,
            "role_id": self.role.id,
            "fsm_team_id": (team or self.team).id,
            "start_datetime": start_utc,
            "end_datetime": end_utc,
            "state": state,
            "company_id": self.env.company.id,
        })

    def _slots(self, start_local, end_local, limit=3):
        return self.engine.compute_top_slots(
            teams=self.team,
            start_dt_local=start_local,
            date_end_local=end_local,
            needed_hours=1.0,
            limit=limit,
        )

    def test_published_planning_shift_defines_availability(self):
        # UTC 21:00 is local 15:00 in El Salvador.
        self._shift(
            self.employee_one,
            datetime(2026, 8, 17, 21, 0),
            datetime(2026, 8, 17, 23, 0),
        )

        slots = self._slots(
            datetime(2026, 8, 17, 7, 0),
            datetime(2026, 8, 18, 0, 0),
            limit=1,
        )

        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["start"], datetime(2026, 8, 17, 15, 0))

    def test_unsaved_guided_wizard_resolves_virtual_team_records(self):
        """The first modal render must work before the transient is saved."""
        self._shift(
            self.employee_one,
            datetime(2026, 8, 17, 21, 0),
            datetime(2026, 8, 17, 23, 0),
        )
        project = self.env["project.project"].create({
            "name": "Virtual Wizard Planning Test",
            "is_fsm": True,
            "company_id": self.env.company.id,
        })
        task_type = self.env["fsm.task.type"].create({
            "name": "Virtual Wizard Planning Task",
            "project_id": project.id,
            "default_planned_hours": 1.0,
            "preferred_team_ids": [(6, 0, self.team.ids)],
        })
        partner = self.env["res.partner"].create({
            "name": "Virtual Wizard Planning Customer",
        })
        wizard = self.env["fsm.task.intake.wizard"].with_context(
            tz="America/El_Salvador",
        ).new({
            "partner_id": partner.id,
            "task_type_id": task_type.id,
            "search_start_dt": datetime(2026, 8, 17, 15, 0),
        })

        wizard._compute_slots()

        self.assertEqual(wizard.qualified_team_ids._origin, self.team)
        self.assertEqual(wizard.slot1_team_id._origin, self.team)
        self.assertGreaterEqual(
            wizard.slot1_start, datetime(2026, 8, 17, 15, 0)
        )
        self.assertLessEqual(
            wizard.slot1_end, datetime(2026, 8, 17, 17, 0)
        )
        self.assertTrue(wizard.slot1_label)

    def test_draft_planning_shift_does_not_create_availability(self):
        self._shift(
            self.employee_one,
            datetime(2026, 8, 17, 14, 0),
            datetime(2026, 8, 17, 20, 0),
            state="draft",
        )

        slots = self._slots(
            datetime(2026, 8, 17, 7, 0),
            datetime(2026, 8, 18, 0, 0),
        )

        self.assertFalse(slots)

    def test_team_window_is_overlap_of_rostered_resources(self):
        self._shift(
            self.employee_one,
            datetime(2026, 8, 17, 14, 0),
            datetime(2026, 8, 17, 23, 0),
        )
        self._shift(
            self.employee_two,
            datetime(2026, 8, 17, 16, 0),
            datetime(2026, 8, 17, 22, 0),
        )

        slots = self._slots(
            datetime(2026, 8, 17, 7, 0),
            datetime(2026, 8, 18, 0, 0),
            limit=1,
        )
        users = self.engine.get_team_users_for_interval_utc(
            self.team,
            datetime(2026, 8, 17, 16, 0),
            datetime(2026, 8, 17, 17, 0),
        )

        self.assertEqual(slots[0]["start"], datetime(2026, 8, 17, 10, 0))
        self.assertEqual(set(users.ids), {self.user_one.id, self.user_two.id})

    def test_team_roster_change_updates_only_effective_and_future_shifts(self):
        old_team = self.env["fsm.team"].create({
            "member_ids": [(6, 0, [self.employee_one.id])],
        })
        new_team = self.env["fsm.team"].create({})
        historical = self._shift(
            self.employee_one,
            datetime(2026, 8, 17, 14, 0),
            datetime(2026, 8, 17, 23, 0),
            team=old_team,
        )
        future = self._shift(
            self.employee_one,
            datetime(2026, 8, 20, 14, 0),
            datetime(2026, 8, 20, 23, 0),
            team=old_team,
        )

        old_team.with_context(
            fsm_team_effective_date="2026-08-18",
        ).write({"member_ids": [(3, self.employee_one.id)]})
        new_team.with_context(
            fsm_team_effective_date="2026-08-18",
        ).write({"member_ids": [(4, self.employee_one.id)]})

        self.assertEqual(historical.fsm_team_id, old_team)
        self.assertEqual(future.fsm_team_id, new_team)
