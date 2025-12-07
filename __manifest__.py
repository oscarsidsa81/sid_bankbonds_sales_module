# -*- coding: utf-8 -*-
{
    "name": "SID – Avales (sid_bonds.orders)",
    "summary": "Gestión de avales con contratos vinculados, estados y chatter",
    "version": "15.0.1.0.0",
    "author": "oscarsidsa81",
    "website": "https://sid-sa.com",
    "category": "Accounting/Finance",
    "license": "AGPL-3",
    "depends": ["base", "mail", "account", "sale","documents"],  # sale por sale.order; account por account.journal
    "data": [
        "data/automation.xml",
        "views/bonds_order_views.xml",
    ],
    'installable' : True,
    'auto_install' : False,
    'application' : False,
}