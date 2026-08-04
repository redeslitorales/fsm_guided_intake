# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from datetime import datetime, timedelta, time


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
    _FSM_INTAKE_WIZARD_STATES = {"customer", "type", "products", "schedule", "notes", "confirm"}

    fsm_task_type_id = fields.Many2one("fsm.task.type", string="Task Type", copy=False)
    fsm_service_address_id = fields.Many2one("res.partner", string="Service Address", copy=False)
    fsm_service_zone_name = fields.Char(string="Service Zone", copy=False)
    fsm_booking_id = fields.Many2one("fsm.booking", string="Booking", copy=False)
    fsm_subscription_id = fields.Many2one(
        "sale.order",
        string="Subscription",
        copy=True,
        index=True,
        ondelete="set null",
        domain="[('is_subscription', '=', True)]",
        help="Customer subscription selected when this task was created through FSM intake.",
    )
    fsm_latitude = fields.Float(
        string="GPS Latitude",
        digits=(16, 7),
        copy=True,
        help="Service coordinates copied from the customer or service-address partner.",
    )
    fsm_longitude = fields.Float(
        string="GPS Longitude",
        digits=(16, 7),
        copy=True,
        help="Service coordinates copied from the customer or service-address partner.",
    )
    fsm_geo_edit_mode = fields.Boolean(
        compute="_compute_fsm_geo_edit_mode",
    )
    fsm_task_type_edit_mode = fields.Boolean(
        compute="_compute_fsm_task_type_edit_mode",
    )
    team_id = fields.Many2one("fsm.team", string="FSM Team", copy=False, help="Assigned field service team")
    fsm_material_ids = fields.One2many("fsm.task.material", "task_id", string="Materials/Services", copy=False)
    fsm_invoiced = fields.Boolean(string="FSM Invoiced", default=False, copy=False)
    fsm_last_invoiced_so_id = fields.Many2one("sale.order", string="Last Invoiced SO", copy=False)
    fsm_default_planned_hours = fields.Float(string="Default Planned Hours (Type)", copy=False)
    bonus_points = fields.Integer(
        string="Bonus Points",
        default=0,
        copy=False,
        help="Points value used for bonus calculations.",
    )
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
    fsm_requires_iptv_install = fields.Boolean(
        string="Requires IPTV Install",
        related="fsm_task_type_id.requires_iptv_install",
        store=True,
        readonly=True,
    )
    fsm_requires_photos = fields.Boolean(
        string="Requires Photos",
        related="fsm_task_type_id.requires_photos",
        store=True,
        readonly=True,
    )
    fsm_task_type_enforce_validation = fields.Boolean(
        string="Task Type Enforces Install Validation",
        related="fsm_task_type_id.enforce_install_validation",
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
    fsm_rx_dbm = fields.Float(string="RX (1490) Optical Power (dBm)", digits=(16, 2), copy=False)
    fsm_tx_dbm = fields.Float(string="TX (1310) Optical Power (dBm)", digits=(16, 2), copy=False)
    fsm_optics_in_spec = fields.Boolean(
        string="Optical Levels In Spec",
        compute="_compute_fsm_optics_in_spec",
        store=True,
    )
    fsm_validation_type = fields.Selection(
        [("cabled", "Cabled"), ("wireless", "Wireless")],
        string="Validation Type",
        copy=False,
    )
    fsm_authenticated = fields.Boolean(string="Authenticated", copy=False)
    fsm_speed_down = fields.Float(string="Speed Down (Mbps)", digits=(16, 2), copy=False)
    fsm_speed_up = fields.Float(string="Speed Up (Mbps)", digits=(16, 2), copy=False)
    fsm_cat6_installed = fields.Boolean(string="Cat6 Installed", copy=False)
    fsm_cat6_meters = fields.Float(string="Cable Meters", digits=(16, 2), copy=False, help="Meters of Cat6 cable installed")
    fsm_cat6_rj45 = fields.Integer(string="RJ45 Connectors", copy=False, help="Number of RJ45 connectors installed")
    fsm_cat6_wall_jacks = fields.Integer(string="Wall Jacks", copy=False, help="Number of wall jacks installed")
    fsm_cat6_notes = fields.Text(string="Cat6 Notes", copy=False)
    
    # Fiber infrastructure fields
    fsm_distribution_box = fields.Char(string="Distribution Box", copy=False)
    fsm_splitter_thread = fields.Char(string="Splitter Thread", copy=False)
    fsm_drop_cable_start = fields.Char(string="Drop Cable Start", copy=False)
    fsm_drop_cable_end = fields.Char(string="Drop Cable End", copy=False)
    fsm_customer_signature = fields.Binary(string="Customer Signature", copy=False, attachment=True)
    fsm_photo_attachment_ids = fields.Many2many(
        "ir.attachment",
        "project_task_fsm_photo_rel",
        "task_id",
        "attachment_id",
        string="Photo Evidence",
        copy=False,
    )
    
    fsm_install_complete = fields.Boolean(
        string="Install Worksheet Complete",
        compute="_compute_fsm_install_complete",
        store=True,
    )
    
    # IPTV related fields from sale_order_id
    iptv_service_id = fields.Integer(
        string='IPTV Service ID',
        related='sale_order_id.iptv_service_id',
        readonly=True,
        store=False
    )
    iptv_status = fields.Selection(
        related='sale_order_id.iptv_status',
        readonly=True,
        store=False
    )
    iptv_stb_ids = fields.One2many(
        'iptv.stb',
        'order_id',
        string='STBs',
        related='sale_order_id.iptv_stb_ids',
        readonly=False
    )
    iptv_max_sessions = fields.Integer(
        related='sale_order_id.iptv_max_sessions',
        readonly=True,
        store=False
    )
    iptv_can_add_stb = fields.Boolean(
        related='sale_order_id.iptv_can_add_stb',
        readonly=True,
        store=False
    )
    
    fsm_task_count = fields.Integer(
        string="Tasks",
        default=1,
        aggregator="sum",
        readonly=True,
    )

    # --- Stage helpers -------------------------------------------------
    def _fsm_get_team_assignee_user_ids(self, team):
        """Return assignees for a team: all member users plus team lead."""
        if not team:
            return []
        users = team.member_ids.mapped("user_id")
        if team.lead_user_id:
            users |= team.lead_user_id
        return users.ids

    @api.onchange("team_id")
    def _onchange_team_id_sync_assignees(self):
        """Refresh assignees when team changes in the form."""
        for task in self:
            if "user_ids" not in task._fields:
                continue
            assignee_ids = task._fsm_get_team_assignee_user_ids(task.team_id)
            task.user_ids = [(6, 0, assignee_ids)]

    def _fsm_find_stage(self, names):
        """Find a stage by name (case-insensitive), preferring the task's project.

        The lookup tries each candidate name with the project filter first, then
        without project restriction as a fallback.
        """
        Stage = self.env["project.task.type"]
        for name in names:
            domain = [("name", "ilike", name)]
            if self.project_id:
                domain = [("project_ids", "in", self.project_id.id)] + domain
            stage = Stage.search(domain, limit=1)
            if stage:
                return stage
        for name in names:
            stage = Stage.search([("name", "ilike", name)], limit=1)
            if stage:
                return stage
        return False

    def _fsm_apply_unscheduled_stage(self):
        """Move tasks without a planned start to the unscheduled stage.

        Uses common stage labels to avoid hard-coded IDs. Skips folded stages to
        avoid re-opening done tasks.
        """
        unscheduled_candidates = ["to be scheduled", "to schedule", "new"]
        for task in self:
            if task.planned_date_begin:
                continue
            if task.stage_id and task.stage_id.fold:
                continue
            stage = task._fsm_find_stage(unscheduled_candidates)
            if stage and task.stage_id != stage:
                task.with_context(fsm_skip_auto_stage=True).write({"stage_id": stage.id})

    def _fsm_apply_scheduled_stage(self):
        """Move tasks with a planned date to the scheduled stage.

        Uses common stage labels to avoid hard-coded IDs. Skips folded stages to
        avoid moving already closed tasks.
        """
        # Include common Spanish labels so scheduling works across translations
        scheduled_candidates = [
            "scheduled",
            "planned",
            "planificado",
            "programado",
            "agendado",
        ]
        for task in self:
            has_schedule = bool(task.planned_date_begin or task.date_deadline)
            if not has_schedule:
                continue
            if task.stage_id and task.stage_id.fold:
                continue
            stage = task._fsm_find_stage(scheduled_candidates)
            if stage and task.stage_id != stage:
                task.with_context(fsm_skip_auto_stage=True).write({"stage_id": stage.id})

    def _fsm_stage_is_done(self, stage):
        """Return True when a stage represents a done/closed state."""
        if not stage:
            return False
        if stage.fold:
            return True
        if "is_closed" in stage._fields and stage.is_closed:
            return True
        if "closed" in stage._fields and stage.closed:
            return True
        return False

    def _link_installation_task_to_subscription(self):
        """Link task to subscription's installation_task_id if task type matches setting."""
        installation_type_id = int(
            self.env['ir.config_parameter'].sudo().get_param(
                'fsm_guided_intake.installation_task_type_id', '0'
            )
        )
        if not installation_type_id:
            return

        for task in self:
            sale_order = task.sale_order_id
            if (
                task.fsm_task_type_id.id == installation_type_id
                and sale_order
                and "installation_task_id" in sale_order._fields
                and sale_order.installation_task_id != task
            ):
                sale_order.installation_task_id = task.id

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

    @api.model_create_multi
    def create(self, vals_list):
        create_ctx = dict(self.env.context)
        create_ctx.pop("default_state", None)
        create_ctx.pop("state", None)
        create_self = self.with_context(create_ctx)

        normalized_vals_list = []
        should_compute_warning = False
        for vals in vals_list:
            new_vals = dict(vals)
            if new_vals.get("state") in self._FSM_INTAKE_WIZARD_STATES:
                new_vals.pop("state", None)
            if "bonus_points" not in new_vals and new_vals.get("fsm_task_type_id"):
                task_type = create_self.env["fsm.task.type"].browse(new_vals["fsm_task_type_id"])
                if task_type.exists():
                    new_vals["bonus_points"] = task_type.bonus_base_points or 0
            if "planned_hours" in new_vals or "fsm_default_planned_hours" in new_vals:
                should_compute_warning = True

            coordinate_partner_id = (
                new_vals.get("fsm_service_address_id")
                or new_vals.get("partner_id")
            )
            coordinate_partner = create_self.env["res.partner"].browse(
                coordinate_partner_id
            ).exists()
            if coordinate_partner:
                if "fsm_latitude" not in new_vals:
                    new_vals["fsm_latitude"] = coordinate_partner.partner_latitude
                if "fsm_longitude" not in new_vals:
                    new_vals["fsm_longitude"] = coordinate_partner.partner_longitude

            if "team_id" in new_vals and "user_ids" not in new_vals and "user_ids" in self._fields:
                team = create_self.env["fsm.team"].browse(new_vals.get("team_id")) if new_vals.get("team_id") else False
                assignee_ids = self._fsm_get_team_assignee_user_ids(team) if team and team.exists() else []
                new_vals["user_ids"] = [(6, 0, assignee_ids)]

            normalized_vals_list.append(new_vals)

        tasks = super(ProjectTask, create_self).create(normalized_vals_list)
        tasks._link_installation_task_to_subscription()

        if should_compute_warning:
            tasks._compute_planned_hours_warning()

        if not create_self.env.context.get("fsm_skip_auto_stage"):
            for task in tasks:
                if task.planned_date_begin:
                    task._fsm_apply_scheduled_stage()
                else:
                    task._fsm_apply_unscheduled_stage()
        return tasks

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
            if task.fsm_cat6_installed:
                config = self.env["ir.config_parameter"].sudo()
                cat6_map = [
                    ("fsm_guided_intake.cat6_cable_product_id", task.fsm_cat6_meters),
                    ("fsm_guided_intake.cat6_rj45_product_id", task.fsm_cat6_rj45),
                    ("fsm_guided_intake.cat6_wall_jack_product_id", task.fsm_cat6_wall_jacks),
                ]
                for param_key, qty in cat6_map:
                    if not qty or qty <= 0:
                        continue
                    product_id = int(config.get_param(param_key, "0") or "0")
                    if not product_id:
                        continue
                    product = self.env["product.product"].browse(product_id)
                    if not product.exists():
                        continue
                    self.env["sale.order.line"].create({
                        "order_id": so.id,
                        "product_id": product.id,
                        "product_uom_qty": qty,
                    })
        return True

    def write(self, vals):
        skip_auto_stage = self.env.context.get("fsm_skip_auto_stage")
        force_close_bypass = bool(
            self.env.su
            and self.env.context.get("fsm_force_close_bypass_completion")
        )
        coordinate_fields = {"fsm_latitude", "fsm_longitude"}
        coordinate_updates = {}
        if coordinate_fields.intersection(vals):
            if "fsm_latitude" in vals and not -90 <= float(vals["fsm_latitude"] or 0.0) <= 90:
                raise ValidationError(_("GPS latitude must be between -90 and 90."))
            if "fsm_longitude" in vals and not -180 <= float(vals["fsm_longitude"] or 0.0) <= 180:
                raise ValidationError(_("GPS longitude must be between -180 and 180."))
            coordinate_updates = {
                task.id: (task.fsm_latitude, task.fsm_longitude)
                for task in self
            }
        team_changed_ids = set()
        if "team_id" in vals and "team_id" in self._fields:
            incoming_team_id = vals.get("team_id") or False
            team_changed_ids = {task.id for task in self if task.team_id.id != incoming_team_id}

        planned_date_in_vals = "planned_date_begin" in vals
        planned_date_value = vals.get("planned_date_begin")
        stage_change_requested = False
        new_stage = False
        if "stage_id" in vals:
            new_stage = self.env["project.task.type"].browse(vals["stage_id"])
            if new_stage and new_stage.fold and not force_close_bypass:
                for task in self:
                    if task.fsm_task_type_id and task.fsm_task_type_id.enforce_install_validation and not task.fsm_install_complete:
                        raise ValidationError(_(
                            "Cannot mark this task as done until the install worksheet is complete and optical levels are in range."
                        ))
            stage_change_requested = any(task.stage_id.id != vals["stage_id"] for task in self)
        if "fsm_done" in self._fields and vals.get("fsm_done"):
            for task in self:
                target_stage = new_stage or task.stage_id
                if not task._fsm_stage_is_done(target_stage):
                    raise ValidationError(_("Move the task to a Done stage before marking it done."))
        res = super().write(vals)

        if coordinate_updates and not self.env.context.get("fsm_skip_coordinate_sync"):
            for task in self:
                old_latitude, old_longitude = coordinate_updates[task.id]
                if (
                    old_latitude == task.fsm_latitude
                    and old_longitude == task.fsm_longitude
                ):
                    continue
                coordinate_partner = task.fsm_service_address_id or task.partner_id
                if coordinate_partner:
                    coordinate_partner.sudo().write({
                        "partner_latitude": task.fsm_latitude,
                        "partner_longitude": task.fsm_longitude,
                    })
                task.message_post(
                    body=Markup(
                        "<p><strong>%s</strong></p>"
                        "<p>%s: %s &rarr; %s<br/>%s: %s &rarr; %s</p>"
                        "<p>%s: %s</p>"
                    ) % (
                        _("GPS coordinates updated"),
                        _("Latitude"),
                        task._fsm_format_coordinate(old_latitude),
                        task._fsm_format_coordinate(task.fsm_latitude),
                        _("Longitude"),
                        task._fsm_format_coordinate(old_longitude),
                        task._fsm_format_coordinate(task.fsm_longitude),
                        _("Partner/Service Address"),
                        coordinate_partner.display_name if coordinate_partner else _("Not set"),
                    ),
                    subtype_xmlid="mail.mt_note",
                )
                if task.partner_id:
                    task.partner_id.sudo().message_post(
                        author_id=self.env.user.partner_id.id,
                        body=Markup(
                            "<p><strong>%s</strong></p>"
                            "<p>%s: <a href=\"/web#id=%s&amp;model=project.task&amp;view_type=form\">%s</a></p>"
                            "<p>%s: %s &rarr; %s<br/>%s: %s &rarr; %s</p>"
                            "<p>%s: %s</p>"
                        ) % (
                            _("GPS coordinates updated"),
                            _("Task"),
                            task.id,
                            task.display_name,
                            _("Latitude"),
                            task._fsm_format_coordinate(old_latitude),
                            task._fsm_format_coordinate(task.fsm_latitude),
                            _("Longitude"),
                            task._fsm_format_coordinate(old_longitude),
                            task._fsm_format_coordinate(task.fsm_longitude),
                            _("Partner/Service Address"),
                            coordinate_partner.display_name if coordinate_partner else _("Not set"),
                        ),
                        subtype_xmlid="mail.mt_note",
                    )
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
        if not skip_auto_stage and not stage_change_requested:
            if planned_date_in_vals or "date_deadline" in vals:
                unscheduled_stage = self._fsm_find_stage(["to be scheduled", "to schedule", "new"])
                to_schedule = self.filtered(lambda t: unscheduled_stage and t.stage_id == unscheduled_stage and (t.planned_date_begin or t.date_deadline))
                if to_schedule:
                    to_schedule._fsm_apply_scheduled_stage()
            elif planned_date_in_vals and not planned_date_value:
                self._fsm_apply_unscheduled_stage()

        if "fsm_task_type_id" in vals or "sale_order_id" in vals:
            self._link_installation_task_to_subscription()

        if "planned_hours" in vals or "fsm_default_planned_hours" in vals:
            self._compute_planned_hours_warning()

        if team_changed_ids and "user_ids" in self._fields:
            changed_tasks = self.browse(list(team_changed_ids))
            for task in changed_tasks:
                assignee_ids = task._fsm_get_team_assignee_user_ids(task.team_id)
                task.write({"user_ids": [(6, 0, assignee_ids)]})

        return res

    @api.model
    def _fsm_format_coordinate(self, value):
        return f"{value:.7f}" if value else _("Not set")

    @api.depends_context("fsm_geo_edit_unlocked")
    def _compute_fsm_geo_edit_mode(self):
        unlocked = bool(self.env.context.get("fsm_geo_edit_unlocked"))
        for task in self:
            task.fsm_geo_edit_mode = unlocked

    def _fsm_geo_edit_action(self, unlocked):
        self.ensure_one()
        action_context = dict(self.env.context)
        action_context["fsm_geo_edit_unlocked"] = unlocked
        action_context["form_view_initial_mode"] = "edit" if unlocked else "readonly"
        return {
            "type": "ir.actions.act_window",
            "name": self.display_name,
            "res_model": "project.task",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": action_context,
        }

    def action_enable_fsm_geo_edit(self):
        return self._fsm_geo_edit_action(True)

    def action_disable_fsm_geo_edit(self):
        return self._fsm_geo_edit_action(False)

    @api.depends_context("fsm_task_type_edit_unlocked")
    def _compute_fsm_task_type_edit_mode(self):
        unlocked = bool(self.env.context.get("fsm_task_type_edit_unlocked"))
        is_fsm_manager = self.env.user.has_group("industry_fsm.group_fsm_manager")
        for task in self:
            task.fsm_task_type_edit_mode = unlocked and is_fsm_manager

    def _fsm_task_type_edit_action(self, unlocked):
        self.ensure_one()
        if unlocked and not self.env.user.has_group("industry_fsm.group_fsm_manager"):
            raise AccessError(_("Only Field Service managers can change the task type."))

        action_context = dict(self.env.context)
        action_context["fsm_task_type_edit_unlocked"] = unlocked
        action_context["form_view_initial_mode"] = "edit" if unlocked else "readonly"
        return {
            "type": "ir.actions.act_window",
            "name": self.display_name,
            "res_model": "project.task",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
            "context": action_context,
        }

    def action_enable_fsm_task_type_edit(self):
        return self._fsm_task_type_edit_action(True)

    def action_disable_fsm_task_type_edit(self):
        return self._fsm_task_type_edit_action(False)

    @api.model
    def _fsm_backfill_partner_coordinates(self):
        """Initialize existing FSM task GPS and unambiguous active subscriptions."""
        self.env.cr.execute("""
            UPDATE project_task AS task
               SET fsm_latitude = partner.partner_latitude,
                   fsm_longitude = partner.partner_longitude
              FROM res_partner AS partner,
                   project_project AS project
             WHERE project.id = task.project_id
               AND project.is_fsm = TRUE
               AND partner.id = COALESCE(task.fsm_service_address_id, task.partner_id)
               AND COALESCE(task.fsm_latitude, 0) = 0
               AND COALESCE(task.fsm_longitude, 0) = 0
               AND COALESCE(partner.partner_latitude, 0) != 0
               AND COALESCE(partner.partner_longitude, 0) != 0
        """)
        self.env.cr.execute("""
            WITH single_active_subscription AS (
                SELECT partner_id,
                       MIN(id) AS subscription_id
                  FROM sale_order
                 WHERE is_subscription = TRUE
                   AND subscription_state IN ('3_progress', '4_paused', '8_suspend')
                   AND partner_id IS NOT NULL
                 GROUP BY partner_id
                HAVING COUNT(*) = 1
            )
            UPDATE project_task AS task
               SET fsm_subscription_id = active.subscription_id,
                   sale_order_id = COALESCE(task.sale_order_id, active.subscription_id)
              FROM single_active_subscription AS active,
                   project_project AS project
             WHERE project.id = task.project_id
               AND project.is_fsm = TRUE
               AND active.partner_id = task.partner_id
               AND task.fsm_subscription_id IS NULL
        """)
        return True

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

    def _write_scheduled_datetime(self, start_dt_utc, end_dt_utc, duration_hours=None, team=None, assignee_user_ids=None):
        """Apply schedule/team to an existing task and keep booking in sync."""
        self.ensure_one()

        if not start_dt_utc or not end_dt_utc or end_dt_utc <= start_dt_utc:
            raise ValidationError(_("The planned start date must be before the planned end date."))

        duration_hours = duration_hours if duration_hours is not None else (end_dt_utc - start_dt_utc).total_seconds() / 3600.0
        assignee_user_ids = assignee_user_ids or []
        if not assignee_user_ids and self.user_ids:
            assignee_user_ids = self.user_ids.ids

        write_vals = {
            "planned_date_begin": start_dt_utc,
        }

        if "planned_date_end" in self._fields:
            write_vals["planned_date_end"] = end_dt_utc
        if "planned_hours" in self._fields:
            write_vals["planned_hours"] = duration_hours
            write_vals["fsm_default_planned_hours"] = self.fsm_default_planned_hours or duration_hours
        if "date_start" in self._fields:
            write_vals["date_start"] = start_dt_utc
        if "date_end" in self._fields:
            write_vals["date_end"] = end_dt_utc
        if "date_deadline" in self._fields and end_dt_utc:
            deadline_dt = end_dt_utc
            if isinstance(deadline_dt, datetime) and deadline_dt.time() != time.min:
                deadline_dt = deadline_dt + timedelta(days=1)
            write_vals["date_deadline"] = fields.Date.to_date(deadline_dt)
        if "team_id" in self._fields and team:
            write_vals["team_id"] = team.id
        if assignee_user_ids and "user_ids" in self._fields:
            write_vals["user_ids"] = [(6, 0, assignee_user_ids)]

        booking = self.fsm_booking_id.sudo() if self.fsm_booking_id else False
        if booking:
            booking.write({
                "team_id": team.id if team else booking.team_id.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })
        elif team:
            booking_ctx = dict(self.env.context)
            booking_ctx.pop("default_state", None)
            booking_ctx.pop("state", None)
            booking = self.env["fsm.booking"].with_context(booking_ctx).sudo().create({
                "task_id": self.id,
                "team_id": team.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })

        if booking:
            write_vals["fsm_booking_id"] = booking.id
            booking.with_context(self.env.context).action_create_or_update_delivery()

        return self.with_context(fsm_skip_auto_stage=True).sudo().write(write_vals)

    def reschedule_clone_to_new_task(self, start_dt_utc, end_dt_utc, team, duration_hours, notes=None, assignee_user_ids=None):
        """Create a new task for the reschedule, archive the current one, and reuse the booking.

        The caller must pass UTC-naive datetimes to avoid double conversions. Booking (and picking)
        are moved forward to the new task to prevent duplicate stock reservations.
        """
        self.ensure_one()

        # Build audit note
        tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        old_start_local = False
        if self.planned_date_begin:
            old_start_local = fields.Datetime.context_timestamp(self.with_context(tz=tz_name), self.planned_date_begin)
        new_start_local = fields.Datetime.context_timestamp(self.with_context(tz=tz_name), start_dt_utc) if start_dt_utc else False
        not_set_label = _("Not set")
        old_start_str = old_start_local.strftime("%Y-%m-%d %H:%M") if old_start_local else not_set_label
        new_start_str = new_start_local.strftime("%Y-%m-%d %H:%M") if new_start_local else not_set_label

        timestamp = fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note_header = _("\n\n=== Appointment Rescheduled (%s) ===\n") % timestamp
        note_text = note_header + _("%(previous_label)s: %(previous)s\n%(new_label)s: %(new)s\n") % {
            "previous_label": _("Previous appointment"),
            "previous": old_start_str,
            "new_label": _("New appointment"),
            "new": new_start_str,
        }
        if notes:
            note_text += _("%(reason_label)s: %(reason)s\n") % {
                "reason_label": _("Reason"),
                "reason": notes,
            }

        # Prepare assignees
        assignee_user_ids = assignee_user_ids or []
        if not assignee_user_ids and self.user_ids:
            assignee_user_ids = self.user_ids.ids

        rescheduled_stage = self._fsm_find_stage(["rescheduled"])

        write_vals = {
            "description": (self.description or "") + note_text,
            "fsm_service_zone_name": self.fsm_service_zone_name,
            "planned_date_begin": start_dt_utc,
        }

        if "planned_date_end" in self._fields:
            write_vals["planned_date_end"] = end_dt_utc
        if "planned_hours" in self._fields:
            write_vals["planned_hours"] = duration_hours
            write_vals["fsm_default_planned_hours"] = self.fsm_default_planned_hours or duration_hours
        if "date_start" in self._fields:
            write_vals["date_start"] = start_dt_utc
        if "date_end" in self._fields:
            write_vals["date_end"] = end_dt_utc
        if "date_deadline" in self._fields and end_dt_utc:
            deadline_dt = end_dt_utc
            if isinstance(deadline_dt, datetime) and deadline_dt.time() != time.min:
                deadline_dt = deadline_dt + timedelta(days=1)
            write_vals["date_deadline"] = fields.Date.to_date(deadline_dt)
        if rescheduled_stage:
            write_vals["stage_id"] = rescheduled_stage.id
        if "team_id" in self._fields and team:
            write_vals["team_id"] = team.id
        if assignee_user_ids and "user_ids" in self._fields:
            write_vals["user_ids"] = [(6, 0, assignee_user_ids)]

        # Update booking in place to preserve delivery links
        booking = self.fsm_booking_id.sudo() if self.fsm_booking_id else False
        if booking:
            booking.write({
                "team_id": team.id if team else booking.team_id.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })
        elif team:
            booking_ctx = dict(self.env.context)
            booking_ctx.pop("default_state", None)
            booking_ctx.pop("state", None)
            booking = self.env["fsm.booking"].with_context(booking_ctx).sudo().create({
                "task_id": self.id,
                "team_id": team.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })
        if booking:
            write_vals["fsm_booking_id"] = booking.id
            booking.with_context(self.env.context).action_create_or_update_delivery()

        self.with_context(fsm_skip_auto_stage=True).sudo().write(write_vals)

        self.message_post(
            body=_("This task was rescheduled. Previous time: %s, New time: %s") % (old_start_str, new_start_str),
            message_type="comment",
        )

        return self

    def send_whatsapp(self):
        """Stub method to satisfy enterprise FSM view validation.
        The actual WhatsApp sending may be provided by a separate integration module.
        """
        return True
    
    def action_activate_iptv_from_task(self):
        """Activate IPTV service from the task"""
        self.ensure_one()
        if not self.sale_order_id:
            raise ValidationError(_("No subscription found for this task."))
        return self.sale_order_id.action_activate_iptv()
    
    def action_refresh_iptv_from_subscription(self):
        """Refresh IPTV data from the subscription"""
        self.ensure_one()
        if not self.sale_order_id:
            raise ValidationError(_("No subscription found for this task."))
        return {'type': 'ir.actions.client', 'tag': 'reload'}
