# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class ProjectTaskMaterial(models.Model):
    _name = "fsm.task.material"
    _description = "FSM Task Material"
    _order = "task_id, id"

    task_id = fields.Many2one("project.task", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    product_uom = fields.Many2one("uom.uom", related="product_id.uom_id", readonly=True)
    product_uom_qty = fields.Float(default=1.0)
    lot_id = fields.Many2one("stock.lot", string="Serial/Lot",
                             domain="[('product_id', '=', product_id)]")
    lot_ids = fields.Many2many("stock.lot", string="Serials", domain="[('product_id', '=', product_id)]")
    is_service = fields.Boolean(compute="_compute_is_service", store=True)

    @api.depends("product_id")
    def _compute_is_service(self):
        for rec in self:
            rec.is_service = rec.product_id and rec.product_id.type == "service"

class ProjectTask(models.Model):
    _inherit = "project.task"

    fsm_task_type_id = fields.Many2one("fsm.task.type", string="Task Type", copy=False)
    fsm_service_address_id = fields.Many2one("res.partner", string="Service Address", copy=False)
    fsm_service_zone_name = fields.Char(string="Service Zone", copy=False)
    fsm_booking_id = fields.Many2one("fsm.booking", string="Booking", copy=False)
    fsm_material_ids = fields.One2many("fsm.task.material", "task_id", string="Materials/Services", copy=False)
    fsm_invoiced = fields.Boolean(string="FSM Invoiced", default=False, copy=False)
    fsm_last_invoiced_so_id = fields.Many2one("sale.order", string="Last Invoiced SO", copy=False)

    # quick helper: mark done button to create invoice later (v1: just creates SO if absent)

def _fsm_create_draft_invoice(self):
    """Create/Update SO from task materials and create a draft invoice (account.move).
    This does NOT post the invoice. It marks task as fsm_invoiced to avoid duplicates.
    """
    AccountMove = self.env["account.move"]
    for task in self:
        if task.fsm_invoiced:
            continue
        # Prepare SO lines
        task.action_fsm_prepare_invoice()
        so = task.sale_order_id
        if not so:
            continue
        # Create draft invoice from SO
        inv = so._create_invoices()
        if inv:
            # leave draft; do not post
            task.fsm_invoiced = True
            task.fsm_last_invoiced_so_id = so.id
    return True

    def action_fsm_prepare_invoice(self):
        """V1: Create/Update a Sales Order linked to the task partner with task materials.
        Invoicing policy (when to invoice) is usually controlled by products; you can invoice at close.
        This method prepares the SO so accounting can invoice it.
        """
        for task in self:
            if not task.partner_id:
                raise UserError(_("Set a customer first."))
            so = task.sale_order_id
            if not so:
                so = self.env["sale.order"].create({
                    "partner_id": task.partner_id.id,
                    "origin": task.display_name,
                })
                task.sale_order_id = so.id
            # add lines
            for ml in task.fsm_material_ids.filtered(lambda l: l.product_uom_qty > 0 and l.product_id.fsm_bill_from_task):
                self.env["sale.order.line"].create({
                    "order_id": so.id,
                    "product_id": ml.product_id.id,
                    "product_uom_qty": ml.product_uom_qty,
                })
        return True


def write(self, vals):
    res = super().write(vals)
    if "stage_id" in vals:
        auto = self.env["ir.config_parameter"].sudo().get_param("fsm_guided_intake.auto_invoice_on_stage_done", default="False")
        stage_name = (self.env["ir.config_parameter"].sudo().get_param("fsm_guided_intake.invoice_stage_done_name", default="Done") or "Done").strip().lower()
        if auto in ("True", True, "1", 1):
            for task in self:
                if task.fsm_invoiced:
                    continue
                if task.stage_id and (task.stage_id.name or "").strip().lower() == stage_name:
                    # Only invoice once, and only if there are materials/services
                    if task.fsm_material_ids:
                        task._fsm_create_draft_invoice()
    return res

@api.model
def _fsm_cron_auto_invoice_done_tasks(self):
    auto = self.env["ir.config_parameter"].sudo().get_param("fsm_guided_intake.auto_invoice_on_stage_done", default="False")
    stage_name = (self.env["ir.config_parameter"].sudo().get_param("fsm_guided_intake.invoice_stage_done_name", default="Done") or "Done").strip().lower()
    if auto not in ("True", True, "1", 1):
        return True
    done_stages = self.env["project.task.type"].search([("name", "ilike", stage_name)])
    if not done_stages:
        return True
    tasks = self.search([("stage_id", "in", done_stages.ids), ("fsm_invoiced", "=", False)])
    for t in tasks:
        if t.fsm_material_ids:
            t._fsm_create_draft_invoice()
    return True
