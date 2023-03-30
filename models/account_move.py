# -*- coding: utf-8 -*-
# Copyright <2023> <Raimundo Pereira da Silva Junior <raimundopsjr@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from datetime import datetime, timedelta
from odoo import models, fields, api, _
from workalendar.america.brazil import Brazil

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'


    payment_promise = fields.Datetime(string='Payment Promise', help="Date and time of payment promise")

    def _active_payment_promise(self):
        if not self.payment_promise:
            return False
        elif self.payment_promise < datetime.now():
            return False
        return True

    def _create_payment_promise(self):
        payment_promise_date = datetime.now() + timedelta(hours=24)
        self.write({
            'payment_promise': payment_promise_date
        })

    def _batch_block_vehicle_w_invoice_overdue(self):

        _logger.info(f"################ inicio do teste*********")

        invoice_filters = [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "<=",
                    fields.Datetime.now().date() - timedelta(days=1),
                ),
            ]
        for move in self.search(invoice_filters):
            move._block_vehicle_w_invoice_overdue()

    def _block_vehicle_w_invoice_overdue(self):
        data_atual = datetime.now().date()
        # Define o calendário de feriados no Brasil
        feriados = Brazil().holidays(data_atual.year)

        for move in self:
            _logger.info(f"################ move {move}")

            if move.type == 'out_invoice' and move.state == 'posted' and move.invoice_payment_state == 'not_paid' and not self._active_payment_promise() :
                dias_atraso = 0
                dia = data_atual
                while dia > move.invoice_date_due:
                    if dia.weekday() not in (5, 6) and dia not in feriados:
                        dias_atraso += 1
                    dia -= timedelta(days=1)

                if dias_atraso >= 2:
                    
                        # Envia o comando de bloqueio do veículo do cliente
                        vehicles = self.env['fleet.vehicle'].search([('driver_id', '=', move.partner_id.id)])
                        _logger.info(f"################ vehicles {vehicles}")
                        if vehicles:
                            for vehicle in vehicles:
                                vehicle.tracker_device.stop_engine()

    def _confirmation_sms_account_template(self, template_xmlid):
        try:
            return self.env.ref(template_xmlid)
        except ValueError:
            return False

    def _reusable_sms_call(self, template_xmlid, invoice_filters):
        """Send an SMS text reminder to custumers pay invoices"""

        template_id = self._confirmation_sms_account_template(template_xmlid)
        invoices = self.search(invoice_filters) or None
        if invoices:
            for posted_invoice in invoices:
                posted_invoice._message_sms_with_template(
                    template=template_id,
                    # template_xmlid="account_move.sms_template_data_invoice_sent",
                    # template_fallback=_("Event reminder: %s, %s.")
                    #% (posted_invoice.name, posted_invoice.partner_id.name),
                    # partner_ids=self._sms_get_default_partners().ids,
                    partner_ids=[posted_invoice.partner_id.id],
                    put_in_queue=False,
                )
                

    def _do_sms_reminder(self):
        ### Invoice sent: Alert by SMS Text Message
        self._reusable_sms_call(
            "account_sms.sms_template_data_invoice_sent",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid')
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "=",
                    fields.Datetime.now().date() - timedelta(days=3),
                ),
            ],
        )
        ### Invoice due date: Alert by SMS Text Message
        self._reusable_sms_call(
            "account_sms.sms_template_data_invoice_due_date",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                ("invoice_date_due", "=", fields.Datetime.now().date()),
            ],
        )
        ### Invoice overdue d+1: Alert by SMS Text Message
        self._reusable_sms_call(
            "account_sms.sms_template_data_invoice_overdue_1",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "=",
                    fields.Datetime.now().date() - timedelta(days=1),
                ),
            ],
        )
        ### Invoice overdue d+2: Alert by SMS Text Message
        self._reusable_sms_call(
            "account_sms.sms_template_data_invoice_overdue_2",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "=",
                    fields.Datetime.now().date() - timedelta(days=2),
                ),
            ],
        )
        ### Invoice overdue d+3: Alert by SMS Text Message
        self._reusable_sms_call(
            "account_sms.sms_template_data_invoice_overdue_3",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "<=",
                    fields.Datetime.now().date() - timedelta(days=3),
                ),
            ],
        )
