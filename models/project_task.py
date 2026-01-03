# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


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
    fsm_default_planned_hours = fields.Float(string="Default Planned Hours (Type)", copy=False)
    fsm_planned_hours_warning = fields.Boolean(
        string="Planned Hours Mismatch",
        compute="_compute_planned_hours_warning",
        store=True,
    )
    fsm_planned_hours_warning_text = fields.Char(
        string="Planned Hours Warning",
        compute="_compute_planned_hours_warning",
        store=True,
    )
    # quick helper: mark done button to create invoice later (v1: just creates SO if absent)
    # Fiber install worksheet (minimal field set)
    fsm_install_type = fields.Selection(
        [("new", "New Install"), ("reinstall", "Reinstall"), ("relocation", "Relocation")],
        string="Install Type",
        copy=False,
    )
    fsm_requires_fiber_install = fields.Boolean(
        string="Requires Fiber Install",
        related="fsm_task_type_id.requires_fiber_install",
        store=True,
        readonly=True,
    )
    fsm_pon_type = fields.Selection(
        [("gpon", "GPON"), ("xgspon", "XGS-PON")],
        string="PON Type",
        copy=False,
    )
    fsm_ont_serial = fields.Char(string="ONT Serial", copy=False)
    fsm_ont_pon_sn = fields.Char(string="ONT PON SN", copy=False)
    fsm_rx_dbm = fields.Float(string="RX Optical Power (dBm)", digits=(16, 2), copy=False)
    fsm_tx_dbm = fields.Float(string="TX Optical Power (dBm)", digits=(16, 2), copy=False)
    fsm_optics_in_spec = fields.Boolean(
        string="Optical Levels In Spec",
        compute="_compute_fsm_optics_in_spec",
        store=True,
    )
    fsm_authenticated = fields.Boolean(string="Authenticated", copy=False)
    fsm_speed_down = fields.Float(string="Speed Down (Mbps)", digits=(16, 2), copy=False)
    fsm_speed_up = fields.Float(string="Speed Up (Mbps)", digits=(16, 2), copy=False)
    fsm_cat6_installed = fields.Boolean(string="Cat6 Installed", copy=False)
    fsm_cat6_notes = fields.Text(string="Cat6 Notes", copy=False)
    fsm_install_complete = fields.Boolean(
        string="Install Worksheet Complete",
        compute="_compute_fsm_install_complete",
        store=True,
    )

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

    @api.depends("fsm_rx_dbm", "fsm_tx_dbm", "fsm_task_type_id.optics_rx_min", "fsm_task_type_id.optics_rx_max", "fsm_task_type_id.optics_tx_min", "fsm_task_type_id.optics_tx_max")
    def _compute_fsm_optics_in_spec(self):
        for task in self:
            if task.fsm_rx_dbm is False or task.fsm_tx_dbm is False:
                task.fsm_optics_in_spec = False
                continue
            rx_min = task.fsm_task_type_id.optics_rx_min if task.fsm_task_type_id else -27.0
            rx_max = task.fsm_task_type_id.optics_rx_max if task.fsm_task_type_id else -8.0
            tx_min = task.fsm_task_type_id.optics_tx_min if task.fsm_task_type_id else 0.5
            tx_max = task.fsm_task_type_id.optics_tx_max if task.fsm_task_type_id else 5.0
            task.fsm_optics_in_spec = (rx_min <= task.fsm_rx_dbm <= rx_max) and (tx_min <= task.fsm_tx_dbm <= tx_max)

    @api.depends("fsm_pon_type", "fsm_ont_serial", "fsm_ont_pon_sn", "fsm_rx_dbm", "fsm_tx_dbm", "fsm_optics_in_spec", "fsm_authenticated", "fsm_speed_down", "fsm_speed_up", "fsm_cat6_installed", "fsm_cat6_notes")
    def _compute_fsm_install_complete(self):
        for task in self:
            cat6_ok = True
            if task.fsm_cat6_installed:
                cat6_ok = bool(task.fsm_cat6_notes)
            required = [
                task.fsm_pon_type,
                task.fsm_ont_serial,
                task.fsm_ont_pon_sn,
                task.fsm_rx_dbm,
                task.fsm_tx_dbm,
                task.fsm_authenticated,
                task.fsm_speed_down,
                task.fsm_speed_up,
                task.fsm_optics_in_spec,
                cat6_ok,
            ]
            task.fsm_install_complete = all(required)

    @api.depends("fsm_default_planned_hours")
    def _compute_planned_hours_warning(self):
        for task in self:
            warn = False
            text = False
            planned = task.planned_hours if "planned_hours" in task._fields else False
            if task.fsm_default_planned_hours and planned:
                if abs(planned - task.fsm_default_planned_hours) > 0.01:
                    warn = True
                    text = _("Planned hours differ from task type default: %s (planned) vs %s (default).") % (
                        planned,
                        task.fsm_default_planned_hours,
                    )
            task.fsm_planned_hours_warning = warn
            task.fsm_planned_hours_warning_text = text

    @api.model
    def create(self, vals):
        tasks = super().create(vals)
        if "planned_hours" in vals or "fsm_default_planned_hours" in vals:
            tasks._compute_planned_hours_warning()
        return tasks

    def write(self, vals):
        res = super().write(vals)
        if "planned_hours" in vals or "fsm_default_planned_hours" in vals:
            self._compute_planned_hours_warning()
        return res

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
        if "stage_id" in vals:
            new_stage = self.env["project.task.type"].browse(vals["stage_id"])
            if new_stage and new_stage.fold:
                for task in self:
                    if task.fsm_task_type_id and task.fsm_task_type_id.enforce_install_validation and not task.fsm_install_complete:
                        raise ValidationError(_(
                            "Cannot mark this task as done until the install worksheet is complete and optical levels are in range."
                        ))
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

    def send_whatsapp(self):
        """Stub method to satisfy enterprise FSM view validation.
        The actual WhatsApp sending may be provided by a separate integration module.
        """
        return True
