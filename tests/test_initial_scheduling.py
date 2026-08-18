from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase


class TestInitialScheduling(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({
            "name": "Initial Scheduling Test",
            "is_fsm": True,
            "company_id": cls.env.company.id,
        })
        cls.to_schedule_stage = cls.env["project.task.type"].create({
            "name": "To Be Scheduled",
            "project_ids": [(4, cls.project.id)],
        })
        cls.scheduled_stage = cls.env["project.task.type"].create({
            "name": "Scheduled",
            "project_ids": [(4, cls.project.id)],
        })
        cls.rescheduled_stage = cls.env["project.task.type"].create({
            "name": "Rescheduled",
            "fold": True,
            "project_ids": [(4, cls.project.id)],
        })
        cls.planned_stage = cls.env["project.task.type"].create({
            "name": "Planned",
            "project_ids": [(4, cls.project.id)],
        })
        cls.task_type = cls.env["fsm.task.type"].create({
            "name": "Initial Installation Appointment",
            "project_id": cls.project.id,
            "default_stage_id": cls.to_schedule_stage.id,
            "default_planned_hours": 1.0,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Initial Scheduling Customer",
        })
        cls.team = cls.env["fsm.team"].create({
            "lead_user_id": cls.env.user.id,
            "warehouse_id": cls.env["stock.warehouse"].search([], limit=1).id,
        })

    def _new_task(self, **values):
        task_values = {
            "name": "Installation appointment",
            "is_fsm": True,
            "project_id": self.project.id,
            "stage_id": self.to_schedule_stage.id,
            "fsm_task_type_id": self.task_type.id,
            "partner_id": self.partner.id,
        }
        task_values.update(values)
        return self.env["project.task"].with_context(
            fsm_skip_auto_stage=True,
        ).create(task_values)

    def test_first_schedule_updates_existing_task_in_place(self):
        task = self._new_task()
        start = fields.Datetime.now() + timedelta(days=1)
        end = start + timedelta(hours=1)
        wizard = self.env["fsm.task.intake.wizard"].create({
            "reschedule_task_id": task.id,
            "appointment_start": start,
            "appointment_end": end,
            "team_id": self.team.id,
            "state": "schedule",
        })

        action = wizard._action_reschedule_task()

        self.assertEqual(action["res_id"], task.id)
        self.assertTrue(task.active)
        self.assertFalse(task.fsm_rescheduled_from_task_id)
        self.assertFalse(task.fsm_rescheduled_to_task_id)
        self.assertEqual(task.planned_date_begin, start)
        self.assertEqual(task.stage_id, self.scheduled_stage)

    def test_replacement_keeps_order_link_and_is_scheduled(self):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
        })
        start = fields.Datetime.now() + timedelta(days=1)
        task = self._new_task(
            planned_date_begin=start,
            sale_order_id=order.id,
            fsm_subscription_id=order.id,
        )

        replacement = task.reschedule_clone_to_new_task(
            start_dt_utc=start + timedelta(days=1),
            end_dt_utc=start + timedelta(days=1, hours=1),
            team=self.team,
            duration_hours=1.0,
            assignee_user_ids=[self.env.user.id],
        )

        self.assertEqual(replacement.sale_order_id, order)
        self.assertEqual(replacement.fsm_subscription_id, order)
        self.assertEqual(replacement.stage_id, self.scheduled_stage)

    def test_reschedule_search_does_not_start_from_past_appointment(self):
        task = self._new_task(
            stage_id=self.planned_stage.id,
            planned_date_begin=fields.Datetime.now() - timedelta(days=90),
        )
        wizard_model = self.env["fsm.change.appointment.wizard"].with_context(
            active_id=task.id,
            active_model="project.task",
            tz="America/El_Salvador",
        )
        before_utc = fields.Datetime.now()

        defaults = wizard_model.default_get(list(wizard_model._fields))

        before_local = fields.Datetime.context_timestamp(
            wizard_model, before_utc
        ).replace(tzinfo=None)
        self.assertGreaterEqual(
            fields.Datetime.to_datetime(defaults["search_start_dt"]),
            before_local,
        )

    def test_guided_reschedule_search_does_not_start_from_past_appointment(self):
        task = self._new_task(
            planned_date_begin=fields.Datetime.now() - timedelta(days=90),
        )
        wizard_model = self.env["fsm.task.intake.wizard"].with_context(
            reschedule_task_id=task.id,
            tz="America/El_Salvador",
        )
        before_utc = fields.Datetime.now()

        defaults = wizard_model.default_get(list(wizard_model._fields))

        before_local = fields.Datetime.context_timestamp(
            wizard_model, before_utc
        ).replace(tzinfo=None)
        self.assertGreaterEqual(
            fields.Datetime.to_datetime(defaults["search_start_dt"]),
            before_local,
        )
