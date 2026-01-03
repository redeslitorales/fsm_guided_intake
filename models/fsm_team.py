# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class FsmTeam(models.Model):
    _name = "fsm.team"
    _description = "FSM Team"
    _order = "name"

    name = fields.Char(compute="_compute_name", store=True, required=True)

    @api.depends('lead_user_id')
    def _compute_name(self):
        for rec in self:
            if rec.lead_user_id:
                rec.name = _('Team Leader: %s') % (rec.lead_user_id.name or '')
            else:
                # fallback to original name if needed (could use an extra field if you want editable base name)
                rec.name = _('Team (no leader)')
# ...existing code...
    active = fields.Boolean(default=True)

    member_ids = fields.Many2many("hr.employee", string="Technicians")
    vehicle_ids = fields.Many2many("fleet.vehicle", string="Vehicles")
    lead_user_id = fields.Many2one("res.users", string="Team Lead")

    calendar_id = fields.Many2one("resource.calendar", string="Working Calendar")
    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse")

    capable_project_ids = fields.Many2many("project.project", string="Capable Projects")
    capable_task_type_ids = fields.Many2many(
        "fsm.task.type",
        "fsm_task_type_fsm_team_rel",
        "fsm_team_id",
        "fsm_task_type_id",
        string="Capable Task Types",
    )
    shift_ids = fields.One2many("fsm.team.shift", "team_id", string="Shifts")

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
