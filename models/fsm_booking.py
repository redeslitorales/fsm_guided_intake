# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta

class FsmBooking(models.Model):
    _name = "fsm.booking"
    _description = "FSM Booking"
    _order = "start_datetime desc"

    name = fields.Char(default=lambda self: self.env["ir.sequence"].next_by_code("fsm.booking"), readonly=True)
    task_id = fields.Many2one("project.task", required=True, ondelete="cascade")
    team_id = fields.Many2one("fsm.team", required=True, ondelete="restrict")

    start_datetime = fields.Datetime(required=True)
    end_datetime = fields.Datetime(required=True)
    allocated_hours = fields.Float(required=True)

    state = fields.Selection([
        ("tentative", "Tentative"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
    ], default="confirmed", required=True)

    picking_id = fields.Many2one("stock.picking", string="Delivery Order", readonly=True, copy=False)

    def action_cancel(self):
        for rec in self:
            rec.state = "cancelled"
            # NOTE: we do not cancel pickings automatically in v1/v3 to avoid data-loss surprises.

    def _create_delivery_and_reserve(self):
        """Create an outgoing picking for the task materials and reserve stock (action_assign).
        Assumptions:
          - warehouse is taken from team.warehouse_id
          - destination is customer stock location
          - services are ignored for stock picking
          - serial tracking uses material.lot_ids (many2many)
          - lot tracking uses material.lot_id (many2one)
        """
        self.ensure_one()
        task = self.task_id
        team = self.team_id

        if not team.warehouse_id:
            raise UserError(_("Team '%s' has no warehouse set.") % team.name)

        picking_type = team.get_default_picking_type_out()
        if not picking_type:
            raise UserError(_("No outgoing picking type found."))

        customer = task.partner_id
        if not customer:
            raise UserError(_("Task has no customer; cannot create delivery order."))

        src_loc = team.warehouse_id.lot_stock_id
        dest_loc = customer.property_stock_customer

        materials = task.fsm_material_ids.filtered(
            lambda l: l.product_id.type in ("product", "consu") and l.product_uom_qty > 0
        )
        if not materials:
            return False

        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": src_loc.id,
            "location_dest_id": dest_loc.id,
            "partner_id": customer.id,
            "origin": task.display_name,
        })

        moves = []
        for line in materials:
            moves.append((0, 0, {
                "name": line.product_id.display_name,
                "product_id": line.product_id.id,
                "product_uom": line.product_uom.id,
                "product_uom_qty": line.product_uom_qty,
                "location_id": src_loc.id,
                "location_dest_id": dest_loc.id,
            }))
        picking.move_ids_without_package = moves

        picking.action_confirm()
        picking.action_assign()

        # Apply selected serial/lot numbers as reservations where provided.
        for line in materials:
            move = picking.move_ids_without_package.filtered(lambda m: m.product_id == line.product_id)[:1]
            if not move:
                continue
            tracking = move.product_id.tracking
            if tracking == "serial":
                lots = line.lot_ids
                if lots:
                    for lot in lots:
                        self.env["stock.move.line"].create({
                            "picking_id": picking.id,
                            "move_id": move.id,
                            "product_id": move.product_id.id,
                            "product_uom_id": move.product_uom.id,
                            "location_id": src_loc.id,
                            "location_dest_id": dest_loc.id,
                            "lot_id": lot.id,
                            "quantity": 1.0,
                        })
            elif tracking == "lot":
                if line.lot_id:
                    self.env["stock.move.line"].create({
                        "picking_id": picking.id,
                        "move_id": move.id,
                        "product_id": move.product_id.id,
                        "product_uom_id": move.product_uom.id,
                        "location_id": src_loc.id,
                        "location_dest_id": dest_loc.id,
                        "lot_id": line.lot_id.id,
                        "quantity": min(line.product_uom_qty, move.product_uom_qty),
                    })

        self.picking_id = picking.id
        return picking

    def action_create_or_update_delivery(self):
        for rec in self.filtered(lambda b: b.state == "confirmed"):
            if not rec.picking_id:
                rec._create_delivery_and_reserve()
