# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _


class PlanningSlot(models.Model):
    _inherit = "planning.slot"

    fsm_employee_schedule_generated = fields.Boolean(
        string="Generated from Employee Schedule",
        default=False,
        copy=False,
        index=True,
        readonly=True,
    )
    fsm_schedule_date = fields.Date(
        string="Employee Schedule Date",
        copy=False,
        index=True,
        readonly=True,
    )
    fsm_schedule_key = fields.Char(
        string="Employee Schedule Key",
        copy=False,
        index=True,
        readonly=True,
    )
    fsm_schedule_calendar_id = fields.Many2one(
        "resource.calendar",
        string="Source Working Schedule",
        copy=False,
        readonly=True,
        ondelete="set null",
    )
    fsm_team_id = fields.Many2one(
        "fsm.team",
        string="Technician Team",
        copy=True,
        index=True,
        ondelete="restrict",
        help=(
            "The dated technician-team assignment for this shift. Published "
            "shifts with the Técnico role define team availability."
        ),
    )

    _sql_constraints = [
        (
            "fsm_schedule_key_unique",
            "unique(fsm_schedule_key)",
            "A Planning shift has already been generated for this employee and date.",
        ),
    ]

    def _get_fields_breaking_publication(self):
        return super()._get_fields_breaking_publication() + ["fsm_team_id"]

    @api.model
    def _fsm_technician_employees(self):
        """Technicians are the active employees assigned to active FSM teams."""
        teams = self.env["fsm.team"].sudo().search([("active", "=", True)])
        employees = teams.mapped("member_ids") | teams.mapped("lead_user_id.employee_id")
        return employees.filtered(
            lambda employee: employee.active
            and employee.resource_id
            and employee.resource_calendar_id
        )

    @api.model
    def _fsm_technician_role(self):
        return self.env.ref("fsm_guided_intake.planning_role_fsm_technician")

    @api.model
    def _fsm_schedule_key_for(self, employee, schedule_date):
        return "fsm-employee-schedule-%s-%s" % (employee.id, schedule_date.isoformat())

    @api.model
    def _fsm_seed_team_by_employee(self, employees):
        """Bootstrap shift teams from the legacy static roster.

        Once a shift has a team, synchronization preserves it. Planning is then
        authoritative and planners may move a draft shift to another team.
        """
        result = {}
        teams = self.env["fsm.team"].sudo().search([("active", "=", True)])
        for team in teams:
            team_employees = team.member_ids | team.lead_user_id.employee_id
            for employee in team_employees & employees:
                result.setdefault(employee.id, team)
        return result

    @api.model
    def _fsm_calendar_shift_bounds(self, employee, schedule_date):
        """Return the outer UTC bounds of an employee's work periods for a date.

        Lunch periods are excluded by Odoo's attendance interval API. The Planning
        shift spans from the first work period to the last so the native Planning
        view shows one daily shift, including the intervening lunch break.
        """
        calendar = employee.resource_calendar_id
        timezone = pytz.timezone(employee.tz or calendar.tz or "UTC")
        local_start = timezone.localize(datetime.combine(schedule_date, time.min))
        local_end = timezone.localize(datetime.combine(schedule_date + timedelta(days=1), time.min))
        intervals_by_resource = calendar._attendance_intervals_batch(
            local_start,
            local_end,
            resources=employee.resource_id,
            tz=timezone,
        )
        intervals = intervals_by_resource.get(employee.resource_id.id, [])
        if not intervals:
            return False, False

        start_datetime = min(interval[0] for interval in intervals)
        end_datetime = max(interval[1] for interval in intervals)
        return (
            start_datetime.astimezone(pytz.UTC).replace(tzinfo=None),
            end_datetime.astimezone(pytz.UTC).replace(tzinfo=None),
        )

    @api.model
    def _fsm_assign_technician_role(self, employees, role):
        for employee in employees:
            values = {}
            if role not in employee.planning_role_ids:
                values["planning_role_ids"] = [(4, role.id)]
            if not employee.default_planning_role_id:
                values["default_planning_role_id"] = role.id
            if values:
                employee.sudo().write(values)

    @api.model
    def sync_fsm_technician_shifts(self, date_start=None, date_end=None):
        """Synchronize technician Planning shifts with employee work schedules.

        The default window is the current and following calendar week. Only
        records tagged by this integration are updated or removed; manual
        Planning shifts are never modified.
        """
        today = fields.Date.context_today(self)
        default_start = today - timedelta(days=today.weekday())
        date_start = fields.Date.to_date(date_start) if date_start else default_start
        date_end = fields.Date.to_date(date_end) if date_end else date_start + timedelta(days=13)
        if date_end < date_start:
            raise ValueError(_("The Planning shift end date cannot be before the start date."))

        role = self._fsm_technician_role().sudo()
        employees = self._fsm_technician_employees().sudo()
        self._fsm_assign_technician_role(employees, role)
        seed_team_by_employee = self._fsm_seed_team_by_employee(employees)

        existing_slots = self.sudo().search([
            ("fsm_employee_schedule_generated", "=", True),
            ("fsm_schedule_date", ">=", date_start),
            ("fsm_schedule_date", "<=", date_end),
        ])
        existing_by_key = {slot.fsm_schedule_key: slot for slot in existing_slots}
        desired_keys = set()
        created = self.browse()
        updated = self.browse()
        protected = self.browse()

        current_date = date_start
        while current_date <= date_end:
            for employee in employees:
                start_datetime, end_datetime = self._fsm_calendar_shift_bounds(
                    employee, current_date
                )
                if not start_datetime:
                    continue
                key = self._fsm_schedule_key_for(employee, current_date)
                desired_keys.add(key)
                values = {
                    "name": _("Employee work schedule"),
                    "resource_id": employee.resource_id.id,
                    "role_id": role.id,
                    "start_datetime": fields.Datetime.to_string(start_datetime),
                    "end_datetime": fields.Datetime.to_string(end_datetime),
                    "company_id": employee.company_id.id,
                    "fsm_employee_schedule_generated": True,
                    "fsm_schedule_date": current_date,
                    "fsm_schedule_key": key,
                    "fsm_schedule_calendar_id": employee.resource_calendar_id.id,
                }
                slot = existing_by_key.get(key)
                if slot:
                    if slot.state == "published":
                        if not slot.fsm_team_id and seed_team_by_employee.get(employee.id):
                            slot.write({
                                "fsm_team_id": seed_team_by_employee[employee.id].id,
                            })
                        protected |= slot
                    else:
                        if not slot.fsm_team_id and seed_team_by_employee.get(employee.id):
                            values["fsm_team_id"] = seed_team_by_employee[employee.id].id
                        slot.write(values)
                        updated |= slot
                else:
                    if seed_team_by_employee.get(employee.id):
                        values["fsm_team_id"] = seed_team_by_employee[employee.id].id
                    created |= self.sudo().create(values)
            current_date += timedelta(days=1)

        stale = existing_slots.filtered(
            lambda slot: slot.state == "draft"
            and slot.fsm_schedule_key not in desired_keys
        )
        removed_count = len(stale)
        stale.unlink()
        return {
            "created": len(created),
            "updated": len(updated),
            "removed": removed_count,
            "protected": len(protected),
            "employees": len(employees),
            "date_start": date_start,
            "date_end": date_end,
        }

    @api.model
    def _cron_sync_fsm_technician_shifts(self):
        return self.sync_fsm_technician_shifts()
