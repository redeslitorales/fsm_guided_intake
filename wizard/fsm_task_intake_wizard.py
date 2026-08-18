# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta, time
import pytz
import math
import logging

def float_hours_to_hm(hours_float):
    h = int(hours_float)
    m = int(round((hours_float - h) * 60))
    return h, m


_logger = logging.getLogger(__name__)

class FsmTaskIntakeWizardLine(models.TransientModel):
    _name = "fsm.task.intake.wizard.line"
    _description = "FSM Intake Wizard Line"

    wizard_id = fields.Many2one("fsm.task.intake.wizard", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    quantity = fields.Float(default=1.0)
    lot_id = fields.Many2one("stock.lot", string="Serial/Lot",
                             domain="[('product_id', '=', product_id)]")
    lot_ids = fields.Many2many("stock.lot", string="Serials", domain="[('product_id', '=', product_id)]")

    tracking = fields.Selection(related="product_id.tracking", readonly=True)
    is_service = fields.Boolean(compute="_compute_is_service", store=False)

    @api.onchange("lot_ids", "product_id")
    def _onchange_lot_ids(self):
        for rec in self:
            if rec.product_id and rec.product_id.tracking == "serial" and rec.lot_ids:
                rec.quantity = len(rec.lot_ids)

    @api.onchange("quantity", "product_id")
    def _onchange_quantity(self):
        for rec in self:
            if rec.product_id and rec.product_id.tracking == "serial":
                if rec.lot_ids and len(rec.lot_ids) != int(rec.quantity or 0):
                    rec.lot_ids = [(5, 0, 0)]

    @api.depends("product_id")
    def _compute_is_service(self):
        for rec in self:
            rec.is_service = rec.product_id and rec.product_id.type == "service"



class FsmTaskIntakeWizard(models.TransientModel):
    _name = "fsm.task.intake.wizard"
    _description = "FSM Guided Task Intake Wizard"

    reschedule_task_id = fields.Many2one("project.task", string="Task to Reschedule", readonly=True)

    def _get_default_state(self):
        if self.env.context.get("state"):
            return self.env.context.get("state")
        if self.env.context.get("reschedule_task_id"):
            return "schedule"
        return "customer"

    @api.onchange('team_id')
    def _onchange_team_id(self):
        """When the team filter is changed, recompute available slots and qualified teams."""
        # Force recompute of qualified teams and slots
        self._compute_qualified_teams()
        self._compute_slots()

    state = fields.Selection([
        ("customer", "Customer"),
        ("type", "Type"),
        ("products", "Products"),
        ("schedule", "Schedule"),
        ("notes", "Notes"),
        ("confirm", "Confirm"),
    ], default=_get_default_state, required=True)

    # Step 1
    task_type_id = fields.Many2one(
        "fsm.task.type",
        string="What are we doing?",
        domain="[('id', 'in', available_task_type_ids)]",
    )
    never_has_product = fields.Boolean(related="task_type_id.never_has_product", readonly=True)

    # Step 2
    partner_id = fields.Many2one("res.partner", string="Customer")
    partner_phone = fields.Char(related="partner_id.phone", readonly=True)
    subscription_id = fields.Many2one(
        "sale.order",
        string="Subscription",
        help="Active subscription for the selected customer."
    )
    available_subscription_ids = fields.Many2many(
        "sale.order",
        compute="_compute_available_orders",
        string="Available Subscriptions",
        readonly=True,
    )
    subscription_category_ids = fields.Many2many(related="task_type_id.subscription_category_ids", readonly=True)
    available_task_type_ids = fields.Many2many(
        "fsm.task.type",
        compute="_compute_available_task_types",
        string="Available Task Types",
        readonly=True,
    )
    show_service_address = fields.Boolean(compute="_compute_service_address_visibility")
    service_address_id = fields.Many2one(
        "res.partner",
        string="Service Address",
        domain="[('parent_id', '=', partner_id)]",
        help="Choose a service location if the customer has multiple addresses."
    )

    # Step 3
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Existing Sales Order",
        help="Select an existing sales order to reuse for this task."
    )
    available_sale_order_ids = fields.Many2many(
        "sale.order",
        compute="_compute_available_orders",
        string="Available Sales Orders",
        readonly=True,
    )
    has_existing_sales_orders = fields.Boolean(
        compute="_compute_has_existing_sales_orders",
        string="Has Existing Sales Orders"
    )
    line_ids = fields.One2many("fsm.task.intake.wizard.line", "wizard_id", string="Products/Services")
    require_products = fields.Boolean(related="task_type_id.requires_products", readonly=True)
    require_serials = fields.Boolean(related="task_type_id.requires_serials", readonly=True)
    require_signature = fields.Boolean(related="task_type_id.requires_signature", readonly=True)
    require_photos = fields.Boolean(related="task_type_id.requires_photos", readonly=True)
    product_category_ids = fields.Many2many(related="task_type_id.product_category_ids", readonly=True)
    subscription_category_ids = fields.Many2many(related="task_type_id.subscription_category_ids", readonly=True)
    preferred_team_ids = fields.Many2many(
        "fsm.team",
        compute="_compute_preferred_and_capable_teams",
        string="Preferred Teams",
        readonly=True,
    )
    capable_only_team_ids = fields.Many2many(
        "fsm.team",
        compute="_compute_preferred_and_capable_teams",
        string="Capable Teams",
        readonly=True,
    )

    # Duration - planned_hours is now computed from task type, not user-editable
    planned_hours = fields.Float(compute="_compute_planned_hours", store=True)
    buffer_before_mins = fields.Integer(related="task_type_id.buffer_before_mins", readonly=True)
    buffer_after_mins = fields.Integer(related="task_type_id.buffer_after_mins", readonly=True)

    # Step 4
    team_id = fields.Many2one("fsm.team", string="Team", help="Optional. If empty, wizard will choose.")
    qualified_team_ids = fields.Many2many(
        "fsm.team",
        compute="_compute_qualified_teams",
        string="Qualified Teams",
        readonly=True,
    )
    slot_index = fields.Integer(default=0)
    slot1_label = fields.Char(compute="_compute_slots", store=True)
    slot2_label = fields.Char(compute="_compute_slots", store=True)
    slot3_label = fields.Char(compute="_compute_slots", store=True)
    slot1_start = fields.Datetime(compute="_compute_slots", store=True)
    slot2_start = fields.Datetime(compute="_compute_slots", store=True)
    slot3_start = fields.Datetime(compute="_compute_slots", store=True)
    slot1_end = fields.Datetime(compute="_compute_slots", store=True)
    slot2_end = fields.Datetime(compute="_compute_slots", store=True)
    slot3_end = fields.Datetime(compute="_compute_slots", store=True)
    slot1_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True, store=True)
    slot2_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True, store=True)
    slot3_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True, store=True)
    slot1_team_label = fields.Char(compute="_compute_slots", readonly=True, store=True)
    slot2_team_label = fields.Char(compute="_compute_slots", readonly=True, store=True)
    slot3_team_label = fields.Char(compute="_compute_slots", readonly=True, store=True)
    slot1_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True, store=True)
    slot2_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True, store=True)
    slot3_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True, store=True)
    search_start_dt = fields.Datetime(string="Slot Search Start", readonly=False)
    filter_use_date = fields.Boolean(string="Filter by Date")
    date_filter_start = fields.Date(string="Earliest Date")
    date_filter_end = fields.Date(string="Latest Date")
    filter_use_time = fields.Boolean(string="Filter by Time")
    time_filter_start = fields.Float(string="Earliest Time", help="Use HH:MM format", digits=(16, 2))
    time_filter_end = fields.Float(string="Latest Time", help="Use HH:MM format", digits=(16, 2))

    selected_slot = fields.Selection(
        selection="_get_slot_selection",
        default="1",
        string="Choose Appointment",
    )
    scheduling_mode = fields.Selection(
        [
            ("exact", "Hour model"),
        ],
        string="Scheduling Mode",
        default="exact",
        help="Scheduling is currently locked to hour model.",
    )
    selected_slot_label = fields.Char(
        compute="_compute_selected_slot_label",
        readonly=True,
        string="Selected Appointment",
    )
    appointment_start = fields.Datetime(
        string="Appointment Start",
        help="Exact start date and time that will be written to the task.",
    )
    appointment_end = fields.Datetime(
        string="Appointment End",
        help="Exact end date and time that will be written to the task.",
    )
    # Freeze the selected slot to avoid accidental recomputation overrides
    frozen_selected_start = fields.Datetime(string="Frozen Selected Start", readonly=True)
    frozen_selected_end = fields.Datetime(string="Frozen Selected End", readonly=True)
    frozen_selected_team_id = fields.Many2one("fsm.team", string="Frozen Selected Team", readonly=True)

    # Step 5
    notes = fields.Text(string="Internal Notes")

    # Warnings / validations (preflight)
    warning_customer_phone_missing = fields.Boolean(compute="_compute_warnings")
    warning_no_service_address = fields.Boolean(compute="_compute_warnings")
    warning_missing_serials = fields.Boolean(compute="_compute_warnings")
    warning_planned_hours_zero = fields.Boolean(compute="_compute_warnings")
    warning_task_type_mapping = fields.Boolean(compute="_compute_warnings")
    warning_no_products_or_so = fields.Boolean(compute="_compute_warnings")

    @api.depends("task_type_id")
    def _compute_planned_hours(self):
        """Planned hours now taken from task type record"""
        for wiz in self:
            wiz.planned_hours = wiz.task_type_id.default_planned_hours if wiz.task_type_id else 1.0

    @api.depends("task_type_id")
    def _compute_preferred_and_capable_teams(self):
        for wiz in self:
            preferred = wiz.task_type_id.preferred_team_ids if wiz.task_type_id else self.env["fsm.team"]
            capable = wiz.task_type_id.capable_team_ids if wiz.task_type_id else self.env["fsm.team"]
            wiz.preferred_team_ids = preferred
            wiz.capable_only_team_ids = capable - preferred if capable else self.env["fsm.team"]

    def _get_state_title(self):
        self.ensure_one()
        titles = {
            "customer": _("Select Customer"),
            "type": _("Select Activity"),
            "products": _("Select Products"),
            "schedule": _("Select Date/Time"),
            "notes": _("Enter Notes"),
            "confirm": _("Confirm Appointment"),
        }
        if self._is_reschedule_mode():
            return {"schedule": _("Select Date/Time"), "notes": _("Enter Notes"), "confirm": _("Confirm Changes")}.get(self.state, "")
        return titles.get(self.state, "")

    def _get_wizard_title(self):
        self.ensure_one()
        if self._is_reschedule_mode():
            return _("Reschedule Field Service Task - %s") % (self._get_state_title() or "")
        return _("Create Field Service Task - %s") % (self._get_state_title() or "")

    def _get_slot_label_map(self):
        self.ensure_one()
        return {
            "1": self.slot1_label or _("No available slot"),
            "2": self.slot2_label or _("No available slot"),
            "3": self.slot3_label or _("No available slot"),
        }

    def _is_reschedule_mode(self):
        return bool(self.reschedule_task_id or self.env.context.get("reschedule_task_id"))

    def _get_step_order(self):
        return ["schedule", "notes", "confirm"] if self._is_reschedule_mode() else ["customer", "type", "products", "schedule", "notes", "confirm"]

    @api.model
    def _get_slot_selection(self):
        labels = self.env.context.get("slot_labels") or {
            "1": _("Option 1"),
            "2": _("Option 2"),
            "3": _("Option 3"),
        }
        return [(key, labels.get(key) or _("Option %s") % key) for key in ["1", "2", "3"]]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Day model is temporarily disabled; always force hour mode.
        if "scheduling_mode" in fields_list:
            res["scheduling_mode"] = "exact"
        task_id = self.env.context.get("reschedule_task_id")
        if task_id:
            task = self.env["project.task"].browse(task_id)
            if task:
                res["reschedule_task_id"] = task.id
                res["state"] = "schedule"
                res["partner_id"] = task.partner_id.id or False
                task_subscription = (
                    task.fsm_subscription_id
                    if "fsm_subscription_id" in task._fields and task.fsm_subscription_id
                    else task.sale_order_id.filtered("is_subscription")
                    if "sale_order_id" in task._fields
                    else self.env["sale.order"]
                )
                res["subscription_id"] = task_subscription.id
                res["service_address_id"] = task.fsm_service_address_id.id if "fsm_service_address_id" in task._fields else False
                res["task_type_id"] = task.fsm_task_type_id.id if "fsm_task_type_id" in task._fields else False
                res["planned_hours"] = (task.planned_hours if "planned_hours" in task._fields else False) or task.fsm_default_planned_hours or 1.0
                now_utc = fields.Datetime.now()
                search_start = max(task.planned_date_begin or now_utc, now_utc)
                res["search_start_dt"] = fields.Datetime.context_timestamp(self, search_start).replace(tzinfo=None)
                # Do not prefill team on reschedule; keep all qualified teams available
                res["team_id"] = False
                res["selected_slot"] = "1"
        return res

    @api.onchange("partner_id")
    def _onchange_partner(self):
        if self.partner_id and not self.service_address_id:
            # best effort: if only one child address, pick it
            addrs = self._get_service_addresses(self.partner_id)
            if len(addrs) == 1:
                self.service_address_id = addrs.id
        if not self.partner_id or (self.subscription_id and self.subscription_id.partner_id != self.partner_id):
            self.subscription_id = False

    def _get_service_addresses(self, partner):
        return partner.child_ids.filtered(lambda p: p.type in ("delivery", "other", "contact"))

    @api.depends("partner_id")
    def _compute_service_address_visibility(self):
        for wiz in self:
            addrs = self._get_service_addresses(wiz.partner_id) if wiz.partner_id else self.env["res.partner"]
            wiz.show_service_address = len(addrs) > 1

    @api.depends("partner_id")
    def _compute_has_existing_sales_orders(self):
        """Check if the customer has any existing sales orders"""
        for wiz in self:
            wiz.has_existing_sales_orders = bool(wiz.available_sale_order_ids)

    @api.depends("subscription_id", "partner_id")
    def _compute_available_task_types(self):
        for wiz in self:
            task_types = self.env["fsm.task.type"].search([])
            # If a customer or subscription is selected, only show client tasks
            if wiz.partner_id or wiz.subscription_id:
                task_types = task_types.filtered(lambda tt: tt.is_client_task)
            if wiz.subscription_id:
                sub_categs = wiz.subscription_id.order_line.mapped("product_id.categ_id")
                sub_categ_ids = set(sub_categs.ids)
                allowed_ids = []
                for tt in task_types:
                    if not tt.subscription_category_ids:
                        allowed_ids.append(tt.id)
                        continue
                    type_categ_ids = set(tt.subscription_category_ids.ids)
                    if sub_categ_ids & type_categ_ids:
                        allowed_ids.append(tt.id)
                task_types = task_types.browse(allowed_ids)
            wiz.available_task_type_ids = task_types

    @api.depends("partner_id", "subscription_category_ids")
    def _compute_available_orders(self):
        for wiz in self:
            subs = self.env["sale.order"]
            sales = self.env["sale.order"]
            if wiz.partner_id:
                domain_base = [("partner_id", "=", wiz.partner_id.id)]
                if wiz.subscription_category_ids:
                    domain = domain_base + [("order_line.product_id.categ_id", "child_of", wiz.subscription_category_ids.ids)]
                else:
                    domain = domain_base
                sales = self.env["sale.order"].search(domain)
                subs = sales.filtered("is_subscription")
            wiz.available_subscription_ids = subs
            wiz.available_sale_order_ids = sales

    @api.depends("partner_id", "service_address_id", "line_ids", "planned_hours", "task_type_id", "sale_order_id")
    def _compute_warnings(self):
        for wiz in self:
            wiz.warning_customer_phone_missing = bool(wiz.partner_id and not wiz.partner_id.phone)
            wiz.warning_no_service_address = bool(wiz.partner_id and wiz.show_service_address and not wiz.service_address_id)
            wiz.warning_missing_serials = False
            wiz.warning_planned_hours_zero = bool((wiz.planned_hours or 0.0) == 0.0)
            wiz.warning_task_type_mapping = bool(wiz.task_type_id and not wiz.task_type_id.project_id)
            
            # New warning: products required but neither SO nor products provided
            wiz.warning_no_products_or_so = bool(
                wiz.task_type_id and 
                wiz.task_type_id.requires_products and 
                not wiz.sale_order_id and 
                not wiz.line_ids
            )

            if wiz.line_ids:
                for l in wiz.line_ids:
                    if l.product_id and l.product_id.tracking in ("serial", "lot"):
                        if l.product_id.tracking == "serial" and not l.lot_ids:
                            wiz.warning_missing_serials = True
                        elif l.product_id.tracking == "lot" and not l.lot_id:
                            wiz.warning_missing_serials = True

    def _preflight_errors(self):
        self.ensure_one()
        errors = []
        if not self.task_type_id:
            errors.append(_("Task type is required."))
        if not self.partner_id:
            errors.append(_("Customer is required."))
        if self.task_type_id and not self.task_type_id.project_id:
            errors.append(_("Task type must have a project assigned."))
        if self.task_type_id and self.task_type_id.requires_products:
            if self.never_has_product:
                # Explicitly allow skipping products when configured
                pass
            else:
                project = self.task_type_id.project_id
                if project and hasattr(project, "allow_materials") and not project.allow_materials:
                    errors.append(_("Project '%s' must allow materials when products are required.") % project.display_name)
        if (self.planned_hours or 0.0) == 0.0:
            errors.append(_("Planned hours cannot be 0."))
        if self.task_type_id and self.task_type_id.requires_products and not self.never_has_product:
            if not self.sale_order_id and not self.line_ids:
                errors.append(_("This task type requires products. Please select a Sales Order or add products."))
        if self.task_type_id and self.task_type_id.requires_serials:
            for l in self.line_ids:
                if l.product_id and l.product_id.tracking in ("serial", "lot"):
                    if l.product_id.tracking == "serial" and not l.lot_ids:
                        errors.append(_("Product '%s' requires serial numbers.") % l.product_id.display_name)
                    elif l.product_id.tracking == "lot" and not l.lot_id:
                        errors.append(_("Product '%s' requires a lot number.") % l.product_id.display_name)
        return errors

    def _get_service_zone_name(self):
        self.ensure_one()
        addr = self.service_address_id or self.partner_id
        if addr and addr.city:
            return addr.city
        if addr and addr.state_id:
            return addr.state_id.name
        if addr and addr.country_id:
            return addr.country_id.name
        return ""

    @api.depends("task_type_id")
    def _compute_qualified_teams(self):
        for wiz in self:
            if not wiz.task_type_id:
                wiz.qualified_team_ids = self.env["fsm.team"]
                continue
            preferred = wiz.task_type_id.preferred_team_ids or self.env["fsm.team"]
            capable = wiz.task_type_id.capable_team_ids
            combined = (preferred | capable) if (preferred or capable) else self.env["fsm.team"]
            wiz.qualified_team_ids = combined if combined else self.env["fsm.team"].search([("active", "=", True)])

    @api.depends(
        "scheduling_mode",
        "selected_slot",
        "slot1_label",
        "slot2_label",
        "slot3_label",
        "date_filter_start",
        "appointment_start",
        "appointment_end",
    )
    def _compute_selected_slot_label(self):
        for wiz in self:
            if wiz.appointment_start and wiz.appointment_end:
                start_local = fields.Datetime.context_timestamp(wiz, wiz.appointment_start)
                end_local = fields.Datetime.context_timestamp(wiz, wiz.appointment_end)
                wiz.selected_slot_label = _("%s, %s - %s") % (
                    start_local.strftime("%a, %B %d"),
                    start_local.strftime("%H:%M"),
                    end_local.strftime("%H:%M"),
                )
                continue
            labels = {
                "1": wiz.slot1_label or _("No available slot"),
                "2": wiz.slot2_label or _("No available slot"),
                "3": wiz.slot3_label or _("No available slot"),
            }
            wiz.selected_slot_label = labels.get(wiz.selected_slot or "1", _("No available slot"))

    def _to_utc(self, dt):
        """Convert naive/local dt to UTC naive using user/context tz (default El Salvador if unset)."""
        if not dt:
            return dt
        tz_name = self.env.context.get("tz") or self.env.user.tz or "America/El_Salvador"
        tz = pytz.timezone(tz_name)
        local_dt = dt if dt.tzinfo else tz.localize(dt)
        return local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

    def _round_to_nearest_10(self, dt):
        """Round datetime to the nearest 10-minute mark."""
        if not dt:
            return dt
        remainder = dt.minute % 10
        minute = dt.minute - remainder + (10 if remainder >= 5 else 0)
        if minute == 60:
            dt = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            dt = dt.replace(minute=minute, second=0, microsecond=0)
        return dt

    def _get_duration_hours(self):
        """Duration in hours derived from the single required-minutes source."""
        return max(self._get_required_minutes() or 60, 1) / 60.0

    def _get_required_minutes(self):
        """Single source of truth for required minutes in scheduling flows."""
        self.ensure_one()
        if self.task_type_id:
            return self.task_type_id._get_required_minutes()
        if self.planned_hours:
            return int(round(self.planned_hours * 60.0))
        return 60

    def _get_capacity_service_date(self):
        """Capacity mode is date-based: pick best available day input."""
        self.ensure_one()
        min_service_date = fields.Date.context_today(self) + timedelta(days=1)

        candidate = False
        if self.filter_use_date and self.date_filter_start:
            candidate = self.date_filter_start
        elif self.search_start_dt:
            candidate = fields.Datetime.to_datetime(self.search_start_dt).date()
        elif self.slot1_start:
            candidate = fields.Datetime.to_datetime(self.slot1_start).date()

        # Day model must never schedule on the current day.
        return max(candidate or min_service_date, min_service_date)

    def _pick_capacity_team(self):
        """Pick a team for capacity mode without forcing exact slot search."""
        self.ensure_one()
        if self.team_id:
            return self.team_id
        preferred = self.task_type_id.preferred_team_ids if self.task_type_id else self.env["fsm.team"]
        if preferred:
            return preferred[0]
        capable = self.task_type_id.capable_team_ids if self.task_type_id else self.env["fsm.team"]
        if capable:
            return capable[0]
        return self.env["fsm.team"].search([("active", "=", True)], limit=1)

    def _find_top_slots(self, start_dt, limit=3, date_end=None, time_start=None, time_end=None):
        self.ensure_one()
        needed_hours = self._get_duration_hours()
        reschedule_task_id = self.reschedule_task_id.id or self.env.context.get("reschedule_task_id")

        if self.team_id:
            teams = self.team_id
        else:
            teams = self.qualified_team_ids
        if not teams:
            teams = self.env["fsm.team"].search([("active", "=", True)])
        if "active" in self.env["fsm.team"]._fields:
            teams = teams.filtered(lambda team: team.active)
        if not teams:
            return []

        lead_minutes = int(self.env["ir.config_parameter"].sudo().get_param(
            "fsm_guided_intake.slot_start_lead_minutes", "0"
        ) or 0)
        priority_windows = self.env["fsm.task.priority.slot"].get_windows_for_priority(
            self.task_type_id.priority if self.task_type_id else False
        )

        return self.env["fsm.slot.engine"].compute_top_slots(
            teams=teams,
            start_dt_local=start_dt,
            needed_hours=needed_hours,
            limit=limit,
            date_end_local=date_end,
            time_start=time_start,
            time_end=time_end,
            exclude_task_id=reschedule_task_id,
            buffer_before_mins=self.buffer_before_mins or 0,
            buffer_after_mins=self.buffer_after_mins or 0,
            lead_minutes=lead_minutes,
            priority_windows=priority_windows,
        )

    @api.depends("task_type_id", "partner_id", "planned_hours", "slot_index", "search_start_dt", "date_filter_start", "date_filter_end", "time_filter_start", "time_filter_end", "filter_use_date", "filter_use_time")
    def _compute_slots(self):
        for wiz in self:
            wiz.slot1_label = False
            wiz.slot2_label = False
            wiz.slot3_label = False
            wiz.slot1_start = False
            wiz.slot2_start = False
            wiz.slot3_start = False
            wiz.slot1_end = False
            wiz.slot2_end = False
            wiz.slot3_end = False
            wiz.slot1_team_id = False
            wiz.slot2_team_id = False
            wiz.slot3_team_id = False
            wiz.slot1_team_label = False
            wiz.slot2_team_label = False
            wiz.slot3_team_label = False
            wiz.slot1_is_preferred = False
            wiz.slot2_is_preferred = False
            wiz.slot3_is_preferred = False

            if not wiz.task_type_id or not wiz.partner_id:
                continue
            if (wiz.planned_hours or 0.0) <= 0:
                continue

            start_dt = wiz.search_start_dt
            if not start_dt:
                start_dt = fields.Datetime.context_timestamp(wiz, fields.Datetime.now()).replace(tzinfo=None) + timedelta(minutes=15)
            if wiz.filter_use_date and wiz.date_filter_start:
                start_dt = datetime.combine(wiz.date_filter_start, time.min)
            search_end = datetime.combine(wiz.date_filter_end, time.max) if (wiz.filter_use_date and wiz.date_filter_end) else None
            slots = wiz._find_top_slots(
                start_dt,
                limit=3,
                date_end=search_end,
                time_start=wiz.time_filter_start if wiz.filter_use_time else None,
                time_end=wiz.time_filter_end if wiz.filter_use_time else None,
            )

            # Deduplicate slots again before display to avoid identical entries
            uniq_slots = []
            seen_keys = set()
            for s in slots:
                key = (s["team"].id if s.get("team") else False, s.get("start"), s.get("end"))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                uniq_slots.append(s)
            slots = uniq_slots
            preferred_team_ids = set(wiz.preferred_team_ids._origin.ids)

            # Format labels with proper datetime display
            if len(slots) > 0:
                wiz.slot1_start = slots[0]["start"]
                wiz.slot1_end = slots[0]["end"]
                wiz.slot1_team_id = slots[0]["team"]
                wiz.slot1_team_label = slots[0]["team"].lead_user_id.name or slots[0]["team"].name
                wiz.slot1_is_preferred = slots[0]["team"].id in preferred_team_ids
                wiz.slot1_label = _("%s, %s - %s") % (
                    slots[0]["start"].strftime("%a, %B %d"),
                    slots[0]["start"].strftime("%H:%M"),
                    slots[0]["end"].strftime("%H:%M"),
                )
            if len(slots) > 1:
                wiz.slot2_start = slots[1]["start"]
                wiz.slot2_end = slots[1]["end"]
                wiz.slot2_team_id = slots[1]["team"]
                wiz.slot2_team_label = slots[1]["team"].lead_user_id.name or slots[1]["team"].name
                wiz.slot2_is_preferred = slots[1]["team"].id in preferred_team_ids
                wiz.slot2_label = _("%s, %s - %s") % (
                    slots[1]["start"].strftime("%a, %B %d"),
                    slots[1]["start"].strftime("%H:%M"),
                    slots[1]["end"].strftime("%H:%M"),
                )
            if len(slots) > 2:
                wiz.slot3_start = slots[2]["start"]
                wiz.slot3_end = slots[2]["end"]
                wiz.slot3_team_id = slots[2]["team"]
                wiz.slot3_team_label = slots[2]["team"].lead_user_id.name or slots[2]["team"].name
                wiz.slot3_is_preferred = slots[2]["team"].id in preferred_team_ids
                wiz.slot3_label = _("%s, %s - %s") % (
                    slots[2]["start"].strftime("%a, %B %d"),
                    slots[2]["start"].strftime("%H:%M"),
                    slots[2]["end"].strftime("%H:%M"),
                )

    @api.onchange("selected_slot")
    def _onchange_selected_slot(self):
        """Persist the chosen slot so later recomputes do not replace it."""
        slot_map = {
            "1": (self.slot1_start, self.slot1_end, self.slot1_team_id),
            "2": (self.slot2_start, self.slot2_end, self.slot2_team_id),
            "3": (self.slot3_start, self.slot3_end, self.slot3_team_id),
        }
        start_dt, end_dt, team_id = slot_map.get(self.selected_slot, (self.slot1_start, self.slot1_end, self.slot1_team_id))
        values = {
            "frozen_selected_start": start_dt,
            "frozen_selected_end": end_dt,
            "frozen_selected_team_id": team_id.id if team_id else False,
            # Unlike the slot fields, these are normal Odoo datetimes (UTC in
            # storage and shown in the user's timezone) and may be edited.
            "appointment_start": self._to_utc(start_dt) if start_dt else False,
            "appointment_end": self._to_utc(end_dt) if end_dt else False,
        }
        if self.id:
            self.write(values)
        else:
            self.update(values)

    # Navigation
    def _get_selected_schedule(self):
        """Return the exact selected interval in local and UTC representations."""
        self.ensure_one()
        slot_map = {
            "1": (self.slot1_start, self.slot1_end, self.slot1_team_id),
            "2": (self.slot2_start, self.slot2_end, self.slot2_team_id),
            "3": (self.slot3_start, self.slot3_end, self.slot3_team_id),
        }
        slot_start, slot_end, slot_team = slot_map.get(
            self.selected_slot,
            (self.slot1_start, self.slot1_end, self.slot1_team_id),
        )

        if bool(self.appointment_start) != bool(self.appointment_end):
            raise UserError(_("Enter both the appointment start and end times."))

        if self.appointment_start and self.appointment_end:
            start_utc = fields.Datetime.to_datetime(self.appointment_start)
            end_utc = fields.Datetime.to_datetime(self.appointment_end)
            start_local = fields.Datetime.context_timestamp(self, start_utc).replace(tzinfo=None)
            end_local = fields.Datetime.context_timestamp(self, end_utc).replace(tzinfo=None)
        else:
            if not slot_start or not slot_end:
                raise UserError(_("No available schedule slot found."))
            start_local = fields.Datetime.to_datetime(slot_start)
            end_local = fields.Datetime.to_datetime(slot_end)
            start_utc = self._to_utc(start_local)
            end_utc = self._to_utc(end_local)

        if end_utc <= start_utc:
            raise UserError(_("The appointment end time must be after the start time."))

        duration_hours = (end_utc - start_utc).total_seconds() / 3600.0
        return start_local, end_local, start_utc, end_utc, slot_team, duration_hours

    def action_next(self):
        self.ensure_one()
        order = self._get_step_order()
        idx = order.index(self.state)
        if self.state == "confirm":
            return {"type": "ir.actions.act_window_close"}
        if self.state == "schedule":
            has_slot = bool(self.slot1_start or self.slot2_start or self.slot3_start)
            has_manual_value = bool(self.appointment_start or self.appointment_end)
            if not has_slot and not has_manual_value:
                raise UserError(_("No available appointment slots were found."))
            if has_slot and not self.appointment_start and not self.appointment_end:
                self._onchange_selected_slot()
            self._get_selected_schedule()

        if not self._is_reschedule_mode():
            if self.state == "customer" and not self.partner_id:
                raise UserError(_("Please select a customer before continuing."))
            if self.state == "type" and not self.task_type_id:
                raise UserError(_("Please select an activity before continuing."))
            if self.state == "type" and self.never_has_product:
                self.state = "schedule"
            else:
                self.state = order[min(idx+1, len(order)-1)]
        else:
            if self.state == "schedule" and not self.frozen_selected_start:
                # Freeze the currently selected slot before the form reloads
                self._onchange_selected_slot()
            self.state = order[min(idx+1, len(order)-1)]
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(
                self.env.context,
                slot_labels=self._get_slot_label_map(),
                search_start_dt=self.search_start_dt,
                frozen_slot_start=self.frozen_selected_start and self.frozen_selected_start.isoformat(),
                frozen_slot_end=self.frozen_selected_end and self.frozen_selected_end.isoformat(),
                frozen_slot_team_id=self.frozen_selected_team_id.id if self.frozen_selected_team_id else False,
            ),
        }

    def action_back(self):
        self.ensure_one()
        order = self._get_step_order()
        idx = order.index(self.state)
        if self._is_reschedule_mode():
            self.state = order[max(idx-1, 0)]
        else:
            if self.state == "schedule" and self.never_has_product:
                self.state = "type"
            else:
                self.state = order[max(idx-1, 0)]
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt),
        }

    def action_more_options(self):
        self.ensure_one()
        # Move search start forward based on last shown slots (or current time).
        # If no slots are currently shown, jump a full day to avoid repeating the same window.
        has_slots = bool(self.slot1_end or self.slot3_end)
        base = self.slot3_end or self.slot1_end or self.search_start_dt
        if not base:
            base = fields.Datetime.context_timestamp(self, fields.Datetime.now()).replace(tzinfo=None)
        increment = timedelta(hours=2.0 if has_slots else 24.0)
        self.search_start_dt = base + increment
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt),
        }

    def action_create_task(self):
        self.ensure_one()
        if self._is_reschedule_mode():
            return self._action_reschedule_task()
        errors = self._preflight_errors()
        if errors:
            raise UserError(_("Fix these issues before saving:\n- %s") % "\n- ".join(errors))

        scheduling_mode = "exact"

        start_dt = False
        end_dt = False
        start_dt_utc = False
        end_dt_utc = False
        slot_team = self.env["fsm.team"]
        duration_hours = self._get_duration_hours()
        if scheduling_mode == "exact":
            (
                start_dt,
                end_dt,
                start_dt_utc,
                end_dt_utc,
                slot_team,
                duration_hours,
            ) = self._get_selected_schedule()

        # Choose the team from the selected slot so availability and assignment stay aligned.
        if scheduling_mode == "exact":
            team = slot_team or self.team_id
            if not team:
                start_local = fields.Datetime.context_timestamp(self, fields.Datetime.now()).replace(tzinfo=None)
                candidates = self._find_top_slots(start_local, limit=3)
                team = candidates[0]["team"] if candidates else self.env["fsm.team"].search([], limit=1)
        else:
            team = self._pick_capacity_team()
        if not team:
            raise UserError(_("No FSM team found."))

        service_date = self._get_capacity_service_date() if scheduling_mode == "capacity" else False

        debug_payload = {
            "selected_slot": self.selected_slot,
            "slot1": (self.slot1_start, self.slot1_end),
            "slot2": (self.slot2_start, self.slot2_end),
            "slot3": (self.slot3_start, self.slot3_end),
            "computed_start": start_dt,
            "computed_end": end_dt,
            "service_date": service_date,
            "planned_hours": self.planned_hours,
            "required_minutes": self._get_required_minutes(),
            "scheduling_mode": scheduling_mode,
            "team_id": team.id if team else False,
        }

        # Create task
        task_vals = {
            "name": self.task_type_id.name,
            "project_id": self.task_type_id.project_id.id,
            "partner_id": self.partner_id.id,
            "fsm_task_type_id": self.task_type_id.id,
            "description": self.notes or "",
            "fsm_service_address_id": (self.service_address_id.id if self.service_address_id else False),
            "fsm_service_zone_name": self._get_service_zone_name(),
        }
        if scheduling_mode == "exact":
            task_vals["team_id"] = team.id
        else:
            # Explicitly clear team/assignees so project/task defaults do not auto-fill them.
            task_vals["team_id"] = False
            if "user_id" in self.env["project.task"]._fields:
                task_vals["user_id"] = False
            if "user_ids" in self.env["project.task"]._fields:
                task_vals["user_ids"] = [(6, 0, [])]
        if self.task_type_id.default_pon_type and "fsm_pon_type" in self.env["project.task"]._fields:
            task_vals["fsm_pon_type"] = self.task_type_id.default_pon_type
        # In day/capacity mode dispatch will assign team and users later.
        if scheduling_mode == "exact":
            slot_engine = self.env["fsm.slot.engine"]
            assignee_user_ids = slot_engine.get_team_users_for_interval_utc(
                team, start_dt_utc, end_dt_utc
            ).ids
            if assignee_user_ids or slot_engine._availability_source() == "planning":
                if "user_id" in task_vals or "user_id" in self.env["project.task"]._fields:
                    task_vals["user_id"] = assignee_user_ids[0] if assignee_user_ids else False
                if "user_ids" in self.env["project.task"]._fields:
                    task_vals["user_ids"] = [(6, 0, assignee_user_ids)]
        task_fields = self.env["project.task"]._fields
        if scheduling_mode == "capacity" and service_date:
            # Day model is date-only; keep exact datetime fields unset to satisfy task date constraints.
            start_dt = False
            end_dt = False
        if scheduling_mode != "exact":
            start_dt_utc = self._to_utc(start_dt) if start_dt else start_dt
            end_dt_utc = self._to_utc(end_dt) if end_dt else end_dt
        if scheduling_mode == "exact" and "planned_date_begin" in task_fields:
            task_vals["planned_date_begin"] = start_dt_utc
        if scheduling_mode == "exact" and "planned_date_end" in task_fields:
            task_vals["planned_date_end"] = end_dt_utc
        if scheduling_mode == "exact" and "date_start" in task_fields:
            task_vals["date_start"] = start_dt_utc
        if scheduling_mode == "exact" and "date_end" in task_fields:
            task_vals["date_end"] = end_dt_utc
        if "date_deadline" in task_fields:
            if scheduling_mode == "capacity" and service_date:
                task_vals["date_deadline"] = service_date
            else:
                task_vals["date_deadline"] = self.env[
                    "project.task"
                ]._fsm_schedule_deadline_value(end_dt_utc)
        if "planned_hours" in task_fields:
            task_vals["planned_hours"] = duration_hours
        if "allocated_hours" in task_fields:
            task_vals["allocated_hours"] = duration_hours
        if "fsm_default_planned_hours" in task_fields:
            # Keep the task-type duration as the baseline so a manually edited
            # interval can still be identified as a deliberate override.
            task_vals["fsm_default_planned_hours"] = self.planned_hours or duration_hours
        if self.subscription_id and "fsm_subscription_id" in task_fields:
            task_vals["fsm_subscription_id"] = self.subscription_id.id
        if "sale_order_id" in task_fields:
            operational_order = self.sale_order_id or self.subscription_id
            if operational_order:
                task_vals["sale_order_id"] = operational_order.id
        if self.task_type_id.default_stage_id:
            task_vals["stage_id"] = self.task_type_id.default_stage_id.id
        try:
            # Remove wizard-specific context keys that can collide with project.task defaults
            create_ctx = dict(self.env.context)
            create_ctx.pop("default_state", None)
            create_ctx.pop("state", None)
            task = self.env["project.task"].with_context(create_ctx).create(task_vals)
            if scheduling_mode == "capacity":
                # Defense-in-depth: keep day-mode tasks unassigned until dispatch finalizes routing.
                clear_vals = {"team_id": False}
                if "user_id" in task._fields:
                    clear_vals["user_id"] = False
                if "user_ids" in task._fields:
                    clear_vals["user_ids"] = [(6, 0, [])]
                task.write(clear_vals)
        except Exception as e:
            raise UserError(_("Task creation failed: %s\nDebug payload: %s") % (e, debug_payload))

        if scheduling_mode == "exact":
            task._fsm_apply_scheduled_stage()
        else:
            task._fsm_apply_unscheduled_stage()

        # Materials
        for l in self.line_ids:
            self.env["fsm.task.material"].create({
                "task_id": task.id,
                "product_id": l.product_id.id,
                "product_uom_qty": l.quantity,
                "lot_id": l.lot_id.id if l.lot_id else False,
                "lot_ids": [(6, 0, l.lot_ids.ids)] if getattr(l, 'lot_ids', False) and l.lot_ids else False,
            })

        # Checklist subtasks
        if self.task_type_id.checklist_subtask_names:
            names = [n.strip() for n in (self.task_type_id.checklist_subtask_names or "").splitlines() if n.strip()]
            for nm in names:
                self.env["project.task"].create({
                    "name": nm,
                    "project_id": task.project_id.id,
                    "parent_id": task.id,
                    "partner_id": task.partner_id.id,
                })

        # Reservation vs exact booking: default to capacity-based reservation
        if scheduling_mode == "exact":
            alloc_hours = duration_hours
            try:
                clean_ctx = dict(self.env.context)
                clean_ctx.pop("default_state", None)
                clean_ctx.pop("state", None)
                booking = self.env["fsm.booking"].with_context(clean_ctx).create({
                    "task_id": task.id,
                    "team_id": team.id,
                    "start_datetime": start_dt_utc,
                    "end_datetime": end_dt_utc,
                    "allocated_hours": alloc_hours,
                    "state": "confirmed",
                })
                task.fsm_booking_id = booking.id

                # Create delivery + reserve (as requested)
                booking.with_context(clean_ctx).action_create_or_update_delivery()
            except Exception as e:
                raise UserError(_("Booking creation failed: %s\nDebug payload: %s") % (e, debug_payload))
        else:
            required_minutes = self._get_required_minutes()
            try:
                self.env["fsm.day.reservation"].create({
                    "task_id": task.id,
                    "service_date": service_date,
                    "task_type_id": self.task_type_id.id,
                    "required_minutes": required_minutes,
                    "capacity_bucket": (self.task_type_id.skill_level or team.skill_level or "L1"),
                    "zone": task.fsm_service_zone_name,
                    "priority": self.task_type_id.priority,
                    "assigned_team_id": False,
                    "assigned_start_datetime": False,
                    "assigned_end_datetime": False,
                })
            except Exception as e:
                raise UserError(_("Reservation creation failed: %s\nDebug payload: %s") % (e, debug_payload))

        # Open created task
        action = {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "view_mode": "form",
            "res_id": task.id,
        }
        return action

    def _action_reschedule_task(self):
        self.ensure_one()
        task = self.reschedule_task_id or self.env["project.task"].browse(self.env.context.get("reschedule_task_id"))
        if not task:
            raise UserError(_("No task to reschedule was provided."))

        (
            start_dt,
            end_dt,
            start_dt_utc,
            end_dt_utc,
            slot_team,
            duration_hours,
        ) = self._get_selected_schedule()

        team = slot_team or self.team_id
        if not team and getattr(task, "fsm_booking_id", False):
            team = task.fsm_booking_id.team_id
        if not team:
            team = self.env["fsm.team"].search([], limit=1)
        if not team:
            raise UserError(_("No FSM team found for scheduling."))

        assignee_user_ids = []
        if team:
            if team.lead_user_id:
                assignee_user_ids.append(team.lead_user_id.id)
            member_users = team.member_ids.mapped("user_id").filtered(lambda u: u)
            assignee_user_ids += member_users.ids
        elif "user_ids" in task._fields and task.user_ids:
            assignee_user_ids = task.user_ids.ids
        assignee_user_ids = list(dict.fromkeys(assignee_user_ids))

        # The contract workflow also uses reschedule_task_id to schedule the
        # already-created installation task for the first time.  Scheduling an
        # undated task is not a reschedule: update it in place so its sales-order
        # link and workflow state are preserved.
        if not task.planned_date_begin:
            task._write_scheduled_datetime(
                start_dt_utc=start_dt_utc,
                end_dt_utc=end_dt_utc,
                duration_hours=duration_hours,
                team=team,
                assignee_user_ids=assignee_user_ids,
            )
            task._fsm_apply_scheduled_stage()
            return {
                "type": "ir.actions.act_window",
                "res_model": "project.task",
                "res_id": task.id,
                "view_mode": "form",
            }

        new_task = task.reschedule_clone_to_new_task(
            start_dt_utc=start_dt_utc,
            end_dt_utc=end_dt_utc,
            team=team,
            duration_hours=duration_hours,
            notes=self.notes,
            assignee_user_ids=assignee_user_ids,
        )

        action = {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": new_task.id,
            "view_mode": "form",
        }
        return action

    @api.model
    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        """
        Inject dynamic slot labels into the radio selection so users see the actual
        time strings instead of generic Slot 1/2/3.
        """
        res = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if view_type != "form":
            return res

        res_id = self.env.context.get("res_id")
        if not res_id or "selected_slot" not in res.get("fields", {}):
            return res

        wiz = self.browse(res_id)
        labels = wiz._get_slot_label_map()
        selection = [(key, labels.get(key) or _("No available slot")) for key in ["1", "2", "3"]]
        res["fields"]["selected_slot"]["selection"] = selection
        return res

