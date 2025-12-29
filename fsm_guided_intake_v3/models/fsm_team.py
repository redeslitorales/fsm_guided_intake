# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class FsmTeam(models.Model):
    _name = "fsm.team"
    _description = "FSM Team"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    member_ids = fields.Many2many("hr.employee", string="Technicians")
    vehicle_ids = fields.Many2many("fleet.vehicle", string="Vehicles")

    calendar_id = fields.Many2one("resource.calendar", string="Working Calendar")
    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse")

    capable_project_ids = fields.Many2many("project.project", string="Capable Projects")
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
    _order = "team_id, weekday, start_time"

    team_id = fields.Many2one("fsm.team", required=True, ondelete="cascade")
    name = fields.Char(required=True)

    weekday = fields.Selection([
        ("0", "Monday"),
        ("1", "Tuesday"),
        ("2", "Wednesday"),
        ("3", "Thursday"),
        ("4", "Friday"),
        ("5", "Saturday"),
        ("6", "Sunday"),
    ], required=True, default="0")

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
