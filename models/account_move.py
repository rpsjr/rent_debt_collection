# -*- coding: utf-8 -*-
# Copyright <2023> <Raimundo Pereira da Silva Junior <raimundopsjr@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import datetime
from datetime import timedelta
from odoo import models, fields, api, _
from workalendar.america.brazil import Brazil

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'


    payment_promise = fields.Datetime(string='Payment Promise', help="Date and time of payment promise")

    def _active_payment_promise(self):
        if not self.payment_promise:
            return False
        elif self.payment_promise < datetime.datetime.now():
            return False
        return True

    def _create_payment_promise(self):
        payment_promise_date = datetime.datetime.now() + timedelta(hours=24)
        self.write({
            'payment_promise': payment_promise_date
        })
    
    def _is_recidivist(self):
        """
        Verifica se o parceiro (motorista) é reincidente em atrasos nos últimos N dias (configurável).
        Critério: Ter outra fatura que venceu no período configurado (antes da fatura atual)
        que esteja em aberto (não paga) OU que tenha sido paga com atraso.
        """
        
        # Get configured recidivism window or use default 28 days
        ICP = self.env['ir.config_parameter'].sudo()
        recidivism_days = int(ICP.get_param('fleet.recidivism_window_days', default=28))

        # Janela de análise: N dias antes do vencimento desta fatura
        start_check_date = self.invoice_date_due - timedelta(days=recidivism_days)
        
        domain = [
            ('id', '!=', self.id), # Não conta a si mesma
            ('partner_id', '=', self.partner_id.id),
            ('type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('invoice_date_due', '>=', start_check_date),
            ('invoice_date_due', '<', self.invoice_date_due), # Apenas histórico passado
        ]
        
        previous_invoices = self.search(domain)
        
        for inv in previous_invoices:
            # 1. Se não está paga e a data de vencimento já passou (garantido pelo domain), atrasou.
            if inv.invoice_payment_state != 'paid':
                return True
            
            # 2. Se está paga, verifica se o pagamento foi feito após o vencimento.
            # Odoo 13: Recupera infos de pagamento via widget JSON
            reconciled_vals = inv._get_reconciled_info_JSON_values()
            if reconciled_vals:
                payment_dates = []
                for val in reconciled_vals:
                    p_date = val.get('date')
                    if p_date:
                        # Garante conversão para date object
                        payment_dates.append(fields.Date.from_string(str(p_date)))
                
                if payment_dates:
                    last_payment_date = max(payment_dates)
                    if last_payment_date > inv.invoice_date_due:
                        return True
                        
        return False

    def _batch_block_vehicle_w_invoice_overdue(self):
        
        # Get current UTC time
        utc_now = datetime.datetime.utcnow()

        # Bahia's standard offset is UTC-3.
        # This approach does NOT automatically account for Daylight Saving Time (DST) changes.
        # If Bahia observes DST, you would need to manually adjust `bahia_offset_hours`
        # during the DST transition periods in Odoo.
        bahia_offset_hours = -3
        bahia_now = utc_now + timedelta(hours=bahia_offset_hours)

        # Get configured start and end hours or use default
        ICP = self.env['ir.config_parameter'].sudo()
        start_hour = float(ICP.get_param('fleet.block_start_hour', default=6.0))
        end_hour = float(ICP.get_param('fleet.block_end_hour', default=18.0))

        if start_hour <= bahia_now.hour < end_hour:
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
                # Commit the changes to the database
                self.env.cr.commit()
        else:
            # Use the built-in log function
            _logger.info("Skipped _batch_block_vehicle_w_invoice_overdue() because it's outside Bahia daylight hours (Bahia time: %s)" % bahia_now.strftime('%Y-%m-%d %H:%M:%S'))


    def _block_vehicle_w_invoice_overdue(self):
        data_atual = datetime.datetime.now().date()
        # Define o calendário de feriados no Brasil
        feriados = Brazil().holidays(data_atual.year)

        # Get configured tolerance days or use default 2 days
        ICP = self.env['ir.config_parameter'].sudo()
        default_tolerance_days = int(ICP.get_param('fleet.block_tolerance_days', default=2))

        for move in self:
            _logger.info(f"################ move {move}")

            if move.type == 'out_invoice' and move.state == 'posted' and move.invoice_payment_state == 'not_paid' and not self._active_payment_promise() :
                
                # Regra dinâmica de tolerância
                # Se for reincidente (atrasou nos últimos N dias), tolerância é 0 dias.
                # Se não for reincidente, mantém tolerância configurada (default 2 dias úteis).
                is_recidivist = move._is_recidivist()
                tolerance_days = 0 if is_recidivist else default_tolerance_days
                
                _logger.info(f"Move {move.id}: Recidivist={is_recidivist}, Tolerance={tolerance_days} days")

                dias_atraso = 0
                dia = data_atual
                while dia > move.invoice_date_due:
                    if dia.weekday() not in (5, 6) and dia not in feriados:
                        dias_atraso += 1
                    dia -= timedelta(days=1)

                if dias_atraso > tolerance_days:
                    
                        # Envia o comando de bloqueio do veículo do cliente
                        vehicles = self.env['fleet.vehicle'].search([('driver_id', '=', move.partner_id.id)])
                        _logger.info(f"################ vehicles {vehicles}")
                        if vehicles:
                            for vehicle in vehicles:
                                # Otimização: Só tenta bloquear se o estado atual não for 'blocked'
                                if vehicle.tracker_device and vehicle.tracker_device.engine_last_cmd != 'blocked':
                                    try:
                                        response = vehicle.tracker_device.stop_engine()
                                        if response:
                                            # Log no Chatter do Veículo
                                            vehicle.message_post(body=_("Veículo bloqueado automaticamente por inadimplência (Fatura %s). Dias de atraso: %s (Reincidente: %s)") % (move.name, dias_atraso, is_recidivist))
                                            # Log no Chatter da Fatura
                                            move.message_post(body=_("Comando de bloqueio enviado para o veículo %s devido a atraso > %s dias.") % (vehicle.license_plate, tolerance_days))
                                    except Exception as e:
                                        _logger.error(f"Erro ao bloquear veículo {vehicle.license_plate}: {e}")

    
    def _batch_unlock_vehicle_clean_record(self):
        """
        Desbloqueia veículos cujos motoristas não possuem mais faturas vencidas em aberto.
        Esta função deve rodar periodicamente (ex: a cada 20min).
        Otimização: Busca apenas veículos marcados como 'blocked' no tracker.
        """
        _logger.info("Starting _batch_unlock_vehicle_clean_record (Optimized)...")
        
        # 1. Busca apenas veículos que estão marcados como bloqueados e vinculados a motorista
        # Isso reduz drasticamente a busca no banco de dados
        blocked_vehicles = self.env['fleet.vehicle'].search([
            ('driver_id', '!=', False),
            ('tracker_device.engine_last_cmd', '=', 'blocked')
        ])
        
        if not blocked_vehicles:
            _logger.info("No blocked vehicles found to check.")
            return

        for vehicle in blocked_vehicles:
            partner = vehicle.driver_id
            
            # 2. Verifica se o parceiro AINDA tem faturas vencidas em aberto
            # Critério: Fatura Posted, Not Paid, e Data Vencimento < Hoje
            
            overdue_invoices_count = self.env['account.move'].search_count([
                ('partner_id', '=', partner.id),
                ('type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('invoice_payment_state', '=', 'not_paid'),
                ('invoice_date_due', '<', fields.Date.context_today(self))
            ])
            
            # Se não houver faturas vencidas, libera o veículo
            if overdue_invoices_count == 0:
                try:
                    if vehicle.tracker_device:
                         response = vehicle.tracker_device.resume_engine()
                         
                         if response:
                             # Log no Chatter do Veículo
                             vehicle.message_post(body=_("Veículo desbloqueado automaticamente. Cliente regularizou pendências financeiras."))
                         
                except Exception as e:
                    _logger.error(f"Error unlocking vehicle {vehicle.license_plate}: {str(e)}")
            
            # Commit a cada iteração para evitar locks longos
            self.env.cr.commit()

    def _confirmation_sms_account_template(self, template_xmlid):
        try:
            return self.env.ref(template_xmlid)
        except ValueError:
            return False

    def _reusable_sms_call(self, template_xmlid, invoice_filters, filter_recidivists=None):
        """
        Send an SMS text reminder to custumers pay invoices.
        
        filter_recidivists (bool): 
            If True, send only to recidivists.
            If False, send only to good payers.
            If None (default), send to all.
        """

        template_id = self._confirmation_sms_account_template(template_xmlid)
        invoices = self.search(invoice_filters) or None
        
        if invoices:
            for posted_invoice in invoices:
                
                # Check recidivism if filter is applied
                if filter_recidivists is not None:
                    # Precisamos chamar o metodo na instância da invoice
                    is_recidivist = posted_invoice._is_recidivist()
                    
                    if filter_recidivists and not is_recidivist:
                        continue # Queria apenas reincidentes, mas este não é. Pula.
                    
                    if not filter_recidivists and is_recidivist:
                        continue # Queria apenas bons pagadores, mas este é reincidente. Pula.

                posted_invoice._message_sms_with_template(
                    template=template_id,
                    partner_ids=[posted_invoice.partner_id.id],
                    put_in_queue=False,
                )
                

    def _do_sms_reminder(self):
        # 1. D-1 (Amanhã): Apenas para Reincidentes (Aviso de Bloqueio Iminente)
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_pre_due_bad",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "=",
                    fields.Datetime.now().date() + timedelta(days=1),
                ),
            ],
            filter_recidivists=True # Only Bad Payers
        )
        
        # 2. D+0 (Hoje): Vencimento
        
        # 2a. Bom Pagador: Lembrete amigável
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_due_date_good",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                ("invoice_date_due", "=", fields.Datetime.now().date()),
            ],
            filter_recidivists=False # Only Good Payers
        )
        
        # 2b. Reincidente: Aviso de bloqueio amanhã
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_due_date_bad",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                ("invoice_date_due", "=", fields.Datetime.now().date()),
            ],
            filter_recidivists=True # Only Bad Payers
        )
        
        # 3. D+1 (Atraso)
        
        # 3a. Bom Pagador: Aviso de atraso (bloqueio em D+3)
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_overdue_1_good",
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
            filter_recidivists=False # Only Good Payers
        )
        
        # 4. D+2 (Pré-bloqueio)
        
        # 4a. Bom Pagador: Aviso final antes do bloqueio em D+3
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_overdue_2_good",
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
            filter_recidivists=False # Only Good Payers
        )
        
        # 5. D > 2 ou Bloqueados (Geral)
        # Envia aviso de "Bloqueado / Regularize" para todos os atrasados acima de 2 dias
        # ou reincidentes que já passaram do prazo (D+1 em diante)
        # Para simplificar, usamos a lógica antiga para D-3 a D-8, mas usando o template bloqueado
        self._reusable_sms_call(
            "rent_debt_collection.sms_template_data_invoice_overdue_blocked",
            [
                ("type", "=", "out_invoice"),
                ('invoice_payment_state', '=', 'not_paid'),
                ("state", "=", "posted"),
                (
                    "invoice_date_due",
                    "<=",
                    fields.Datetime.now().date() - timedelta(days=3),
                ),
                (
                    "invoice_date_due",
                    ">",
                    fields.Datetime.now().date() - timedelta(days=9),
                ),
            ],
        )
