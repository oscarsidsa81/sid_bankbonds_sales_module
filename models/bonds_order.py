# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BondsOrder ( models.Model ) :
    _name = "sid_bonds_orders"
    _description = "Avales"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    _BOND_STATES_SKIP_NOTIFY = {"expired", "solicit_dev", "recovered", "solicit_can", "cancelled"}

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
        "sale.quotations",
        relation="sid_bonds_quotation_rel",
        column1="bond_id",
        column2="quotation_id",
        string="Contratos / Pedidos",
        tracking=True,
        copy=False,
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
            ("pending_bank", "Pendiente Banco"),
            ("requested", "Solicitado"),
            ("sent", "Enviado a cliente"),
            ("receipt", "Recibido cliente"),
            ("active", "Vigente"),
            ("expired", "Vencido"),
            ("solicit_dev", "Solicitada Devolución"),
            ("recovered", "Recuperado"),
            ("solicit_can", "Solicitada Cancelación"),
            ("cancelled", "Cancelado"),
        ],
        string="Estado",
        default="draft",
        store=True,
        tracking=True,
    )

    aval_type = fields.Selection (
        [
            ("prov", "Provisional"),
            ("adel", "Adelanto"),
            ("fiel", "Fiel Cumplimiento"),
            ("gar", "Garantía"),
            ("fiel_gar", "Fiel Cumplimiento y Garantía"),
        ],
        string="Tipo",
        tracking=True,
    )

    description = fields.Text ( string="Descripción / Notas" )

    def write(self, vals):
        # 1) Guardamos el valor anterior (calculado) antes del write
        # OJO: base_pedidos es compute store=False => se calcula al acceder
        old_map = {b.id: b.base_pedidos for b in self}

        # 2) write normal (y tu lógica de name/reference si la mantienes)
        if "reference" in vals and vals.get("reference"):
            vals = dict(vals)
            vals["name"] = vals["reference"]

        res = super().write(vals)

        # 3) Si el write afecta a algo que pueda cambiar la base, evaluamos después
        # Esto evita spam si editas campos no relacionados.
        triggers = {"contract_ids","base_pedidos", "partner_id"}  # si quieres, añade aquí otras cosas
        if triggers.intersection(vals.keys()):
            self._post_base_pedidos_variation_note(old_map)

        return res


    def _schedule_creator_todo(self, old_value, new_value, pct):
        """
        Activity tipo 'Por hacer' para create_uid (si existe).
        Evita duplicados abiertos con el mismo resumen.
        """
        self.ensure_one()
        if not self.create_uid:
            return

        # tipo de actividad 'Por hacer' estándar
        todo_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not todo_type:
            return

        summary = _("Revisar necesidad de ampliar aval")
        note = _(
            "Se detectó variación > 3%% en Base Imponible Pedidos.\n"
            "Anterior: %(old)s\nNuevo: %(new)s\nCambio: %(pct).2f%%\n\n"
            "Revisar si es necesario ampliar el aval o avales asociados."
        ) % {"old": old_value, "new": new_value, "pct": pct}

        # evita spam: si ya hay una activity abierta igual, no crear otra
        existing = self.env["mail.activity"].search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("user_id", "=", self.create_uid.id),
            ("activity_type_id", "=", todo_type.id),
            ("summary", "=", summary),
            ("state", "=", "planned"),
        ], limit=1)
        if existing:
            return

        deadline = fields.Date.context_today(self)  # hoy
        self.activity_schedule(
            activity_type_id=todo_type.id,
            user_id=self.create_uid.id,
            summary=summary,
            note=note,
            date_deadline=deadline,
        )


    def _get_bonds_manager_partners(self):
        """Devuelve res.partner (partners) de usuarios del grupo de Gestión de Avales."""
        group = self.env.ref("sid_bankbonds_sales_module.group_bonds_manager", raise_if_not_found=False)
        if not group:
            return self.env["res.partner"]
        users = group.users
        return users.mapped("partner_id")

    def _post_base_pedidos_variation_note(self, old_map):
        """
        old_map: {bond_id: old_base_pedidos}
        Si variación > 3% (contra valor anterior) y estado permitido:
          - publica nota interna mencionando a usuarios del grupo
          - crea activity tipo Por hacer para create_uid
        """
        partners = self._get_bonds_manager_partners()

        for bond in self:
            # 1) estados excluidos
            if bond.state in self._BOND_STATES_SKIP_NOTIFY:
                continue

            old = float(old_map.get(bond.id, 0.0) or 0.0)
            new = float(bond.base_pedidos or 0.0)

            # si ambos 0, nada
            if old == 0.0 and new == 0.0:
                continue

            # 2) % contra valor anterior (si old == 0, no se puede dividir)
            if old == 0.0:
                # Si quieres que 0 -> algo dispare siempre, deja esto así:
                pct = 100.0
                changed = (new != 0.0)
            else:
                pct = abs(new - old) / abs(old) * 100.0
                changed = pct > 3.0

            if not changed:
                continue

            # 3) menciones HTML (solo si hay partners)
            mentions_html = ""
            if partners:
                mentions_html = " ".join(
                    f'<a data-oe-model="res.partner" data-oe-id="{p.id}">@{p.display_name}</a>'
                    for p in partners
                )

            body = _(
                "<p><b>Variación en Base Imponible Pedidos</b> (&gt; 3%%)</p>"
                "<p>Anterior: %(old)s<br/>Nuevo: %(new)s<br/>Cambio: %(pct).2f%%</p>"
                "%(mentions)s"
            ) % {
                "old": old,
                "new": new,
                "pct": pct,
                "mentions": f"<p>{mentions_html}</p>" if mentions_html else "",
            }

            # 4) Nota interna. Además, notifica a esos partners (opcional pero útil)
            bond.message_post(
                body=body,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
                partner_ids=partners.ids if partners else None,
            )

            # 5) Activity al creador
            bond._schedule_creator_todo(old, new, pct)

    def action_view_sale_orders(self) :
        bonds = self.filtered ( lambda b : b.contract_ids )
        action = self.env.ref ( "sale.action_orders" ).read ()[0]
        if not bonds :
            return action

        quotation_ids = bonds.mapped ( "contract_ids" ).ids
        action["domain"] = [
            ("quotations_id", "in", quotation_ids),
            ("state", "=", "sale"),
        ]
        action["context"] = dict ( self.env.context )
        return action

    # Con esta computación podemos tener el amount_untaxed de los pedidos confirmados que estén relacionados con el valor de quotations_id
    @api.depends (
        "contract_ids",
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


class SaleQuotationsBonds ( models.Model ) :
    _inherit = "sale.quotations"
    _description = "Contratos/Pedidos"
    _parent_store = True  # activa parent_path

    parent_id = fields.Many2one (
        comodel_name="sale.quotations",
        string="Contrato Principal",
        index=True,
        ondelete="restrict",
    )

    child_ids = fields.One2many (
        comodel_name="sale.quotations",
        inverse_name="parent_id",
        string="Adendas",
    )

    parent_path = fields.Char ( index=True )

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

    sale_order_sale_ids = fields.Many2many (
        "sale.order",
        compute="_compute_sale_order_sale_ids",
        string="Pedidos Confirmados",
        store=False,
    )

    @api.depends ( "sale_order_ids.state", "sale_order_ids.quotations_id" )
    def _compute_sale_order_sale_ids(self) :
        for rec in self :
            rec.sale_order_sale_ids = rec.sale_order_ids.filtered (
                lambda so : so.state == "sale" )

