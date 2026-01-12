# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta, time
import pytz
import math

def float_hours_to_hm(hours_float):
    h = int(hours_float)
    m = int(round((hours_float - h) * 60))
    return h, m

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
    selected_slot_label = fields.Char(
        compute="_compute_selected_slot_label",
        readonly=True,
        string="Selected Appointment",
    )

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
        task_id = self.env.context.get("reschedule_task_id")
        if task_id:
            task = self.env["project.task"].browse(task_id)
            if task:
                res["reschedule_task_id"] = task.id
                res["state"] = "schedule"
                res["partner_id"] = task.partner_id.id or False
                res["subscription_id"] = task.sale_order_id.id if "sale_order_id" in task._fields else False
                res["service_address_id"] = task.fsm_service_address_id.id if "fsm_service_address_id" in task._fields else False
                res["task_type_id"] = task.fsm_task_type_id.id if "fsm_task_type_id" in task._fields else False
                res["planned_hours"] = (task.planned_hours if "planned_hours" in task._fields else False) or task.fsm_default_planned_hours or 1.0
                res["search_start_dt"] = task.planned_date_begin or fields.Datetime.now()
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
                subs = self.env["sale.order"].search(domain)
                sales = subs
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

    @api.depends("selected_slot", "slot1_label", "slot2_label", "slot3_label")
    def _compute_selected_slot_label(self):
        for wiz in self:
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
        """Duration in hours based on task type planned hours with sane floor."""
        hours = self.task_type_id.default_planned_hours if self.task_type_id else self.planned_hours
        return max(hours or 0.0, 1.0)

    def _find_top_slots(self, start_dt, limit=3, date_end=None, time_start=None, time_end=None):
        """
        Return a list of top available slots sorted by start time.
        Each slot is a dict: {"start": datetime, "end": datetime, "team": fsm.team}.
        Availability is based on the team calendar (team.calendar_id or lead's calendar)
        and constrained by the team lead's bookings (across all teams that share the same lead).
        """
        self.ensure_one()
        needed_hours = self._get_duration_hours()
        buffer_before = timedelta(minutes=(self.buffer_before_mins or 0))
        buffer_after = timedelta(minutes=(self.buffer_after_mins or 0))
        reschedule_task_id = self.reschedule_task_id.id or self.env.context.get("reschedule_task_id")

        # If a team is selected, prioritize it but still include all qualified teams
        if self.team_id:
            teams = self.team_id | self.qualified_team_ids
        else:
            teams = self.qualified_team_ids
        if not teams:
            teams = self.env["fsm.team"].search([("active", "=", True)])

        slots = []
        # Scan a few days ahead
        search_end = date_end or (start_dt + timedelta(days=14))
        # Convert search window to UTC to match stored booking datetimes
        search_start_utc = self._to_utc(start_dt)
        search_end_utc = self._to_utc(search_end)
        lead_minutes = int(self.env["ir.config_parameter"].sudo().get_param("fsm_guided_intake.slot_start_lead_minutes", "0") or 0)

        # Precompute team sets per lead to check lead availability across teams
        lead_to_team_ids = {}
        leads = teams.mapped("lead_user_id").filtered(lambda u: u)
        if leads:
            all_lead_teams = self.env["fsm.team"].search([("lead_user_id", "in", leads.ids)])
            for lead in leads:
                lead_to_team_ids[lead.id] = all_lead_teams.filtered(lambda t: t.lead_user_id.id == lead.id).ids

        for team in teams:
            # Prefer team calendar, fallback to lead calendar, then company/default calendar
            calendar = (
                team.calendar_id
                or getattr(team.lead_user_id, "resource_calendar_id", False)
                or self.env.company.resource_calendar_id
                or self.env.ref("resource.resource_calendar_std", raise_if_not=False)
            )
            if not calendar:
                continue
            attendances = calendar.attendance_ids.filtered(lambda a: not a.display_type)
            if not attendances:
                continue
            # Preload existing bookings for the window to avoid overlaps
            team_ids_for_lead = lead_to_team_ids.get(team.lead_user_id.id, [team.id])
            booking_domain = [
                ("team_id", "in", team_ids_for_lead),
                ("state", "!=", "cancelled"),
                ("start_datetime", "<", search_end_utc),
                ("end_datetime", ">", search_start_utc),
            ]
            if reschedule_task_id:
                booking_domain.append(("task_id", "!=", reschedule_task_id))
            existing_bookings = self.env["fsm.booking"].search(booking_domain)
            # Also consider tasks with planned dates for this team (if any)
            task_intervals = []
            Task = self.env["project.task"]
            if "team_id" in Task._fields:
                task_domain = [("team_id", "in", team_ids_for_lead), ("stage_id.fold", "=", False)]
                if reschedule_task_id:
                    task_domain.append(("id", "!=", reschedule_task_id))
                if "planned_date_begin" in Task._fields and "planned_date_end" in Task._fields:
                    task_domain += [
                        ("planned_date_begin", "<", search_end_utc),
                        ("planned_date_end", ">", search_start_utc),
                    ]
                elif "date_start" in Task._fields and "date_end" in Task._fields:
                    task_domain += [
                        ("date_start", "<", search_end_utc),
                        ("date_end", ">", search_start_utc),
                    ]
                tasks = Task.search(task_domain)
                for t in tasks:
                    start = getattr(t, "planned_date_begin", False) or getattr(t, "date_start", False)
                    end = getattr(t, "planned_date_end", False) or getattr(t, "date_end", False)
                    if start and end:
                        task_intervals.append((start, end))
            # Loop through days
            current_day = start_dt.date()
            while datetime.combine(current_day, time.min) < search_end:
                if self.filter_use_date and self.date_filter_start and current_day < self.date_filter_start:
                    current_day += timedelta(days=1)
                    continue
                if self.filter_use_date and self.date_filter_end and current_day > self.date_filter_end:
                    break
                weekday_str = str(current_day.weekday())
                day_attendances = attendances.filtered(lambda a: a.dayofweek == weekday_str)
                if day_attendances:
                    earliest = min(day_attendances.mapped("hour_from"))
                    latest = max(day_attendances.mapped("hour_to"))

                    effective_start = earliest
                    effective_end = latest
                    if time_start is not None:
                        effective_start = max(effective_start, time_start)
                    if time_end is not None:
                        effective_end = min(effective_end, time_end)
                    start_hour, start_min = float_hours_to_hm(effective_start)
                    end_candidate = effective_end
                    end_hour, end_min = float_hours_to_hm(end_candidate)
                    shift_start_dt = datetime.combine(current_day, time(start_hour, start_min)) + timedelta(minutes=lead_minutes)
                    shift_end_dt = datetime.combine(current_day, time(end_hour, end_min)) + timedelta(hours=1)

                    if shift_end_dt <= shift_start_dt:
                        current_day += timedelta(days=1)
                        continue

                    if shift_start_dt < start_dt:
                        shift_start_dt = start_dt

                    # Generate multiple candidate slots across the day window
                    cursor = shift_start_dt
                    step = timedelta(minutes=30)
                    while cursor + timedelta(hours=needed_hours) + buffer_before + buffer_after <= shift_end_dt:
                        slot_start = cursor + buffer_before
                        slot_end = slot_start + timedelta(hours=needed_hours) + buffer_after

                        slot_start_utc = self._to_utc(slot_start)
                        slot_end_utc = self._to_utc(slot_end)
                        overlap = existing_bookings.filtered(
                            lambda b: b.start_datetime < slot_end_utc and b.end_datetime > slot_start_utc
                        )
                        if not overlap and task_intervals:
                            for start_dt, end_dt in task_intervals:
                                if start_dt < slot_end_utc and end_dt > slot_start_utc:
                                    overlap = True
                                    break

                        # Filter: Only show slots in the future for today (timezone aware, force UTC-6 if unset)
                        import pytz
                        tz_name = self.env.context.get("tz") or self.env.user.tz or "America/El_Salvador"
                        tz = pytz.timezone(tz_name)
                        now_utc = fields.Datetime.now()
                        now_tz = pytz.UTC.localize(now_utc).astimezone(tz)
                        # Treat slot_start as local time (naive means local); avoid double-shifting by UTC
                        if slot_start.tzinfo:
                            slot_start_tz = slot_start.astimezone(tz)
                        else:
                            slot_start_tz = tz.localize(slot_start)
                        # If slot is today, allow if slot is at or after now (>=)
                        if slot_start_tz.date() == now_tz.date():
                            if slot_start_tz >= now_tz:
                                if not overlap:
                                    slots.append({
                                        "start": slot_start,
                                        "end": slot_end,
                                        "team": team,
                                    })
                        else:
                            if not overlap:
                                slots.append({
                                    "start": slot_start,
                                    "end": slot_end,
                                    "team": team,
                                })
                        cursor += step

                current_day += timedelta(days=1)
        
        # Sort by start time
        slots.sort(key=lambda s: s["start"])
        return slots[:limit]

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

            # Base search start in user/local tz (default UTC-6) to avoid skipping "today" when server is UTC
            if wiz.search_start_dt:
                start_dt_ctx = fields.Datetime.context_timestamp(wiz, wiz.search_start_dt)
            else:
                start_dt_ctx = fields.Datetime.context_timestamp(wiz, fields.Datetime.now() + timedelta(minutes=15))
            start_dt = wiz._round_to_nearest_10((start_dt_ctx.replace(tzinfo=None)) if start_dt_ctx else fields.Datetime.now())

            if wiz.filter_use_date and wiz.date_filter_start:
                start_dt = datetime.combine(wiz.date_filter_start, time.min)
            search_end = datetime.combine(wiz.date_filter_end, time.max) if (wiz.filter_use_date and wiz.date_filter_end) else None
            # Scan forward in 2-hour increments (up to ~7 days) until we find slots.
            slots = []
            chosen_start = start_dt
            max_attempts = 84  # 2-hour steps for 7 days
            for attempt in range(max_attempts):
                start_dt_attempt = start_dt + timedelta(hours=attempt * 2.0)
                start_dt_attempt = wiz._round_to_nearest_10(start_dt_attempt)
                slots = wiz._find_top_slots(
                    start_dt_attempt,
                    limit=3,
                    date_end=search_end,
                    time_start=wiz.time_filter_start if wiz.filter_use_time else None,
                    time_end=wiz.time_filter_end if wiz.filter_use_time else None,
                )
                # Deduplicate slots (team + time) to avoid repeated identical options
                uniq = []
                seen = set()
                for s in slots:
                    key = (s["team"].id if s["team"] else False, s["start"], s["end"])
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append(s)
                slots = uniq
                if slots:
                    chosen_start = start_dt_attempt
                    break
            # remember the start used; next run will bump from the last shown window
            wiz.search_start_dt = wiz._to_utc(chosen_start)

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

            # Format labels with proper datetime display
            if len(slots) > 0:
                wiz.slot1_start = slots[0]["start"]
                wiz.slot1_end = slots[0]["end"]
                wiz.slot1_team_id = slots[0]["team"]
                wiz.slot1_team_label = slots[0]["team"].lead_user_id.name or slots[0]["team"].name
                wiz.slot1_is_preferred = slots[0]["team"] in wiz.preferred_team_ids
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
                wiz.slot2_is_preferred = slots[1]["team"] in wiz.preferred_team_ids
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
                wiz.slot3_is_preferred = slots[2]["team"] in wiz.preferred_team_ids
                wiz.slot3_label = _("%s, %s - %s") % (
                    slots[2]["start"].strftime("%a, %B %d"),
                    slots[2]["start"].strftime("%H:%M"),
                    slots[2]["end"].strftime("%H:%M"),
                )
            # Advance search start past the last shown slot to avoid repeats
            last_end = wiz.slot3_end or wiz.slot1_end or wiz.search_start_dt or fields.Datetime.now()
            if last_end:
                wiz.search_start_dt = wiz._to_utc(last_end + timedelta(hours=2.0))

    # Navigation
    def action_next(self):
        self.ensure_one()
        order = self._get_step_order()
        idx = order.index(self.state)
        if self.state == "confirm":
            return {"type": "ir.actions.act_window_close"}
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
            if self.state == "schedule" and not (self.slot1_start or self.slot2_start or self.slot3_start):
                raise UserError(_("No available appointment slots were found."))
            self.state = order[min(idx+1, len(order)-1)]
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map(), search_start_dt=self.search_start_dt),
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
        # Move search start forward based on last shown slots (or current time)
        base = self.slot3_end or self.slot1_end or fields.Datetime.now()
        self.search_start_dt = (base or fields.Datetime.now()) + timedelta(hours=2.0)
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

        # Determine selected slot
        slot_map = {
            "1": (self.slot1_start, self.slot1_end),
            "2": (self.slot2_start, self.slot2_end),
            "3": (self.slot3_start, self.slot3_end),
        }
        start_dt, end_dt = slot_map.get(self.selected_slot, (self.slot1_start, self.slot1_end))
        if not start_dt or not end_dt:
            raise UserError(_("No available schedule slot found."))

        # Choose team: from slot computation (team included in label); v1: pick first capable team if not explicitly filtered
        team = self.team_id
        if not team:
            # pick best team from current computed slots by matching start_dt
            candidates = self._find_top_slots(fields.Datetime.now(), limit=3)
            team = candidates[0]["team"] if candidates else self.env["fsm.team"].search([], limit=1)
        if not team:
            raise UserError(_("No FSM team found."))

        debug_payload = {
            "selected_slot": self.selected_slot,
            "slot1": (self.slot1_start, self.slot1_end),
            "slot2": (self.slot2_start, self.slot2_end),
            "slot3": (self.slot3_start, self.slot3_end),
            "computed_start": start_dt,
            "computed_end": end_dt,
            "planned_hours": self.planned_hours,
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
        if self.task_type_id.default_pon_type and "fsm_pon_type" in self.env["project.task"]._fields:
            task_vals["fsm_pon_type"] = self.task_type_id.default_pon_type
        # Assign responsible + followers from the selected team (lead user + member users)
        assignee_user_ids = []
        if team and team.lead_user_id:
            assignee_user_ids.append(team.lead_user_id.id)
        if team and team.member_ids:
            member_users = team.member_ids.mapped("user_id").filtered(lambda u: u)
            assignee_user_ids += member_users.ids
        assignee_user_ids = list(dict.fromkeys(assignee_user_ids))  # dedupe while preserving order
        if assignee_user_ids:
            if "user_id" in task_vals or "user_id" in self.env["project.task"]._fields:
                task_vals["user_id"] = assignee_user_ids[0]
            if "user_ids" in self.env["project.task"]._fields:
                task_vals["user_ids"] = [(6, 0, assignee_user_ids)]
        task_fields = self.env["project.task"]._fields
        start_dt = fields.Datetime.to_datetime(start_dt) if start_dt else start_dt
        # Force duration to the planned hours (task type) to avoid drift or unexpected longer slots.
        duration_hours = self._get_duration_hours()
        end_dt = start_dt + timedelta(hours=duration_hours) if start_dt else end_dt
        if start_dt and end_dt and end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=15)
            duration_hours = 0.25
        start_dt_utc = self._to_utc(start_dt) if start_dt else start_dt
        end_dt_utc = self._to_utc(end_dt) if end_dt else end_dt
        if "planned_date_begin" in task_fields:
            task_vals["planned_date_begin"] = start_dt_utc
        if "planned_date_end" in task_fields:
            task_vals["planned_date_end"] = end_dt_utc
        if "date_start" in task_fields:
            task_vals["date_start"] = start_dt_utc
        if "date_end" in task_fields:
            task_vals["date_end"] = end_dt_utc
        if "date_deadline" in task_fields:
            deadline_dt = end_dt or (start_dt + timedelta(hours=self.planned_hours or 0.0))
            if deadline_dt:
                if isinstance(deadline_dt, datetime) and deadline_dt.time() != time.min:
                    deadline_dt = deadline_dt + timedelta(days=1)
                task_vals["date_deadline"] = fields.Date.to_date(deadline_dt)
            else:
                task_vals["date_deadline"] = False
        if "planned_hours" in self.env["project.task"]._fields:
            task_vals["planned_hours"] = duration_hours
            # Pass through default planned hours for comparison
            task_vals["fsm_default_planned_hours"] = duration_hours
        if self.sale_order_id and "sale_order_id" in task_fields:
            task_vals["sale_order_id"] = self.sale_order_id.id
        if self.task_type_id.default_stage_id:
            task_vals["stage_id"] = self.task_type_id.default_stage_id.id
        try:
            # Remove wizard-specific context keys that can collide with project.task defaults
            create_ctx = dict(self.env.context)
            create_ctx.pop("default_state", None)
            create_ctx.pop("state", None)
            task = self.env["project.task"].with_context(create_ctx).create(task_vals)
        except Exception as e:
            raise UserError(_("Task creation failed: %s\nDebug payload: %s") % (e, debug_payload))

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

        # Booking
        alloc_hours = (end_dt - start_dt).total_seconds() / 3600.0
        try:
            clean_ctx = dict(self.env.context)
            clean_ctx.pop("default_state", None)
            clean_ctx.pop("state", None)
            booking = self.env["fsm.booking"].with_context(clean_ctx).create({
                "task_id": task.id,
                "team_id": team.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })
            task.fsm_booking_id = booking.id

            # Create delivery + reserve (as requested)
            booking.with_context(clean_ctx).action_create_or_update_delivery()
        except Exception as e:
            raise UserError(_("Booking creation failed: %s\nDebug payload: %s") % (e, debug_payload))

        # Open created task
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "view_mode": "form",
            "res_id": task.id,
        }

    def _action_reschedule_task(self):
        self.ensure_one()
        task = self.reschedule_task_id or self.env["project.task"].browse(self.env.context.get("reschedule_task_id"))
        if not task:
            raise UserError(_("No task to reschedule was provided."))

        slot_map = {
            "1": (self.slot1_start, self.slot1_end, self.slot1_team_id),
            "2": (self.slot2_start, self.slot2_end, self.slot2_team_id),
            "3": (self.slot3_start, self.slot3_end, self.slot3_team_id),
        }
        start_dt, end_dt, slot_team = slot_map.get(self.selected_slot, (self.slot1_start, self.slot1_end, self.slot1_team_id))
        if not start_dt or not end_dt:
            raise UserError(_("Please pick an available appointment slot."))

        duration_hours = self._get_duration_hours()
        end_dt = start_dt + timedelta(hours=duration_hours)

        team = slot_team or self.team_id
        if not team and getattr(task, "fsm_booking_id", False):
            team = task.fsm_booking_id.team_id
        if not team:
            team = self.env["fsm.team"].search([], limit=1)
        if not team:
            raise UserError(_("No FSM team found for scheduling."))

        start_dt_utc = self._to_utc(start_dt)
        end_dt_utc = self._to_utc(end_dt)

        update_vals = {
            "planned_date_begin": start_dt_utc,
        }
        if "planned_date_end" in task._fields:
            update_vals["planned_date_end"] = end_dt_utc
        if "planned_hours" in task._fields:
            update_vals["planned_hours"] = duration_hours

        assignee_user_ids = []
        if team:
            if team.lead_user_id:
                assignee_user_ids.append(team.lead_user_id.id)
            member_users = team.member_ids.mapped("user_id").filtered(lambda u: u)
            assignee_user_ids += member_users.ids
        elif "user_ids" in task._fields and task.user_ids:
            # Fallback: keep current assignees only when no team was provided
            assignee_user_ids = task.user_ids.ids
        if assignee_user_ids and "user_ids" in task._fields:
            update_vals["user_ids"] = [(6, 0, list(dict.fromkeys(assignee_user_ids)))]

        if self.notes:
            current_description = task.description or ""
            timestamp = fields.Datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            note_header = f"\n\n=== Appointment Rescheduled ({timestamp}) ===\n"
            new_note = f"{note_header}{self.notes}\n"
            update_vals["description"] = current_description + new_note

        old_start = task.planned_date_begin.strftime("%Y-%m-%d %H:%M") if getattr(task, "planned_date_begin", False) else "Not set"
        new_start = start_dt.strftime("%Y-%m-%d %H:%M")

        message_body = f"""
        <p><strong>Appointment Rescheduled</strong></p>
        <ul>
            <li><strong>Previous Start:</strong> {old_start}</li>
            <li><strong>New Start:</strong> {new_start}</li>
        """

        if assignee_user_ids:
            names = ", ".join(self.env["res.users"].browse(assignee_user_ids).mapped("name"))
            message_body += f"<li><strong>Assigned To:</strong> {names}</li>"

        if team:
            message_body += f"<li><strong>Team:</strong> {team.display_name}</li>"

        if self.notes:
            message_body += f"<li><strong>Reason:</strong> {self.notes}</li>"

        message_body += "</ul>"

        try:
            task.write(update_vals)
            booking = getattr(task, "fsm_booking_id", False)
            if booking:
                booking.write({
                    "team_id": team.id,
                    "start_datetime": start_dt_utc,
                    "end_datetime": end_dt_utc,
                    "allocated_hours": duration_hours,
                    "state": "confirmed",
                })
            else:
                booking = self.env["fsm.booking"].create({
                    "task_id": task.id,
                    "team_id": team.id,
                    "start_datetime": start_dt_utc,
                    "end_datetime": end_dt_utc,
                    "allocated_hours": duration_hours,
                    "state": "confirmed",
                })
                task.fsm_booking_id = booking.id

            task.message_post(body=message_body, subject="Appointment Rescheduled")
        except Exception as e:
            raise UserError(_("Failed to update task: %s") % e)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Appointment Updated"),
                "message": _("The appointment has been rescheduled."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

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

