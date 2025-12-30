# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime, timedelta, time
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
    line_ids = fields.One2many("fsm.task.intake.wizard.line", "wizard_id", string="Products/Services")
    require_products = fields.Boolean(related="task_type_id.requires_products", readonly=True)
    require_serials = fields.Boolean(related="task_type_id.requires_serials", readonly=True)
    require_signature = fields.Boolean(related="task_type_id.requires_signature", readonly=True)
    require_photos = fields.Boolean(related="task_type_id.requires_photos", readonly=True)

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

    selected_slot = fields.Selection(selection="_get_slot_selection", default="1")
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

    def _get_slot_selection(self):
        """Generate selection options with formatted datetime labels"""
        self.ensure_one()
        
        options = []
        if self.slot1_label:
            options.append(("1", self.slot1_label))
        else:
            options.append(("1", _("No available slot")))
            
        if self.slot2_label:
            options.append(("2", self.slot2_label))
        else:
            options.append(("2", _("No available slot")))
            
        if self.slot3_label:
            options.append(("3", self.slot3_label))
        else:
            options.append(("3", _("No available slot")))
            
        return options

    def _get_slot_label_map(self):
        self.ensure_one()
        return {
            "1": self.slot1_label or _("No available slot"),
            "2": self.slot2_label or _("No available slot"),
            "3": self.slot3_label or _("No available slot"),
        }

    @api.onchange("partner_id")
    def _onchange_partner(self):
        if self.partner_id and not self.service_address_id:
            # best effort: if only one child address, pick it
            addrs = self._get_service_addresses(self.partner_id)
            if len(addrs) == 1:
                self.service_address_id = addrs.id

    def _get_service_addresses(self, partner):
        return partner.child_ids.filtered(lambda p: p.type in ("delivery", "other", "contact"))

    @api.depends("partner_id")
    def _compute_service_address_visibility(self):
        for wiz in self:
            addrs = self._get_service_addresses(wiz.partner_id) if wiz.partner_id else self.env["res.partner"]
            wiz.show_service_address = len(addrs) > 1

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
            capable = wiz.task_type_id.capable_team_ids
            if capable:
                wiz.qualified_team_ids = capable
            else:
                # fallback: all active teams
                wiz.qualified_team_ids = self.env["fsm.team"].search([("active", "=", True)])

    @api.depends("selected_slot", "slot1_label", "slot2_label", "slot3_label")
    def _compute_selected_slot_label(self):
        for wiz in self:
            labels = {
                "1": wiz.slot1_label or _("No available slot"),
                "2": wiz.slot2_label or _("No available slot"),
                "3": wiz.slot3_label or _("No available slot"),
            }
            wiz.selected_slot_label = labels.get(wiz.selected_slot or "1", _("No available slot"))

    def _find_top_slots(self, start_dt, limit=3):
        """
        Return a list of top available slots sorted by start time.
        Each slot is a dict: {"start": datetime, "end": datetime, "team": fsm.team}.
        """
        self.ensure_one()
        needed_hours = self.planned_hours or 1.0
        buffer_before = timedelta(minutes=(self.buffer_before_mins or 0))
        buffer_after = timedelta(minutes=(self.buffer_after_mins or 0))

        teams = self.qualified_team_ids
        if not teams:
            teams = self.env["fsm.team"].search([("active", "=", True)])

        slots = []
        # Scan a few days ahead
        search_end = start_dt + timedelta(days=14)
        for team in teams:
            # Check if team has shifts
            if not team.shift_ids:
                continue
            # Loop through days
            current_day = start_dt.date()
            while datetime.combine(current_day, time.min) < search_end:
                weekday_str = str(current_day.weekday())
                shifts = team.shift_ids.filtered(lambda s: s.weekday == weekday_str)
                for shift in shifts:
                    shift_start_hour, shift_start_min = float_hours_to_hm(shift.start_time)
                    shift_end_hour, shift_end_min = float_hours_to_hm(shift.end_time)
                    shift_start_dt = datetime.combine(current_day, time(shift_start_hour, shift_start_min))
                    shift_end_dt = datetime.combine(current_day, time(shift_end_hour, shift_end_min))
                    
                    # Only consider if shift_start >= start_dt
                    if shift_start_dt < start_dt:
                        shift_start_dt = start_dt
                    
                    # Check if we can fit the needed hours + buffers
                    slot_start = shift_start_dt
                    slot_end = slot_start + timedelta(hours=needed_hours)
                    
                    # Make sure slot_end doesn't exceed shift_end_dt
                    if slot_end > shift_end_dt:
                        continue
                    
                    # Check capacity (simplified: assume no overlapping bookings check for now)
                    # In production, you'd check fsm.booking records for this team/time
                    
                    slots.append({
                        "start": slot_start,
                        "end": slot_end,
                        "team": team,
                    })
                
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

            if not wiz.task_type_id or not wiz.partner_id:
                continue
            if (wiz.planned_hours or 0.0) <= 0:
                continue

            start_dt = fields.Datetime.now() + timedelta(minutes=15)
            # Skip ahead based on slot_index (each click moves search start)
            start_dt = start_dt + timedelta(hours=wiz.slot_index * 1.0)

            slots = wiz._find_top_slots(start_dt, limit=3)
            
            # Format labels with proper datetime display
            if len(slots) > 0:
                wiz.slot1_start = slots[0]["start"]
                wiz.slot1_end = slots[0]["end"]
                wiz.slot1_label = _("%s, %s - %s") % (
                    slots[0]["start"].strftime("%a, %B %d"),
                    slots[0]["start"].strftime("%H:%M"),
                    slots[0]["end"].strftime("%H:%M"),
                )
            if len(slots) > 1:
                wiz.slot2_start = slots[1]["start"]
                wiz.slot2_end = slots[1]["end"]
                wiz.slot2_label = _("%s, %s - %s") % (
                    slots[1]["start"].strftime("%a, %B %d"),
                    slots[1]["start"].strftime("%H:%M"),
                    slots[1]["end"].strftime("%H:%M"),
                )
            if len(slots) > 2:
                wiz.slot3_start = slots[2]["start"]
                wiz.slot3_end = slots[2]["end"]
                wiz.slot3_label = _("%s, %s - %s") % (
                    slots[2]["start"].strftime("%a, %B %d"),
                    slots[2]["start"].strftime("%H:%M"),
                    slots[2]["end"].strftime("%H:%M"),
                )

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
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map()),
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
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map()),
        }

    def action_more_options(self):
        self.ensure_one()
        self.slot_index += 1
        return {
            "type": "ir.actions.act_window",
            "res_model": "fsm.task.intake.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
            "name": self._get_wizard_title(),
            "context": dict(self.env.context, slot_labels=self._get_slot_label_map()),
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
        task_fields = self.env["project.task"]._fields
        start_dt = fields.Datetime.to_datetime(start_dt) if start_dt else start_dt
        end_dt = fields.Datetime.to_datetime(end_dt) if end_dt else end_dt
        if start_dt:
            if not end_dt or end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=self.planned_hours or 0.0)
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(minutes=1)
        if "planned_date_begin" in task_fields:
            task_vals["planned_date_begin"] = start_dt
        if "planned_date_end" in task_fields:
            task_vals["planned_date_end"] = end_dt
        if "date_start" in task_fields:
            task_vals["date_start"] = start_dt
        if "date_end" in task_fields:
            task_vals["date_end"] = end_dt
        if "date_deadline" in task_fields:
            deadline_dt = end_dt or (start_dt + timedelta(hours=self.planned_hours or 0.0))
            task_vals["date_deadline"] = fields.Date.to_date(deadline_dt)
        if "planned_hours" in self.env["project.task"]._fields:
            task_vals["planned_hours"] = self.planned_hours
        if self.sale_order_id and "sale_order_id" in task_fields:
            task_vals["sale_order_id"] = self.sale_order_id.id
        if self.task_type_id.default_stage_id:
            task_vals["stage_id"] = self.task_type_id.default_stage_id.id
        task = self.env["project.task"].create(task_vals)
        task.flush()

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
        booking = self.env["fsm.booking"].create({
            "task_id": task.id,
            "team_id": team.id,
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "allocated_hours": alloc_hours,
            "state": "confirmed",
        })
        task.fsm_booking_id = booking.id

        # Create delivery + reserve (as requested)
        booking.action_create_or_update_delivery()
        booking.flush()

        # Open created task
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "view_mode": "form",
            "res_id": task.id,
        }
