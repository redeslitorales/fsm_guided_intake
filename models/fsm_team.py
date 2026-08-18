# -*- coding: utf-8 -*-
from datetime import datetime, time
import logging

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)

class FsmTeam(models.Model):
    _name = "fsm.team"
    _description = "FSM Team"
    _order = "name"

    name = fields.Char(
        string="Team Name",
        compute="_compute_name",
        store=True,
        readonly=True,
        help="Auto-generated name based on lead/warehouse to avoid manual typing",
    )
    active = fields.Boolean(default=True)

    member_ids = fields.Many2many("hr.employee", string="Technicians")
    vehicle_ids = fields.Many2many("fleet.vehicle", string="Vehicles")
    lead_user_id = fields.Many2one("res.users", string="Team Lead")

    calendar_id = fields.Many2one("resource.calendar", string="Working Calendar")
    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse")

    skill_level = fields.Selection(
        [
            ("L1", "L1"),
            ("L2", "L2"),
            ("L3", "L3"),
        ],
        string="Skill Level",
        help="Minimum skill level this team is equipped to handle.",
    )

    capable_project_ids = fields.Many2many("project.project", string="Capable Projects")
    capable_task_type_ids = fields.Many2many(
        "fsm.task.type",
        "fsm_task_type_fsm_team_rel",
        "fsm_team_id",
        "fsm_task_type_id",
        string="Capable Task Types",
    )
    shift_ids = fields.One2many("fsm.team.shift", "team_id", string="Shifts")

    def _fsm_roster_employees(self):
        employees = self.env["hr.employee"]
        for team in self:
            employees |= team.member_ids
            if team.lead_user_id.employee_id:
                employees |= team.lead_user_id.employee_id
        return employees

    def _fsm_planning_team_for_employee(self, employee):
        """Resolve the employee's current legacy team for bridge propagation."""
        teams = self.search([
            ("active", "=", True),
            "|",
            ("member_ids", "in", employee.id),
            ("lead_user_id.employee_id", "=", employee.id),
        ])
        preferred = teams & self
        return preferred[:1] or teams[:1]

    def _fsm_sync_impacted_planning_teams(self, employees, effective_date=None):
        """Apply a static roster edit to Planning from its effective date.

        This is a transition bridge. It never rewrites historical shifts and
        only changes the dated team assignment; Planning remains the schedule
        and publication source of truth.
        """
        employees = employees.filtered(lambda employee: employee.resource_id)
        if not employees:
            return 0
        role = self.env.ref(
            "fsm_guided_intake.planning_role_fsm_technician",
            raise_if_not_found=False,
        )
        if not role:
            return 0

        effective_date = fields.Date.to_date(
            effective_date
            or self.env.context.get("fsm_team_effective_date")
            or fields.Date.context_today(self)
        )
        timezone = pytz.timezone(
            self.env.context.get("tz")
            or self.env.user.tz
            or "America/El_Salvador"
        )
        effective_start_utc = timezone.localize(
            datetime.combine(effective_date, time.min)
        ).astimezone(pytz.UTC).replace(tzinfo=None)

        changed = 0
        for employee in employees:
            target_team = self._fsm_planning_team_for_employee(employee)
            slots = self.env["planning.slot"].sudo().search([
                ("resource_id", "=", employee.resource_id.id),
                ("role_id", "=", role.id),
                ("end_datetime", ">", effective_start_utc),
            ])
            to_update = slots.filtered(
                lambda slot: slot.fsm_team_id != target_team
            )
            if to_update:
                to_update.with_context(
                    fsm_skip_team_planning_sync=True
                ).write({"fsm_team_id": target_team.id or False})
                changed += len(to_update)
        if changed:
            _logger.info(
                "Updated %s technician Planning shift team assignments from %s",
                changed,
                effective_date,
            )
        return changed

    @api.model_create_multi
    def create(self, vals_list):
        teams = super().create(vals_list)
        if not self.env.context.get("fsm_skip_team_planning_sync"):
            teams._fsm_sync_impacted_planning_teams(
                teams._fsm_roster_employees()
            )
        return teams

    def write(self, vals):
        roster_fields = {"member_ids", "lead_user_id", "active"}
        sync_roster = bool(roster_fields & set(vals)) and not self.env.context.get(
            "fsm_skip_team_planning_sync"
        )
        previous_employees = self._fsm_roster_employees() if sync_roster else self.env["hr.employee"]
        result = super().write(vals)
        if sync_roster:
            impacted_employees = previous_employees | self._fsm_roster_employees()
            self._fsm_sync_impacted_planning_teams(impacted_employees)
        return result

    @api.depends("lead_user_id", "warehouse_id", "member_ids", "member_ids.name")
    def _compute_name(self):
        for team in self:
            parts = []
            if team.lead_user_id:
                parts.append(team.lead_user_id.name)
            if team.warehouse_id:
                parts.append(team.warehouse_id.name)
            if not parts and team.member_ids:
                # Use up to two member names as a fallback label
                member_names = [m.name for m in team.member_ids[:2] if m.name]
                if member_names:
                    parts.append(" / ".join(member_names))
            fallback = _("Team %s") % (team.id or _("New"))
            team.name = " - ".join(parts) if parts else fallback

    def get_default_picking_type_out(self):
        self.ensure_one()
        if self.warehouse_id and self.warehouse_id.out_type_id:
            return self.warehouse_id.out_type_id
        # fallback: any outgoing type
        return self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1)

class FsmTeamShift(models.Model):
    _name = "fsm.team.shift"
    _description = "FSM Team Shift"
    _order = "team_id, pattern, start_time"

    team_id = fields.Many2one("fsm.team", required=True, ondelete="cascade")
    name = fields.Char(required=True)

    pattern = fields.Selection([
        ("sun_thu", "Sun-Thu"),
        ("mon_fri", "Mon-Fri"),
        ("tue_sat", "Tue-Sat"),
        ("wed_sun", "Wed-Sun"),
        ("thu_mon", "Thu-Mon"),
        ("fri_wed", "Fri-Wed"),
    ], required=True, default="mon_fri",
        help="Days this shift covers. Example: Mon-Fri covers Monday through Friday each week.")

    start_time = fields.Float(required=True, help="Hour in 24h format. Example: 8.5 for 08:30")
    end_time = fields.Float(required=True, help="Hour in 24h format. Example: 17.0 for 17:00")

    capacity_hours = fields.Float(required=True, default=8.0,
                                  help="Total hours the team can perform during this shift.")

    @api.constrains("start_time", "end_time", "capacity_hours")
    def _check_shift(self):
        for rec in self:
            if rec.end_time <= rec.start_time:
                raise ValidationError(_("Shift end time must be after start time."))
            if rec.capacity_hours <= 0:
                raise ValidationError(_("Shift capacity must be > 0."))

    def _get_resource_calendar(self):
        """Return the calendar that defines capacity for this shift.

        Requirement: derive shift capacity from the team lead's resource calendar
        when available; fall back to the team's calendar if the lead has none.
        """
        self.ensure_one()
        lead_calendar = False
        if self.team_id and self.team_id.lead_user_id:
            # Prefer the employee calendar; fall back to user calendar if present.
            lead_calendar = (
                getattr(self.team_id.lead_user_id, "employee_id", False)
                and self.team_id.lead_user_id.employee_id.resource_calendar_id
            ) or getattr(self.team_id.lead_user_id, "resource_calendar_id", False)

        return lead_calendar or self.team_id.calendar_id

    def _hours_for_weekday(self, weekday):
        """Compute usable hours for a specific weekday based on the lead's calendar.

        The shift window (start/end) is intersected with the calendar attendances for
        the given weekday. If no calendar is available, fall back to the shift's
        configured capacity_hours.
        """
        self.ensure_one()
        calendar = self._get_resource_calendar()
        if not calendar:
            return max(0.0, self.capacity_hours or (self.end_time - self.start_time))

        attendances = calendar.attendance_ids.filtered(lambda a: int(a.dayofweek) == weekday)
        if not attendances:
            return 0.0

        total = 0.0
        for att in attendances:
            overlap_start = max(att.hour_from, self.start_time)
            overlap_end = min(att.hour_to, self.end_time)
            if overlap_end > overlap_start:
                total += overlap_end - overlap_start
        return total

    def _get_weekday_set(self):
        """Return a set of Python weekday ints covered by this shift pattern."""
        mapping = {
            "sun_thu": {6, 0, 1, 2, 3},
            "mon_fri": {0, 1, 2, 3, 4},
            "tue_sat": {1, 2, 3, 4, 5},
            "wed_sun": {2, 3, 4, 5, 6},
            "thu_mon": {3, 4, 5, 6, 0},
            "fri_wed": {4, 5, 6, 0, 1, 2},
        }
        return mapping.get(self.pattern, set())
