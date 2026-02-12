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
    installation_task_type_id = fields.Many2one(
        "fsm.task.type",
        string="Installation Task Type",
        config_parameter="fsm_guided_intake.installation_task_type_id",
        help="Tasks of this type will automatically link to the subscription's installation_task_id field.",
    )
    fsm_cat6_cable_product_id = fields.Many2one(
        "product.product",
        string="Cat6 Cable Product",
        config_parameter="fsm_guided_intake.cat6_cable_product_id",
        help="Product to bill per meter when Cat6 cable meters are captured on a task.",
    )
    fsm_cat6_rj45_product_id = fields.Many2one(
        "product.product",
        string="RJ45 Connector Product",
        config_parameter="fsm_guided_intake.cat6_rj45_product_id",
        help="Product to bill per connector when RJ45 counts are captured on a task.",
    )
    fsm_cat6_wall_jack_product_id = fields.Many2one(
        "product.product",
        string="Wall Jack Product",
        config_parameter="fsm_guided_intake.cat6_wall_jack_product_id",
        help="Product to bill per unit when wall jacks are captured on a task.",
    )
