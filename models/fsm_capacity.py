# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from datetime import timedelta


def _skill_rank(skill):
    order = {"L1": 1, "L2": 2, "L3": 3}
    return order.get(skill or "", 0)


class FsmCapacityDay(models.Model):
    _name = "fsm.capacity.day"
    _description = "FSM Daily Capacity"
    _order = "date desc, team_id"

    name = fields.Char(string="Label", compute="_compute_name", store=True)
    team_id = fields.Many2one("fsm.team", string="Team", required=True, ondelete="cascade")
    date = fields.Date(required=True)
    shift_id = fields.Many2one("fsm.team.shift", string="Shift", ondelete="set null")
    skill_level = fields.Selection(
        [
            ("L1", "L1"),
            ("L2", "L2"),
            ("L3", "L3"),
        ],
        string="Skill Level",
        help="Minimum skill level that can work this capacity bucket.",
    )
    capacity_kind = fields.Selection(
        [
            ("manual", "Manual"),
            ("import", "Imported"),
            ("forecast", "Forecast"),
        ],
        string="Capacity Type",
        default="manual",
        required=True,
        help="Tracks where this capacity entry originated.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
        ],
        default="draft",
        required=True,
    )

    total_minutes = fields.Integer(string="Total (min)", required=True, default=0)
    reserved_minutes = fields.Integer(string="Reserved Urgent (min)", default=0)
    sellable_minutes = fields.Integer(
        string="Sellable (min)",
        compute="_compute_sellable_minutes",
        store=True,
    )
    booked_minutes = fields.Integer(string="Booked (min)", default=0)
    remaining_minutes = fields.Integer(
        string="Remaining (min)",
        compute="_compute_remaining_minutes",
        store=True,
    )

    line_ids = fields.One2many(
        "fsm.capacity.day.line",
        "capacity_day_id",
        string="Task Type Lines",
    )

    _sql_constraints = [
        (
            "team_date_unique",
            "unique(team_id, date, shift_id, skill_level)",
            "A team already has a capacity entry for this date/shift/skill level.",
        )
    ]

    @api.depends("team_id", "date", "shift_id")
    def _compute_name(self):
        for rec in self:
            team_name = rec.team_id.display_name or _("Team")
            date_label = rec.date or _("No Date")
            shift_label = rec.shift_id.name if rec.shift_id else False
            parts = [str(date_label), team_name]
            if shift_label:
                parts.append(shift_label)
            rec.name = " - ".join(parts)

    @api.depends("total_minutes", "reserved_minutes")
    def _compute_sellable_minutes(self):
        for rec in self:
            rec.sellable_minutes = max(0, (rec.total_minutes or 0) - (rec.reserved_minutes or 0))

    @api.depends("sellable_minutes", "booked_minutes")
    def _compute_remaining_minutes(self):
        for rec in self:
            rec.remaining_minutes = max(0, (rec.sellable_minutes or 0) - (rec.booked_minutes or 0))

    @api.constrains("total_minutes", "reserved_minutes", "booked_minutes")
    def _check_minutes(self):
        for rec in self:
            for field_name in ["total_minutes", "reserved_minutes", "booked_minutes"]:
                value = getattr(rec, field_name) or 0
                if value < 0:
                    raise ValidationError(_("%s cannot be negative.") % rec._fields[field_name].string)

    # ---- Capacity generation helpers ----
    def _protection_config(self):
        icp = self.env["ir.config_parameter"].sudo()
        def _get(key, default):
            val = icp.get_param(key, default)
            try:
                return float(val)
            except Exception:
                return default
        return {
            "standard_to_basic": _get("fsm_guided_intake.protect_standard_to_basic_pct", 0.25),
            "fiber_to_basic": _get("fsm_guided_intake.protect_fiber_to_basic_pct", 0.40),
            "fiber_to_standard": _get("fsm_guided_intake.protect_fiber_to_standard_pct", 0.40),
            "urgent_reserve": _get("fsm_guided_intake.urgent_capacity_reserve_pct", 0.0),
        }

    def _matching_shift_for_date(self, target_date):
        self.ensure_one()
        weekday = target_date.weekday()
        for shift in self.team_id.shift_ids:
            if weekday in shift._get_weekday_set():
                return shift
        return False

    def _rebuild_bucket_lines(self, protection_cfg=None):
        protection_cfg = protection_cfg or self._protection_config()
        for rec in self:
            if not rec.team_id:
                rec.line_ids = [(5, 0, 0)]
                continue
            skill = rec.team_id.skill_level
            total = rec.sellable_minutes or 0
            lines = []

            def _protection_pct(bucket_skill):
                # bucket_skill is the work type; team skill is rec.team_id.skill_level
                if _skill_rank(bucket_skill) == _skill_rank(skill):
                    return 0.0
                if bucket_skill == "L1" and skill == "L2":
                    return protection_cfg.get("standard_to_basic", 0.25)
                if bucket_skill == "L1" and skill == "L3":
                    return protection_cfg.get("fiber_to_basic", 0.40)
                if bucket_skill == "L2" and skill == "L3":
                    return protection_cfg.get("fiber_to_standard", 0.40)
                return 0.0

            for bucket_skill in ("L3", "L2", "L1"):
                if _skill_rank(skill) < _skill_rank(bucket_skill):
                    continue
                prot_pct = _protection_pct(bucket_skill)
                protected = int(round(total * prot_pct))
                sellable = max(0, total - protected)
                lines.append(
                    (0, 0, {
                        "bucket_skill_level": bucket_skill,
                        "total_minutes": total,
                        "protected_minutes": protected,
                        "sellable_minutes": sellable,
                        "available_minutes": sellable,
                    })
                )

            rec.line_ids = [(5, 0, 0)] + lines

    @api.model
    def generate_from_shifts(self, date_start=None, date_end=None):
        """Generate daily capacity for active teams based on their shifts and protection rules."""
        start = fields.Date.to_date(date_start) if date_start else fields.Date.context_today(self)
        end = fields.Date.to_date(date_end) if date_end else start
        protection_cfg = self._protection_config()
        urgent_pct = protection_cfg.get("urgent_reserve", 0.0) or 0.0

        teams = self.env["fsm.team"].search([("active", "=", True)])
        current = start
        created = self.browse()
        while current <= end:
            weekday = current.weekday()
            for team in teams:
                shift = False
                for cand in team.shift_ids:
                    if weekday in cand._get_weekday_set():
                        shift = cand
                        break
                if not shift:
                    continue

                # Derive hours from the team lead's resource calendar for this weekday
                # and intersect with the shift window.
                hours = shift._hours_for_weekday(weekday)
                if hours <= 0:
                    continue

                total_minutes = int(round(hours * 60))
                # Urgent reserve only applies to L3-capable teams
                urgent_reserved = int(round(total_minutes * urgent_pct)) if team.skill_level == "L3" else 0

                vals = {
                    "team_id": team.id,
                    "date": current,
                    "shift_id": shift.id,
                    "skill_level": team.skill_level,
                    "capacity_kind": "forecast",
                    "state": "ready",
                    "total_minutes": total_minutes,
                    "reserved_minutes": urgent_reserved,
                }
                rec = self.search([
                    ("team_id", "=", team.id),
                    ("date", "=", current),
                    ("shift_id", "=", shift.id),
                    ("skill_level", "=", team.skill_level),
                ], limit=1)

                if rec:
                    rec.write(vals)
                else:
                    rec = self.create(vals)
                rec._rebuild_bucket_lines(protection_cfg=protection_cfg)
                created |= rec
            current = current + timedelta(days=1)
        return created

    @api.model
    def cron_generate_from_shifts(self):
        start = fields.Date.context_today(self)
        end = start + timedelta(days=14)
        return self.generate_from_shifts(start, end)


class FsmCapacityDayLine(models.Model):
    _name = "fsm.capacity.day.line"
    _description = "FSM Daily Capacity Line"
    _order = "capacity_day_id, id"

    capacity_day_id = fields.Many2one("fsm.capacity.day", required=True, ondelete="cascade")
    task_type_id = fields.Many2one("fsm.task.type", string="Task Type", ondelete="restrict")
    bucket_skill_level = fields.Selection(
        [
            ("L1", "Basic (L1)"),
            ("L2", "Standard (L2)"),
            ("L3", "Fiber (L3)"),
        ],
        string="Capability Bucket",
        required=True,
        help="Capability bucket this line represents; higher skill may lend capacity to lower buckets with protection reserves.",
    )
    total_minutes = fields.Integer(string="Total (min)", default=0)
    protected_minutes = fields.Integer(string="Protected From Downgrade (min)", default=0)
    sellable_minutes = fields.Integer(string="Sellable (min)", default=0)
    available_minutes = fields.Integer(string="Available (min)", default=0)

    @api.constrains("available_minutes")
    def _check_available_minutes(self):
        for rec in self:
            if rec.available_minutes < 0:
                raise ValidationError(_("Available minutes cannot be negative."))