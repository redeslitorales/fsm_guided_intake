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
    line_ids = fields.One2many("fsm.task.intake.wizard.line", "wizard_id", string="Products/Services")
    require_products = fields.Boolean(related="task_type_id.requires_products", readonly=True)
    require_serials = fields.Boolean(related="task_type_id.requires_serials", readonly=True)
    require_signature = fields.Boolean(related="task_type_id.requires_signature", readonly=True)
    require_photos = fields.Boolean(related="task_type_id.requires_photos", readonly=True)

    # Duration
    planned_hours = fields.Float(default=lambda self: self._default_planned_hours())
    buffer_before_mins = fields.Integer(related="task_type_id.buffer_before_mins", readonly=True)
    buffer_after_mins = fields.Integer(related="task_type_id.buffer_after_mins", readonly=True)

    # Step 4
    team_id = fields.Many2one("fsm.team", string="Team", help="Optional. If empty, wizard will choose.")
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

    selected_slot = fields.Selection([("1","Option 1"),("2","Option 2"),("3","Option 3")], default="1")

    # Step 5
    notes = fields.Text(string="Internal Notes")

    # Warnings / validations (preflight)
    warning_customer_phone_missing = fields.Boolean(compute="_compute_warnings")
    warning_no_service_address = fields.Boolean(compute="_compute_warnings")
    warning_missing_serials = fields.Boolean(compute="_compute_warnings")
    warning_planned_hours_zero = fields.Boolean(compute="_compute_warnings")
    warning_task_type_mapping = fields.Boolean(compute="_compute_warnings")

    @api.model
    def _default_planned_hours(self):
        # context may include default_task_type_id
        tt = self.env["fsm.task.type"].browse(self._context.get("default_task_type_id")) if self._context.get("default_task_type_id") else None
        return tt.default_planned_hours if tt else 1.0

    @api.onchange("task_type_id")
    def _onchange_task_type(self):
        if self.task_type_id:
            self.planned_hours = self.task_type_id.default_planned_hours

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

    @api.depends("partner_id", "service_address_id", "line_ids", "planned_hours", "task_type_id")
    def _compute_warnings(self):
        for wiz in self:
            addrs = self._get_service_addresses(wiz.partner_id) if wiz.partner_id else self.env["res.partner"]
            wiz.warning_customer_phone_missing = bool(wiz.partner_id) and not bool(wiz.partner_id.phone or wiz.partner_id.mobile)
            wiz.warning_no_service_address = bool(wiz.partner_id) and len(addrs) > 1 and not bool(wiz.service_address_id)
            wiz.warning_planned_hours_zero = (wiz.planned_hours or 0.0) <= 0.0
            wiz.warning_task_type_mapping = bool(wiz.task_type_id) and not bool(wiz.task_type_id.project_id)

            if wiz.task_type_id and wiz.task_type_id.requires_serials:
                serial_lines = wiz.line_ids.filtered(lambda l: l.product_id.tracking == "serial")
                lot_lines = wiz.line_ids.filtered(lambda l: l.product_id.tracking == "lot")
                wiz.warning_missing_serials = any(not l.lot_ids for l in serial_lines) or any(not l.lot_id for l in lot_lines)
            else:
                wiz.warning_missing_serials = False

    def _preflight_errors(self):
        self.ensure_one()
        errors = []
        if self.warning_task_type_mapping:
            errors.append(_("Task type is missing a project mapping."))
        if self.warning_planned_hours_zero:
            errors.append(_("Planned hours is 0."))
        if self.warning_customer_phone_missing:
            errors.append(_("Customer is missing a phone number."))
        if self.warning_no_service_address:
            errors.append(_("No service address selected."))
        if self.task_type_id and self.task_type_id.requires_products and not self.line_ids:
            errors.append(_("This task type requires products/services, but none were added."))
        if self.warning_missing_serials:
            errors.append(_("Serial-tracked product(s) are missing serial/lot numbers."))
        return errors

    # --- Scheduling helpers ---
    def _get_service_zone_name(self):
        p = self.service_address_id or self.partner_id
        if not p:
            return ""
        if hasattr(p, "service_zone_id") and p.service_zone_id:
            return p.service_zone_id.display_name
        return ""

    def _haversine_km(self, lat1, lon1, lat2, lon2):
        r = 6371.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
        return 2 * r * math.asin(math.sqrt(a))

    def _partner_zone_key(self, partner):
        # Prefer configured service zones if present; otherwise fall back to ZIP/city buckets.
        if not partner:
            return ""
        if hasattr(partner, "service_zone_id") and partner.service_zone_id:
            return f"ZONE:{partner.service_zone_id.id}"
        return (partner.zip or "")[:3] or (partner.city or "").lower() or (partner.state_id.name or "").lower()

    def _find_top_slots(self, start_dt, limit=3):
        """Return list of dicts: {team, start, end, score} sorted best-first.
        Score boosts if same 'zone' as other tasks already booked that day.
        """
        self.ensure_one()
        if not self.task_type_id:
            return []
        planned = self.planned_hours or 0.0
        if planned <= 0:
            return []

        # apply buffers
        total_hours = planned + (self.task_type_id.buffer_before_mins + self.task_type_id.buffer_after_mins) / 60.0

        teams = self.task_type_id.capable_team_ids
        if self.team_id:
            teams = teams.filtered(lambda t: t.id == self.team_id.id)
        if not teams:
            teams = self.env["fsm.team"].search([("capable_project_ids", "in", self.task_type_id.project_id.id), ("active","=",True)])

        if not teams:
            return []

        zone = self._partner_zone_key(self.service_address_id or self.partner_id)

        # look ahead N days (v1: 30)
        results = []
        now = fields.Datetime.context_timestamp(self, start_dt) if isinstance(start_dt, datetime) else fields.Datetime.now()
        start = start_dt if isinstance(start_dt, datetime) else fields.Datetime.now()

        for day_offset in range(0, 30):
            day = (start + timedelta(days=day_offset)).date()
            weekday = str(day.weekday())  # Monday=0
            for team in teams:
                shifts = team.shift_ids.filtered(lambda s: s.weekday == weekday)
                if not shifts:
                    continue

                # bookings overlapping this day for team
                day_start = datetime.combine(day, time.min)
                day_end = datetime.combine(day, time.max)
                bookings = self.env["fsm.booking"].search([
                    ("team_id","=",team.id),
                    ("state","=","confirmed"),
                    ("start_datetime","<=", day_end),
                    ("end_datetime",">=", day_start),
                ])

                # cluster scoring: prefer same service zone; then prefer closer geo distance when available
                same_zone_count = 0
                dist_sum = 0.0
                dist_n = 0
                ref_partner = (self.service_address_id or self.partner_id)
                ref_lat = getattr(ref_partner, "partner_latitude", 0.0) or 0.0
                ref_lng = getattr(ref_partner, "partner_longitude", 0.0) or 0.0

                for b in bookings:
                    p = b.task_id.fsm_service_address_id or b.task_id.partner_id
                    z = self._partner_zone_key(p)
                    if z and z == zone:
                        same_zone_count += 1
                    lat = getattr(p, "partner_latitude", 0.0) or 0.0
                    lng = getattr(p, "partner_longitude", 0.0) or 0.0
                    if ref_lat and ref_lng and lat and lng:
                        dist_sum += self._haversine_km(ref_lat, ref_lng, lat, lng)
                        dist_n += 1
                avg_km = (dist_sum / dist_n) if dist_n else 0.0
                for shift in shifts:
                    sh_start = datetime.combine(day, time.min) + timedelta(hours=shift.start_time)
                    sh_end = datetime.combine(day, time.min) + timedelta(hours=shift.end_time)
                    shift_hours = (sh_end - sh_start).total_seconds() / 3600.0
                    capacity_hours = shift.capacity_hours if shift.capacity_hours > 0 else shift_hours

                    # Build occupied intervals within this shift for confirmed bookings
                    intervals = []
                    for b in bookings:
                        overlap_start = max(b.start_datetime, sh_start)
                        overlap_end = min(b.end_datetime, sh_end)
                        if overlap_end > overlap_start:
                            intervals.append((overlap_start, overlap_end))
                    intervals.sort(key=lambda x: x[0])

                    # Merge overlaps
                    merged = []
                    for a, b in intervals:
                        if not merged:
                            merged.append([a, b])
                        else:
                            last = merged[-1]
                            if a <= last[1]:
                                last[1] = max(last[1], b)
                            else:
                                merged.append([a, b])

                    # Quick capacity check (hours)
                    booked = sum((b - a).total_seconds() / 3600.0 for a, b in merged)
                    avail = max(0.0, capacity_hours - booked)
                    if avail + 1e-6 < total_hours:
                        continue

                    # Find earliest gap that fits total_hours (packing within shift)
                    candidate_start = sh_start
                    if day == start.date():
                        candidate_start = max(candidate_start, start)
                    found = False
                    for a, b in merged:
                        if a > candidate_start:
                            gap_hours = (a - candidate_start).total_seconds() / 3600.0
                            if gap_hours + 1e-6 >= total_hours:
                                found = True
                                break
                        candidate_start = max(candidate_start, b)
                    if not found:
                        if (sh_end - candidate_start).total_seconds() / 3600.0 + 1e-6 >= total_hours:
                            found = True
                        else:
                            continue

                    end_dt = candidate_start + timedelta(hours=total_hours)
                    if end_dt > sh_end:
                        continue

                    # score: earlier is better, more same-zone is better

                    # convert day_offset to penalty, same_zone_count to bonus
                    score = (day_offset * 1000) + (avg_km * 0.1) - (same_zone_count * 10)
                    results.append({
                        "team": team,
                        "start": candidate_start,
                        "end": end_dt,
                        "score": score,
                        "same_zone_count": same_zone_count,
                    })

            if len(results) >= limit * 5:
                # stop early once we have enough candidates
                pass

        results.sort(key=lambda r: (r["score"], r["start"]))
        return results[:limit]

    @api.depends("task_type_id", "partner_id", "service_address_id", "planned_hours", "slot_index", "team_id")
    def _compute_slots(self):
        for wiz in self:
            wiz.slot1_label = wiz.slot2_label = wiz.slot3_label = False
            wiz.slot1_start = wiz.slot2_start = wiz.slot3_start = False
            wiz.slot1_end = wiz.slot2_end = wiz.slot3_end = False

            if not wiz.task_type_id or not wiz.partner_id:
                continue
            if (wiz.planned_hours or 0.0) <= 0:
                continue

            start_dt = fields.Datetime.now() + timedelta(minutes=15)
            # Skip ahead based on slot_index (each click moves search start)
            start_dt = start_dt + timedelta(hours=wiz.slot_index * 1.0)

            slots = wiz._find_top_slots(start_dt, limit=3)
            labels = []
            for s in slots:
                team = s["team"]
                st = fields.Datetime.to_string(s["start"])
                en = fields.Datetime.to_string(s["end"])
                labels.append(_("%s — %s to %s (cluster %+d)") % (
                    team.name,
                    s["start"].strftime("%a %Y-%m-%d %H:%M"),
                    s["end"].strftime("%H:%M"),
                    s["same_zone_count"],
                ))

            if len(slots) > 0:
                wiz.slot1_start = slots[0]["start"]
                wiz.slot1_end = slots[0]["end"]
                wiz.slot1_label = labels[0]
            if len(slots) > 1:
                wiz.slot2_start = slots[1]["start"]
                wiz.slot2_end = slots[1]["end"]
                wiz.slot2_label = labels[1]
            if len(slots) > 2:
                wiz.slot3_start = slots[2]["start"]
                wiz.slot3_end = slots[2]["end"]
                wiz.slot3_label = labels[2]

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
            "planned_hours": self.planned_hours,
            "fsm_task_type_id": self.task_type_id.id,
            "description": self.notes or "",
            "fsm_service_address_id": (self.service_address_id.id if self.service_address_id else False),
            "fsm_service_zone_name": self._get_service_zone_name(),
        }
        if self.task_type_id.default_stage_id:
            task_vals["stage_id"] = self.task_type_id.default_stage_id.id
        task = self.env["project.task"].create(task_vals)

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

        # Open created task
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "view_mode": "form",
            "res_id": task.id,
        }
