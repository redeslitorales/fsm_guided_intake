# -*- coding: utf-8 -*-
from odoo import fields, models

class ProductTemplate(models.Model):
    _inherit = "product.template"

    fsm_bill_from_task = fields.Boolean(
        string="Bill from Field Service Task",
        default=True,
        help="If enabled, this product will be added to Sales Orders/Invoices generated from field service tasks."
    )

class ProductProduct(models.Model):
    _inherit = "product.product"

    fsm_bill_from_task = fields.Boolean(related="product_tmpl_id.fsm_bill_from_task", readonly=False)
