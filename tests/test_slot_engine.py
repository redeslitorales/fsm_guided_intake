from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestSlotEngineOperationalStatus(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({
            "name": "Slot Engine Operational Status Test",
            "is_fsm": True,
            "company_id": cls.env.company.id,
        })
        cls.completed_stage = cls.env["project.task.type"].create({
            "name": "Completed (stale stage test)",
            "fold": True,
            "project_ids": [(4, cls.project.id)],
        })
        cls.team = cls.env["fsm.team"].create({
            "lead_user_id": cls.env.user.id,
            "warehouse_id": cls.env["stock.warehouse"].search([], limit=1).id,
        })
        cls.engine = cls.env["fsm.slot.engine"].with_context(
            tz="America/El_Salvador",
        )

    def _task(self, **values):
        task_values = {
            "name": "Scheduled task with stale completed stage",
            "active": True,
            "is_fsm": True,
            "project_id": self.project.id,
            "stage_id": self.completed_stage.id,
            "team_id": self.team.id,
            "user_ids": [(6, 0, [self.env.user.id])],
            "state": "01_in_progress",
            "fsm_done": False,
        }
        task_values.update(values)
        return self.env["project.task"].with_context(
            fsm_skip_auto_stage=True,
        ).create(task_values)

    def test_active_open_task_blocks_even_with_folded_completed_stage(self):
        task = self._task()

        self.assertTrue(self.engine._slot_task_blocks_availability(task))

    def test_explicitly_done_task_releases_availability(self):
        task = self._task(state="1_done", fsm_done=True)

        self.assertFalse(self.engine._slot_task_blocks_availability(task))

    def test_stale_stage_task_is_present_in_team_busy_intervals(self):
        start = fields.Datetime.now() + timedelta(days=1)
        end = start + timedelta(hours=4)
        task = self._task()
        task.with_context(fsm_skip_auto_stage=True).write({
            "planned_date_begin": start,
            "date_deadline": end,
            "allocated_hours": 4.0,
        })

        busy = self.engine._busy_intervals_by_team_utc(
            self.team,
            start - timedelta(hours=1),
            end + timedelta(hours=1),
        )

        self.assertIn((start, end), busy[self.team.id])
