# -*- coding: utf-8 -*-
{
    "name": "sid_bankbonds_sales_module",
    "summary": "Gestión de avales con contratos vinculados, estados y chatter",
    "version": "15.0.1.0.0",
    "author": "oscarsidsa81",
    "website": "https://sid-sa.com",
    "category": "Accounting/Finance",
    "license": "AGPL-3",
    "depends": ["base", "mail", "account", "sale","documents"],  # sale por sale.order; account por account.journal
    "data": [
        "data/automation.xml",
        "data/folders.xml",
        "views/bonds_views.xml",
    ],
    'installable' : True,
    'auto_install' : False,
    'application' : False,
}