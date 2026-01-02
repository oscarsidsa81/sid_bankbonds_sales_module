# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BondsOrder ( models.Model ) :
    _name = "sid_bonds_orders"
    _description = "Avales"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    def action_view_sale_orders(self):
        bonds = self.filtered(lambda b: b.partner_id and b.order_ids)
        if not bonds:
            return self.env.ref("sale.action_orders").read()[0]

        quotation_ids = bonds.mapped("order_ids").ids
        partner_ids = bonds.mapped("partner_id").ids

        domain = [
            ("quotations_id", "in", quotation_ids),
            ("state", "!=", "cancel"),
            ("partner_id", "in", partner_ids),
        ]

        action = self.env.ref("sale.action_orders").read()[0]
        action["domain"] = domain
        action["context"] = dict(self.env.context)
        return action

    name = fields.Char (
        string="Referencia",
        default=lambda self : _ ( "New" ),
        copy=False,
        store=True,
        tracking=True,
    )
    reference = fields.Char ( string="Referencia (externa)" )

    partner_id = fields.Many2one ( "res.partner", string="Cliente",
                                   tracking=True, store=True )
    journal_id = fields.Many2one ( "account.journal", string="Banco",
                                   store=True )
    currency_id = fields.Many2one (
        "res.currency",
        string="Moneda",
        default=lambda self : self.env.company.currency_id.id,
        store=True,
        tracking=True,
    )
    amount = fields.Monetary ( string="Importe", currency_field="currency_id",
                               store=True, tracking=True )

    issue_date = fields.Date ( string="Fecha Emisión", store=True,
                               tracking=True )
    due_date = fields.Date ( string="Fecha vencimiento", store=True,
                             tracking=True )
    is_digital = fields.Boolean ( string="Digital", store=True, tracking=True )
    reviewed = fields.Boolean ( string="Revisado", store=True, tracking=True )

    origin_document = fields.Char (
        string="Documento de Origen",
        compute="_compute_documento_origen",
        store=True,
    )

    contract_ids = fields.One2many (
        comodel_name="sale.quotations",
        inverse_name="bond_id",
        string="Contrato",
        copy=True,
    )

    order_ids = fields.Many2many (
        "sale.quotations",
        "sid_bonds_order_sale_rel",
        "sid_bonds_orders_id",
        "sale_quotations_id",
        string="Pedidos",
    )

    base_pedidos = fields.Monetary (
        string="Base Imponible Pedidos",
        currency_field="currency_id",
        compute="_compute_base_pedidos",
        store=False,
        readonly=True,
        copy=True,
        tracking=True,
    )

    pdf_aval = fields.Binary ( string="PDF Aval", attachment=True, store=True )

    state = fields.Selection (
        [
            ("draft", "Borrador"),
            ("requested", "Solicitado"),
            ("active", "Vigente"),
            ("expired", "Vencido"),
            ("cancelled", "Cancelado"),
            ("pending_bank", "Pendiente Banco"),
            ("sent", "Enviado a cliente"),
            ("receipt", "Recibido cliente"),
            ("solicit_dev", "Solicitada Devolución"),
            ("recovered", "Recuperado"),
            ("solicit_can", "Solicitada Cancelación"),
        ],
        string="Estado",
        default="draft",
        store=True,
        tracking=True,
    )

    aval_type = fields.Selection (
        [
            ("adelanto", "Provisional"),
            ("fiel_gar", "Fiel Cumplimiento y Garantía"),
            ("fiel", "Fiel Cumplimiento"),
            ("gar", "Garantía"),
        ],
        string="Tipo",
        tracking=True,
    )

    description = fields.Text ( string="Descripción / Notas" )


    # Con esta computación podemos tener el amount_untaxed de los pedidos confirmados que estén relacionados con el valor de quotations_id
    @api.depends (
        "order_ids",
        "partner_id",
        "order_ids.sale_order_ids.amount_untaxed",
        "order_ids.sale_order_ids.state",
        "order_ids.sale_order_ids.partner_id",
    )

    def _compute_base_pedidos(self) :
        for record in self :
            total = 0.0
            if not record.order_ids or not record.partner_id :
                record.base_pedidos = 0.0
                continue

            for q in record.order_ids :
                # 1) Si hay sale.order ligados a la quotation, úsalo
                so_list = getattr ( q, "sale_order_ids",
                                    self.env["sale.order"] )
                if so_list :
                    so_list = so_list.filtered ( lambda
                                                     so : so.partner_id.id == record.partner_id.id and so.state != "cancel" )
                    total += sum ( so_list.mapped ( "amount_untaxed" ) )
                    continue

                # 2) Si NO hay sale.order, usa la propia quotation (si tiene campos)
                if hasattr ( q, "amount_untaxed" ) :
                    total += q.amount_untaxed
                elif hasattr ( q, "amount_total" ) :
                    total += q.amount_total

            record.base_pedidos = total

    @api.depends ( "contract_ids", "partner_id" )
    def _compute_documento_origen(self) :
        for record in self :
            if record.contract_ids and record.partner_id :
                sale_orders = self.env["sale.order"].search ( [
                    ("quotations_id", "in", record.contract_ids.ids),
                    ("partner_id", "=", record.partner_id.id),
                ] )
                record.origin_document = ", ".join (
                    sale_orders.mapped ( "name" ) )
            else :
                record.origin_document = False

    def action_request(self) :
        for rec in self :
            if rec.state != "draft" :
                raise UserError (
                    _ ( "Solo puedes solicitar desde Borrador." ) )
            rec.state = "requested"

    def action_activate(self) :
        for rec in self :
            if rec.state not in ("requested", "draft") :
                raise UserError (
                    _ ( "Solo puedes poner Vigente desde Solicitado o Borrador." ) )
            if not rec.amount or rec.amount <= 0 :
                raise UserError ( _ ( "El importe debe ser positivo." ) )
            rec.state = "active"

    def action_expire(self) :
        for rec in self :
            if rec.state != "active" :
                raise UserError ( _ ( "Solo puedes vencer un aval vigente." ) )
            rec.state = "expired"

    def action_cancel(self) :
        for rec in self :
            if rec.state in ("expired", "cancelled") :
                continue
            rec.state = "cancelled"

    def action_set_draft(self) :
        for rec in self :
            rec.state = "draft"

    @api.model_create_multi
    def create(self, vals_list) :
        records = super ().create ( vals_list )
        for rec in records :
            if rec.name == _ ( "New" ) :
                rec.name = self.env["ir.sequence"].next_by_code (
                    "sid_bonds_orders" ) or _ ( "New" )
        return records

    def unlink(self) :
        for rec in self :
            if rec.state in ("active", "expired") :
                raise UserError (
                    _ ( "No puedes eliminar avales vigentes o vencidos." ) )
        return super ().unlink ()


class SaleQuotationsBonds(models.Model):
    _inherit = "sale.quotations"

    bond_id = fields.Many2one(
        comodel_name="sid_bonds_orders",
        string="Aval",
        ondelete="cascade",
        index=True,
    )

    sale_order_ids = fields.One2many(
        comodel_name="sale.order",
        inverse_name="quotations_id",
        string="Pedidos (Sale Orders)",
    )
