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

    state = fields.Selection([
        ("type", "Type"),
        ("customer", "Customer"),
        ("products", "Products"),
        ("schedule", "Schedule"),
        ("notes", "Notes"),
        ("confirm", "Confirm"),
    ], default="type", required=True)

    # Step 1
    task_type_id = fields.Many2one("fsm.task.type", string="What are we doing?", required=True)

    # Step 2
    partner_id = fields.Many2one("res.partner", string="Customer")
    partner_phone = fields.Char(related="partner_id.phone", readonly=True)
    subscription_id = fields.Many2one(
        "sale.order",
        string="Subscription",
        domain="[('partner_id', '=', partner_id)]",
        help="Active subscription for the selected customer."
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
        domain="[('partner_id', '=', partner_id)]",
        help="Select an existing sales order to reuse for this task."
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
    slot1_label = fields.Char(compute="_compute_slots")
    slot2_label = fields.Char(compute="_compute_slots")
    slot3_label = fields.Char(compute="_compute_slots")
    slot1_start = fields.Datetime(compute="_compute_slots")
    slot2_start = fields.Datetime(compute="_compute_slots")
    slot3_start = fields.Datetime(compute="_compute_slots")
    slot1_end = fields.Datetime(compute="_compute_slots")
    slot2_end = fields.Datetime(compute="_compute_slots")
    slot3_end = fields.Datetime(compute="_compute_slots")
    slot1_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True)
    slot2_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True)
    slot3_team_id = fields.Many2one("fsm.team", compute="_compute_slots", readonly=True)
    slot1_team_label = fields.Char(compute="_compute_slots", readonly=True)
    slot2_team_label = fields.Char(compute="_compute_slots", readonly=True)
    slot3_team_label = fields.Char(compute="_compute_slots", readonly=True)
    slot1_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True)
    slot2_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True)
    slot3_is_preferred = fields.Boolean(compute="_compute_slots", readonly=True)
    search_start_dt = fields.Datetime(string="Slot Search Start", readonly=False)

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
            "type": _("Select Activity"),
            "customer": _("Select Customer"),
            "products": _("Select Products"),
            "schedule": _("Select Date/Time"),
            "notes": _("Enter Notes"),
            "confirm": _("Confirm Appointment"),
        }
        return titles.get(self.state, "")

    def _get_wizard_title(self):
        self.ensure_one()
        return _("Create Field Service Task - %s") % (self._get_state_title() or "")

    def _get_slot_label_map(self):
        self.ensure_one()
        # Ensure slot labels are up to date before sharing them with the UI context
        self._compute_slots()
        return {
            "1": self.slot1_label or _("No available slot"),
            "2": self.slot2_label or _("No available slot"),
            "3": self.slot3_label or _("No available slot"),
        }

    @api.model
    def _get_slot_selection(self):
        labels = self.env.context.get("slot_labels") or {
            "1": _("Slot 1"),
            "2": _("Slot 2"),
            "3": _("Slot 3"),
        }
        return [(key, labels.get(key) or _("Slot %s") % key) for key in ["1", "2", "3"]]

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
            if wiz.partner_id:
                count = self.env["sale.order"].search_count([
                    ("partner_id", "=", wiz.partner_id.id)
                ])
                wiz.has_existing_sales_orders = count > 0
            else:
                wiz.has_existing_sales_orders = False

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
            project = self.task_type_id.project_id
            if project and hasattr(project, "allow_materials") and not project.allow_materials:
                errors.append(_("Project '%s' must allow materials when products are required.") % project.display_name)
        if (self.planned_hours or 0.0) == 0.0:
            errors.append(_("Planned hours cannot be 0."))
        if self.task_type_id and self.task_type_id.requires_products:
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

    def _find_top_slots(self, start_dt, limit=3):
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

        teams = self.qualified_team_ids
        if not teams:
            teams = self.env["fsm.team"].search([("active", "=", True)])

        slots = []
        # Scan a few days ahead
        search_end = start_dt + timedelta(days=14)

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
            existing_bookings = self.env["fsm.booking"].search([
                ("team_id", "in", team_ids_for_lead),
                ("state", "!=", "cancelled"),
                ("start_datetime", "<", search_end),
                ("end_datetime", ">", start_dt),
            ])
            # Loop through days
            current_day = start_dt.date()
            while datetime.combine(current_day, time.min) < search_end:
                weekday_str = str(current_day.weekday())
                day_attendances = attendances.filtered(lambda a: a.dayofweek == weekday_str)
                if day_attendances:
                    earliest = min(day_attendances.mapped("hour_from"))
                    latest = max(day_attendances.mapped("hour_to"))

                    start_hour, start_min = float_hours_to_hm(earliest)
                    end_hour, end_min = float_hours_to_hm(latest)
                    shift_start_dt = datetime.combine(current_day, time(start_hour, start_min)) + timedelta(minutes=30)
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

    @api.depends("task_type_id", "partner_id", "planned_hours", "slot_index")
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

            start_dt = wiz.search_start_dt or (fields.Datetime.now() + timedelta(minutes=15))
            # Scan forward in 2-hour increments (up to ~7 days) until we find slots.
            slots = []
            chosen_start = start_dt
            max_attempts = 84  # 2-hour steps for 7 days
            for attempt in range(max_attempts):
                start_dt_attempt = start_dt + timedelta(hours=attempt * 2.0)
                start_dt_attempt = wiz._round_to_nearest_10(start_dt_attempt)
                slots = wiz._find_top_slots(start_dt_attempt, limit=3)
                if slots:
                    chosen_start = start_dt_attempt
                    break
            # remember the start used; next run will bump from the last shown window
            wiz.search_start_dt = chosen_start

            # If still nothing, leave defaults; downstream will keep empties.

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
                wiz.search_start_dt = last_end + timedelta(hours=2.0)

    # Navigation
    def action_next(self):
        self.ensure_one()
        order = ["type","customer","products","schedule","notes","confirm"]
        idx = order.index(self.state)
        if self.state == "confirm":
            return {"type": "ir.actions.act_window_close"}
        if self.state == "customer" and not self.partner_id:
            raise UserError(_("Please select a customer before continuing."))
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
        order = ["type","customer","products","schedule","notes","confirm"]
        idx = order.index(self.state)
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
        if self.sale_order_id and "sale_order_id" in task_fields:
            task_vals["sale_order_id"] = self.sale_order_id.id
        if self.task_type_id.default_stage_id:
            task_vals["stage_id"] = self.task_type_id.default_stage_id.id
        try:
            task = self.env["project.task"].create(task_vals)
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
            booking = self.env["fsm.booking"].create({
                "task_id": task.id,
                "team_id": team.id,
                "start_datetime": start_dt_utc,
                "end_datetime": end_dt_utc,
                "allocated_hours": duration_hours,
                "state": "confirmed",
            })
            task.fsm_booking_id = booking.id

            # Create delivery + reserve (as requested)
            booking.action_create_or_update_delivery()
        except Exception as e:
            raise UserError(_("Booking creation failed: %s\nDebug payload: %s") % (e, debug_payload))

        # Open created task
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "view_mode": "form",
            "res_id": task.id,
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
