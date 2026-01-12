# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BondsOrder ( models.Model ) :
    _name = "sid_bonds_orders"
    _description = "Avales"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    def write(self, vals) :
        res = super ().write ( vals )

        if "reference" in vals :
            for rec in self :
                if rec.reference :
                    rec.name = rec.reference

        return res

    def _compute_order_ids(self) :
        for bond in self :
            bond.order_ids = bond.contract_ids

    def _inverse_order_ids(self) :
        for bond in self :
            new_quots = bond.order_ids
            old_quots = bond.contract_ids

            # añadir: las nuevas
            (new_quots - old_quots).write ( {"bond_id" : bond.id} )
            # quitar: las que ya no estén
            (old_quots - new_quots).write ( {"bond_id" : False} )

    def action_view_sale_orders(self) :
        bonds = self.filtered ( lambda b : b.partner_id and b.contract_ids )
        if not bonds :
            return self.env.ref ( "sale.action_orders" ).read ()[0]

        quotation_ids = bonds.mapped ( "contract_ids" ).ids
        partner_ids = bonds.mapped ( "partner_id" ).ids

        domain = [
            ("quotations_id", "in", quotation_ids),
            ("state", "=", "sale"),
            ("partner_id", "in", partner_ids),
        ]

        action = self.env.ref ( "sale.action_orders" ).read ()[0]
        action["domain"] = domain
        action["context"] = dict ( self.env.context )
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

    contract_ids = fields.Many2many (
        comodel_name="sale.quotations",
        relation="sid_bonds_quotation_rel",
        column1="bond_id",
        column2="quotation_id",
        string="Contratos",
        copy=False,
        tracking=True,
    )

    order_ids = fields.Many2many (
        comodel_name="sale.quotations",
        string="Pedidos",
        compute="_compute_order_ids",
        inverse="_inverse_order_ids",
        store=False,
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
        "contract_ids",
        "order_ids",
        "partner_id",
        "contract_ids.sale_order_ids.amount_untaxed",
        "contract_ids.sale_order_ids.state",
        "contract_ids.sale_order_ids.partner_id",
    )
    def _compute_base_pedidos(self) :
        for bond in self :
            if not bond.contract_ids or not bond.partner_id :
                bond.base_pedidos = 0.0
                continue

            orders = bond.contract_ids.mapped ( "sale_order_ids" ).filtered (
                lambda
                    so : so.partner_id.id == bond.partner_id.id and so.state == "sale"
            )
            bond.base_pedidos = sum ( orders.mapped ( "amount_untaxed" ) )

    @api.depends ( "contract_ids", "partner_id" )
    def _compute_documento_origen(self) :
        for record in self :
            if record.contract_ids and record.partner_id :
                sale_orders = self.env["sale.order"].search ( [
                    ("quotations_id", "in", record.contract_ids.ids),
                    ("partner_id", "=", record.partner_id.id),
                    ("state", "=", "sale"),
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
    _description = "Contratos/Pedidos"
    _parent_store = True  # activa parent_path

    parent_id = fields.Many2one(
        comodel_name="sale.quotations",
        string="Contrato Principal",
        index=True,
        ondelete="restrict",
    )

    child_ids = fields.One2many(
        comodel_name="sale.quotations",
        inverse_name="parent_id",
        string="Adendas",
    )

    parent_path = fields.Char(index=True)

    # # TODO aquí es posible que necesitemos Many2many, al final puede haber
    #
    # bond_id = fields.Many2one(
    #     comodel_name="sid_bonds_orders",
    #     string="Aval",
    #     ondelete="set null",
    #     index=True,
    # )

    # # varios avales para un solo contrato o varios
    bond_ids = fields.Many2many (
        comodel_name="sid_bonds_orders",
        relation="sid_bonds_quotation_rel",
        column1="quotation_id",
        column2="bond_id",
        string="Avales",
    )

    sale_order_ids = fields.One2many(
        comodel_name="sale.order",
        inverse_name="quotations_id",
        string="Pedidos (Sale Orders)",
    )
