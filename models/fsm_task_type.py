# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class FsmTaskType(models.Model):
    _name = "fsm.task.type"
    _description = "FSM Task Type"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)

    project_id = fields.Many2one("project.project", string="Project", required=True)
    default_stage_id = fields.Many2one("project.task.type", string="Default Stage",
                                       domain="[('project_ids', 'in', project_id)]")
    default_planned_hours = fields.Float(string="Default Planned Hours", default=1.0)
    buffer_before_mins = fields.Integer(string="Buffer Before (min)", default=0)
    buffer_after_mins = fields.Integer(string="Buffer After (min)", default=0)

    tag_ids = fields.Many2many("project.tags", string="Default Tags")

    # Requirements / enforcement
    requires_products = fields.Boolean(default=False)
    requires_serials = fields.Boolean(default=False)
    requires_signature = fields.Boolean(default=False)
    requires_photos = fields.Boolean(default=False)

    # Optional SOP checklist template (simple v1: create subtasks)
    checklist_subtask_names = fields.Text(
        string="Checklist Items (one per line)",
        help="When a task is created from this type, these will be created as subtasks.",
        translate=True,
    )

    capable_team_ids = fields.Many2many(
        "fsm.team",
        "fsm_task_type_fsm_team_rel",
        "fsm_task_type_id",
        "fsm_team_id",
        string="Capable Teams",
    )

    product_category_ids = fields.Many2many(
        "product.category",
        "fsm_task_type_product_category_rel",
        "task_type_id",
        "category_id",
        string="Preferred Product Categories",
        help="When selecting products in the intake wizard, these categories are used as initial filters.",
    )
    preferred_team_ids = fields.Many2many(
        "fsm.team",
        "fsm_task_type_fsm_team_pref_rel",
        "fsm_task_type_id",
        "fsm_team_id",
        string="Preferred Teams",
        help="Teams preferred for this task type. They will be highlighted first when scheduling.",
    )

    @api.constrains("default_planned_hours")
    def _check_hours(self):
        for rec in self:
            if rec.default_planned_hours < 0:
                raise ValidationError(_("Planned hours must be >= 0."))

    @api.constrains("requires_products", "project_id")
    def _check_project_allows_materials(self):
        for rec in self:
            if rec.requires_products and rec.project_id and not getattr(rec.project_id, "allow_materials", True):
                raise ValidationError(_("Project '%s' must allow materials when products are required.") % rec.project_id.display_name)

    def _validate_materials_allowed(self):
        for rec in self:
            if rec.requires_products and rec.project_id and hasattr(rec.project_id, "allow_materials") and not rec.project_id.allow_materials:
                raise ValidationError(_("Project '%s' must allow materials when products are required.") % rec.project_id.display_name)

    @api.model
    def create(self, vals):
        record = super().create(vals)
        record._validate_materials_allowed()
        return record

    def write(self, vals):
        res = super().write(vals)
        self._validate_materials_allowed()
        return res
