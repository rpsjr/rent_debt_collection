# Copyright 2023 Babur Ltda.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    'name': 'Rent Debt Collection',
    'description': """
        Rental tenant debt collection actions""",
    'version': '13.0.1.2.0',
    'license': 'AGPL-3',
    'author': 'Babur Ltda.',
    'website': 'babur.com.br',
    'depends': [
        'base',
        'account',
        'fleet',
        'sms',
        'meta_whatsapp',
        'mail_broker_whatsapp',
        'payment_boletointer',
    ],
    'data': [
        'views/fleet_settings.xml',
        'views/account_move.xml',
        "data/sms_data.xml",
        "data/whatsapp_data.xml",
        "data/rent_debt_collection_cron.xml",
        "security/ir.model.access.csv",
        "security/sms_security.xml",
    ],
    'application': True,
}
