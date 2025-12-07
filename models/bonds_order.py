# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BondsOrder ( models.Model ) :
    _name = "sid_bonds.orders"  # según tu tabla
    _description = "Avales"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    # Núcleo / identificación
    name = fields.Char (
        string="Referencia",
        default=lambda self : _ ( "New" ),
        required=False,
        copy=False,
        store=True,
        tracking=True,
    )
    reference = fields.Char (
        string="Referencia (externa)" )  # si quieres mantener tu 'Referencia' de la tabla

    # Chatter base ya lo aporta mail.thread (mensajes, seguidores, etc.)

    # Campos de negocio
    partner_id = fields.Many2one ( "res.partner", string="Cliente",
                                   tracking=True, store=True )
    journal_id = fields.Many2one ( "account.journal", string="Banco",
                                   store=True )
    currency_id = fields.Many2one (
        "res.currency",
        string="Moneda",
        default=lambda self : self.env.company.currency_id.id,
        store=True, tracking=True,
    )
    amount = fields.Monetary ( string="Importe", currency_field="currency_id",
                               store=True, tracking=True )

    issue_date = fields.Date ( string="Fecha Emisión", store=True,
                               tracking=True,
                               )
    due_date = fields.Date ( string="Fecha vencimiento", store=True,
                             tracking=True, )
    is_digital = fields.Boolean ( string="Digital", store=True,
                                  tracking=True, )
    reviewed = fields.Boolean ( string="Revisado", store=True,
                                tracking=True, )
    origin_document = fields.Char (
        string="Documento de Origen", compute="_compute_documento_origen",
    )  # en tu tabla depende de contrato/cliente

    contract_ids = fields.One2many (
        comodel_name="sale.quotations",
        inverse_name="bond_id",
        # <-- NOMBRE DEL CAMPO INVERSO EN sale.quotations
        string="Contrato",
        copy=True,
    )
    order_ids = fields.Many2many (
        "sale.quotations",  # Modelo destino
        "sid_bonds_order_sale_rel",  # Nombre de la tabla rel (m2m)
        "sid_bonds_orders_id",
        # Nombre de la columna que apunta a tu modelo (sid_bonds.orders)
        "sale_quotations_id",
        # Nombre de la columna que apunta al modelo destino (sale.quotations)
        string="Pedidos",
        domain=[],
    )
    # Base imponible pedidos (depende de pedidos/cliente en tu tabla)
    base_pedidos = fields.Monetary (
        string="Base Imponible Pedidos",
        currency_field="currency_id",
        compute="_compute_base_pedidos",
        store=True,
        readonly=True,
        copy=True,
        tracking=True,
    )

    # Documento PDF del aval
    pdf_aval = fields.Binary ( string="PDF Aval", attachment=True, store=True )

    # Estados y tipo (valores inferidos; ajustamos cuando me pases los definitivos)
    state = fields.Selection (
        [
            ("draft", "Borrador"),
            ("pending_bank", "Pendiente Banco"),
            ("sent", "Enviado a cliente"),
            ("receipt", "Recibido cliente"),
            ("solicit_dev", "Solicitada Devolución"),
            ("recovered", "Recuperado"),
            ("solicit_can", "Solicitada Cancelación"),
            ("canceled", "Cancelado"),
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

    class SaleQuotationsBonds ( models.Model ) :
        _inherit = "sale.quotations"      # si ya existe y solo lo amplías
        # ... (resto de campos del modelo)
        bond_id = fields.Many2one (
            comodel_name="sid_bonds.orders",
            string="Aval",
            ondelete="cascade",
            index=True,
        )

    # Cálculos
    @api.depends ( "contract_ids", "partner_id" )
    def _compute_base_pedidos(self) :
        """Suma la base imponible (amount_untaxed) de los contratos vinculados.
        Si quieres filtrar además por cliente, lo dejamos indicado.
        """
        for record in self :
            if record.order_ids and record.partner_id :
                sale_orders = self.env['sale.order'].search ( [
                    ('quotations_id', 'in', record.order_ids.ids),
                    ('partner_id', '=', record.partner_id.id),
                    ('state', '=', 'sale')  # Filtrar solo pedidos confirmados
                ] )
                total_base_imponible = sum (
                    sale_orders.mapped ( 'amount_untaxed' ) )
                record.write ( {
                    'base_pedidos' : total_base_imponible
                } )

    @api.depends ( "contract_ids", "partner_id" )
    def _compute_documento_origen(self) :
        """Detecta los presupuestos vinculados al aval por vía de los contratos.
        """
        for record in self :
            if record.contract_ids and record.partner_id :
                sale_orders = self.env['sale.order'].search ( [
                    ('quotations_id', '=', record.contract_ids.ids),
                    ('partner_id', '=', record.partner_id.id)
                ] )
                references = ", ".join ( sale_orders.mapped ( 'name' ) )
                record.write ( {'origin_document' : references} )

    # Reglas simples y flujo
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

    # Secuencia para name
    @api.model_create_multi
    def create(self, vals_list) :
        records = super ().create ( vals_list )
        for rec in records :
            if rec.name == _ ( "New" ) :
                rec.name = self.env["ir.sequence"].next_by_code (
                    "sid_bonds.orders" ) or _ ( "New" )
        return records

    def unlink(self) :
        for rec in self :
            if rec.state in ("active", "expired") :
                raise UserError (
                    _ ( "No puedes eliminar avales vigentes o vencidos." ) )
        return super ().unlink ()

# x_aval	pdf_aval
# x_banco	journal_id
# x_base_imponible	base_pedidos
# x_cliente	partner_id
# x_contrato	contract_ids
# x_create	issue_date
# x_currency_id	currency_id
# x_date	due_date
# x_estado	state
# x_importe	amount
# x_modo	is_digital
# x_name	referencia
# x_origen	origin_document
# x_pedidos	order_ids
# x_revisado	reviewed
# x_tipo	aval_type
