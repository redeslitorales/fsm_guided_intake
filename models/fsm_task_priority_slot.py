# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


PRIORITY_SELECTION = [
    ("1", "Flexible"),
    ("2", "Low"),
    ("3", "Normal"),
    ("4", "High"),
    ("5", "Critical"),
]


class FsmWeekday(models.Model):
    _name = "fsm.weekday"
    _description = "Weekday"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Selection(
        [
            ("0", "Monday"),
            ("1", "Tuesday"),
            ("2", "Wednesday"),
            ("3", "Thursday"),
            ("4", "Friday"),
            ("5", "Saturday"),
            ("6", "Sunday"),
        ],
        required=True,
    )
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ("code_unique", "unique(code)", "Each weekday can only be configured once."),
    ]


class FsmTaskPrioritySlot(models.Model):
    _name = "fsm.task.priority.slot"
    _description = "FSM Task Priority Time Window"
    _order = "priority desc, hour_from, sequence, id"

    name = fields.Char(compute="_compute_name", translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    priority = fields.Selection(PRIORITY_SELECTION, required=True, default="3")
    dayofweek = fields.Selection(
        [
            ("0", "Monday"),
            ("1", "Tuesday"),
            ("2", "Wednesday"),
            ("3", "Thursday"),
            ("4", "Friday"),
            ("5", "Saturday"),
            ("6", "Sunday"),
        ],
        string="Day of Week",
        required=True,
        default="0",
    )
    dayofweek_ids = fields.Many2many(
        "fsm.weekday",
        "fsm_task_priority_slot_weekday_rel",
        "slot_id",
        "weekday_id",
        string="Days of Week",
    )
    hour_from = fields.Float(string="Start Time", required=True, default=8.0)
    hour_to = fields.Float(string="End Time", required=True, default=17.0)

    @api.depends("priority", "dayofweek", "dayofweek_ids", "hour_from", "hour_to")
    def _compute_name(self):
        priority_labels = dict(PRIORITY_SELECTION)
        day_labels = dict(self._fields["dayofweek"].selection)
        for rec in self:
            days = ", ".join(rec.dayofweek_ids.mapped("name"))
            if not days:
                days = _(day_labels.get(rec.dayofweek, rec.dayofweek or ""))
            rec.name = _("%(priority)s - %(day)s %(start)s-%(end)s") % {
                "priority": _(priority_labels.get(rec.priority, rec.priority or "")),
                "day": days,
                "start": rec._float_time_label(rec.hour_from),
                "end": rec._float_time_label(rec.hour_to),
            }

    def _float_time_label(self, hour):
        hour = hour or 0.0
        hours = int(hour)
        minutes = int(round((hour - hours) * 60.0))
        if minutes == 60:
            hours += 1
            minutes = 0
        return "%02d:%02d" % (hours, minutes)

    @api.constrains("hour_from", "hour_to")
    def _check_hours(self):
        for rec in self:
            if rec.hour_from < 0 or rec.hour_from >= 24:
                raise ValidationError(_("Start time must be between 00:00 and 23:59."))
            if rec.hour_to <= 0 or rec.hour_to > 24:
                raise ValidationError(_("End time must be between 00:01 and 24:00."))
            if rec.hour_to <= rec.hour_from:
                raise ValidationError(_("End time must be after start time."))

    @api.constrains("dayofweek_ids")
    def _check_days(self):
        for rec in self:
            if not rec.dayofweek_ids:
                raise ValidationError(_("Select at least one day of the week."))

    def _weekday_commands_from_legacy_day(self, dayofweek):
        weekday = self.env["fsm.weekday"].search([("code", "=", dayofweek or "0")], limit=1)
        return [(6, 0, weekday.ids)] if weekday else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("dayofweek_ids"):
                commands = self._weekday_commands_from_legacy_day(vals.get("dayofweek"))
                if commands:
                    vals["dayofweek_ids"] = commands
        return super().create(vals_list)

    @api.model
    def get_windows_for_priority(self, priority):
        if not priority:
            return self.browse()
        return self.search(
            [("active", "=", True), ("priority", "=", priority)],
            order="hour_from, sequence, id",
        )
