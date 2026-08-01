# -*- coding: utf-8 -*-
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class FsmDayReservation(models.Model):
    _name = "fsm.day.reservation"
    _description = "FSM Day Reservation"
    _order = "service_date desc, id desc"

    task_id = fields.Many2one("project.task", required=True, ondelete="cascade")
    service_date = fields.Date(required=True)
    task_type_id = fields.Many2one("fsm.task.type", string="Task Type", required=True, ondelete="restrict")
    required_minutes = fields.Integer(string="Required Minutes", default=0)
    capacity_bucket = fields.Selection(
        [
            ("L1", "Basic (L1)"),
            ("L2", "Standard (L2)"),
            ("L3", "Fiber (L3)"),
        ],
        string="Capacity Bucket",
        required=True,
    )
    zone = fields.Char(string="Zone")
    priority = fields.Selection(
        [
            ("1", "Flexible"),
            ("2", "Low"),
            ("3", "Normal"),
            ("4", "High"),
            ("5", "Critical"),
        ],
        string="Priority",
    )
    assigned_team_id = fields.Many2one("fsm.team", string="Assigned Team", ondelete="set null")
    assigned_start_date = fields.Date(
        string="Start Date",
        compute="_compute_assigned_parts",
        inverse="_inverse_assigned_start_parts",
        store=False,
    )
    assigned_start_time = fields.Float(
        string="Start Time",
        compute="_compute_assigned_parts",
        inverse="_inverse_assigned_start_parts",
        store=False,
    )
    assigned_end_date = fields.Date(
        string="End Date",
        compute="_compute_assigned_parts",
        inverse="_inverse_assigned_end_parts",
        store=False,
    )
    assigned_end_time = fields.Float(
        string="End Time",
        compute="_compute_assigned_parts",
        inverse="_inverse_assigned_end_parts",
        store=False,
    )
    assigned_start_datetime = fields.Datetime(string="Assigned Start")
    assigned_end_datetime = fields.Datetime(string="Assigned End")
    assigned_end_preview = fields.Datetime(
        string="Calculated End",
        compute="_compute_assigned_end_preview",
        store=False,
    )
    dispatch_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("finalized", "Finalized"),
            ("cancelled", "Cancelled"),
        ],
        string="Dispatch State",
        default="pending",
        required=True,
    )

    @api.constrains("required_minutes")
    def _check_required_minutes(self):
        for rec in self:
            if rec.required_minutes is not None and rec.required_minutes < 0:
                raise ValidationError(_("Required minutes cannot be negative."))

    def _combine_local_parts_to_utc(self, date_value, time_float):
        if not date_value:
            return False
        time_float = time_float or 0.0
        hour = int(time_float)
        minute = int(round((time_float - hour) * 60))
        if minute >= 60:
            hour += 1
            minute -= 60
        hour = hour % 24
        local_naive = datetime.combine(date_value, time(hour=hour, minute=minute))
        tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        tz = pytz.timezone(tz_name)
        local_aware = tz.localize(local_naive)
        utc_aware = local_aware.astimezone(pytz.UTC)
        return utc_aware.replace(tzinfo=None)

    @api.depends("assigned_start_datetime", "assigned_end_datetime")
    def _compute_assigned_parts(self):
        for rec in self:
            if rec.assigned_start_datetime:
                start_local = fields.Datetime.context_timestamp(rec, rec.assigned_start_datetime)
                rec.assigned_start_date = start_local.date()
                rec.assigned_start_time = start_local.hour + (start_local.minute / 60.0)
            else:
                rec.assigned_start_date = False
                rec.assigned_start_time = 0.0

            if rec.assigned_end_datetime:
                end_local = fields.Datetime.context_timestamp(rec, rec.assigned_end_datetime)
                rec.assigned_end_date = end_local.date()
                rec.assigned_end_time = end_local.hour + (end_local.minute / 60.0)
            else:
                rec.assigned_end_date = False
                rec.assigned_end_time = 0.0

    def _inverse_assigned_start_parts(self):
        for rec in self:
            if rec.assigned_start_date:
                rec.assigned_start_datetime = rec._combine_local_parts_to_utc(rec.assigned_start_date, rec.assigned_start_time)
            else:
                rec.assigned_start_datetime = False

    def _inverse_assigned_end_parts(self):
        for rec in self:
            if rec.assigned_end_date:
                rec.assigned_end_datetime = rec._combine_local_parts_to_utc(rec.assigned_end_date, rec.assigned_end_time)
            else:
                rec.assigned_end_datetime = False

    @api.depends("assigned_start_datetime", "assigned_end_datetime", "required_minutes")
    def _compute_assigned_end_preview(self):
        for rec in self:
            if rec.assigned_end_datetime:
                rec.assigned_end_preview = rec.assigned_end_datetime
            elif rec.assigned_start_datetime and (rec.required_minutes or 0) > 0:
                rec.assigned_end_preview = rec.assigned_start_datetime + timedelta(minutes=rec.required_minutes)
            else:
                rec.assigned_end_preview = False

    @api.onchange("assigned_start_datetime", "required_minutes")
    def _onchange_assigned_start_required_minutes(self):
        for rec in self:
            minutes = rec.required_minutes or 0
            if rec.assigned_start_datetime and minutes > 0:
                if not rec.assigned_end_datetime or rec.assigned_end_datetime <= rec.assigned_start_datetime:
                    rec.assigned_end_datetime = rec.assigned_start_datetime + timedelta(minutes=minutes)

    @api.onchange("assigned_start_date", "assigned_start_time", "required_minutes")
    def _onchange_start_parts_required_minutes(self):
        for rec in self:
            minutes = rec.required_minutes or 0
            if rec.assigned_start_date and minutes > 0:
                has_end = bool(rec.assigned_end_date)
                if not has_end:
                    start_dt = rec._combine_local_parts_to_utc(rec.assigned_start_date, rec.assigned_start_time)
                    if start_dt:
                        rec.assigned_end_datetime = start_dt + timedelta(minutes=minutes)

    def action_finalize_dispatch(self):
        for rec in self:
            if rec.dispatch_state == "cancelled":
                raise ValidationError(_("Cannot finalize a cancelled reservation."))

            team = rec.assigned_team_id or rec.task_id.team_id
            start_dt = rec.assigned_start_datetime
            end_dt = rec.assigned_end_datetime
            if start_dt and not end_dt and (rec.required_minutes or 0) > 0:
                end_dt = start_dt + timedelta(minutes=rec.required_minutes)
                rec.assigned_end_datetime = end_dt
            if not team or not start_dt or not end_dt:
                raise ValidationError(_("Assign a team and start/end before finalizing dispatch."))
            if end_dt <= start_dt:
                raise ValidationError(_("End time must be after start time."))

            duration_hours = (end_dt - start_dt).total_seconds() / 3600.0
            required_minutes = rec.required_minutes or int(round(duration_hours * 60))
            if required_minutes <= 0:
                raise ValidationError(_("Required minutes must be positive to finalize dispatch."))

            # Get team member users for assignment
            member_users = []
            if team.member_ids:
                member_users = team.member_ids.mapped("user_id").filtered(lambda u: u).ids

            ctx = dict(self.env.context)
            ctx.pop("default_state", None)
            ctx.pop("state", None)

            # Use the task's own scheduling helper — handles all date fields,
            # booking creation/update, team, and users in a safe, field-aware way.
            rec.task_id.with_context(ctx)._write_scheduled_datetime(
                start_dt_utc=start_dt,
                end_dt_utc=end_dt,
                duration_hours=duration_hours,
                team=team,
                assignee_user_ids=member_users,
            )

            rec.write({
                "dispatch_state": "finalized",
                "assigned_team_id": team.id,
                "required_minutes": required_minutes,
                "service_date": rec.service_date or fields.Date.to_date(start_dt),
            })

        return True
