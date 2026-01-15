# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_fsm_intake_from_subscription(self):
        self.ensure_one()
        wizard_view = self.env.ref("fsm_guided_intake.fsm_task_intake_wizard_form", raise_if_not_found=False)
        return {
            "type": "ir.actions.act_window",
            "name": "Create Field Service Task",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "view_id": wizard_view.id if wizard_view else False,
            "target": "new",
            "context": {
                "default_partner_id": self.partner_id.id,
                "default_subscription_id": self.id,
                "default_state": "type",
            },
        }
