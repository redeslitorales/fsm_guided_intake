# -*- coding: utf-8 -*-
from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    fsm_auto_invoice_on_stage_done = fields.Boolean(
        string="Auto-create invoice draft when task reaches Done stage",
        config_parameter="fsm_guided_intake.auto_invoice_on_stage_done",
        default=False,
    )
    fsm_invoice_stage_done_name = fields.Char(
        string="Done Stage Name",
        config_parameter="fsm_guided_intake.invoice_stage_done_name",
        default="Done",
        help="When a task's stage name matches this value (case-insensitive), the system will prepare the Sales Order and create a draft invoice.",
    )
    fsm_slot_start_lead_minutes = fields.Integer(
        string="Slot Start Lead Minutes",
        config_parameter="fsm_guided_intake.slot_start_lead_minutes",
        default=0,
        help="Minutes to add after shift start before the first slot can begin.",
    )
