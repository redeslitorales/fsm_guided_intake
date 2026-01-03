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
    never_has_product = fields.Boolean(
        string="Never Has Product",
        help="Skip the Products step in the intake wizard for this task type.",
        default=False,
    )
    is_client_task = fields.Boolean(
        string="Client Task",
        help="Show this task type only when a customer/subscription is selected in the wizard.",
        default=False,
    )
    may_be_rescheduled = fields.Boolean(
        string="May Be Rescheduled",
        help="Indicates this task type can be rescheduled without extra approval.",
        default=True,
    )
    include_in_wizard = fields.Boolean(
        string="Include in Wizard",
        help="If unchecked, this task type will not be offered in the intake wizard.",
        default=True,
    )

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
    subscription_category_ids = fields.Many2many(
        "product.category",
        "fsm_task_type_sub_product_category_rel",
        "task_type_id",
        "category_id",
        string="Subscription Product Categories",
        help="Only subscriptions containing products in these categories will be offered in the intake wizard.",
    )
    preferred_team_ids = fields.Many2many(
        "fsm.team",
        "fsm_task_type_fsm_team_pref_rel",
        "fsm_task_type_id",
        "fsm_team_id",
        string="Preferred Teams",
        help="Teams preferred for this task type. They will be highlighted first when scheduling.",
    )

    # Install validation (fiber)
    enforce_install_validation = fields.Boolean(
        string="Enforce Install Validation",
        help="Block closing tasks unless install worksheet is complete and optical levels are in range."
    )
    requires_fiber_install = fields.Boolean(
        string="Requires Fiber Install",
        help="Show fiber install worksheet on tasks of this type.",
        default=False,
    )
    default_pon_type = fields.Selection(
        [("gpon", "GPON"), ("xgspon", "XGS-PON")],
        string="Default PON Type",
    )
    optics_rx_min = fields.Float(string="RX Min (dBm)", default=-27.0, digits=(16, 2))
    optics_rx_max = fields.Float(string="RX Max (dBm)", default=-8.0, digits=(16, 2))
    optics_tx_min = fields.Float(string="TX Min (dBm)", default=0.5, digits=(16, 2))
    optics_tx_max = fields.Float(string="TX Max (dBm)", default=5.0, digits=(16, 2))

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
