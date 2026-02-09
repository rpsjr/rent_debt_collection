# -*- coding: utf-8 -*-
import logging
import datetime
import pytz
from datetime import timedelta
from odoo import models, fields, api, _
from workalendar.america.brazil import Brazil

_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    payment_promise = fields.Datetime(string='Payment Promise', help="Date and time of payment promise")

    def _active_payment_promise(self):
        """Retorna True se houver uma promessa de pagamento válida no futuro."""
        return self.payment_promise and self.payment_promise > fields.Datetime.now()

    def _create_payment_promise(self):
        self.write({
            'payment_promise': fields.Datetime.now() + timedelta(hours=24)
        })

    def _is_recidivist(self):
        """
        Verifica se o parceiro (motorista) é reincidente em atrasos nos últimos N dias.
        Considera feriados e finais de semana: Se o vencimento cair em dia não útil,
        o pagamento no próximo dia útil é considerado pontual.
        """
        
        # Instancia o calendário apenas uma vez para performance
        cal = Brazil()
        
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
        
        # Otimização: Search apenas nos campos necessários
        previous_invoices = self.search(domain)
        
        for inv in previous_invoices:
            # 1. Se não está paga e a data de vencimento (original) já passou, é atraso certo.
            if inv.invoice_payment_state != 'paid':
                return True
            
            # 2. Se está paga, precisamos verificar QUANDO foi paga em relação ao dia útil
            reconciled_vals = inv._get_reconciled_info_JSON_values() or []
            
            payment_dates = []
            for val in reconciled_vals:
                p_date = val.get('date')
                if p_date:
                    payment_dates.append(fields.Date.from_string(str(p_date)))
            
            if payment_dates:
                last_payment_date = max(payment_dates)
                original_due_date = inv.invoice_date_due

                # Lógica de Dia Útil Bancário:
                # Se o vencimento cai em dia não útil, posterga para o próximo dia útil.
                if not cal.is_working_day(original_due_date):
                    legal_due_date = cal.find_following_working_day(original_due_date)
                else:
                    legal_due_date = original_due_date
                
                # Compara a data do pagamento com a data legal de vencimento
                if last_payment_date > legal_due_date:
                    return True
                        
        return False

    def _batch_block_vehicle_w_invoice_overdue(self):
        """
        Job cron para bloquear veículos com faturas vencidas.
        Executa apenas dentro do horário comercial configurado (Horário Bahia/SP).
        """
        # Configuração de Fuso Horário Correto
        tz_name = self.env.user.tz or 'America/Sao_Paulo'
        local_tz = pytz.timezone(tz_name)
        now_utc = datetime.datetime.now(pytz.utc)
        now_local = now_utc.astimezone(local_tz)

        ICP = self.env['ir.config_parameter'].sudo()
        start_hour = float(ICP.get_param('fleet.block_start_hour', default=6.0))
        end_hour = float(ICP.get_param('fleet.block_end_hour', default=18.0))

        # Verifica se está fora do horário permitido
        if not (start_hour <= now_local.hour < end_hour):
            _logger.info(f"Skipping block batch: Outside working hours ({now_local.strftime('%H:%M')} in {tz_name})")
            return

        _logger.info("Starting batch vehicle block check...")

        # Data de corte: Vencidas até ontem (hoje não conta como atraso para bloqueio imediato no batch)
        cut_off_date = fields.Date.context_today(self) - timedelta(days=1)

        invoice_filters = [
            ("type", "=", "out_invoice"),
            ('invoice_payment_state', '=', 'not_paid'),
            ("state", "=", "posted"),
            ("invoice_date_due", "<=", cut_off_date),
            # Lógica: Faturas SEM transação OU com transação VENCIDA/ATRASADA
            '|',
                ('transaction_ids', '=', False),
                ('transaction_ids.inter_status', 'in', ['VENCIDO', 'ATRASADO'])
        ]

        moves = self.search(invoice_filters)
        _logger.info(f"Found {len(moves)} potentially overdue invoices.")

        for move in moves:
            try:
                move._block_vehicle_w_invoice_overdue()
                # Commit a cada registro para evitar long transaction locks e timeouts
                self.env.cr.commit()
            except Exception as e:
                self.env.cr.rollback()
                _logger.exception(f"Error processing block for move {move.id}: {e}")

    def _block_vehicle_w_invoice_overdue(self):
        """
        Lógica individual de bloqueio. Verifica tolerância e executa o comando.
        """
        self.ensure_one()
        
        # 1. Validações básicas (Guard Clauses)
        if not (self.type == 'out_invoice' and self.state == 'posted' and self.invoice_payment_state == 'not_paid'):
            return

        if self._active_payment_promise():
            _logger.info(f"Move {self.id}: Bloqueio ignorado devido a promessa de pagamento ativa.")
            return

        # 2. Validação de Transações (Inter)
        valid_transactions = self.transaction_ids.filtered(lambda t: t.state != 'cancel')
        if valid_transactions:
            # Se tem transações, SÓ bloqueia se houver alguma VENCIDA ou ATRASADA.
            is_inter_overdue = any(t.inter_status in ('VENCIDO', 'ATRASADO') for t in valid_transactions)
            if not is_inter_overdue:
                _logger.info(f"Move {self.id}: Bloqueio ignorado. Status Inter regular.")
                return

        # 3. Definição de Tolerância
        ICP = self.env['ir.config_parameter'].sudo()
        default_tolerance = int(ICP.get_param('fleet.block_tolerance_days', default=2))
        
        is_recidivist = self._is_recidivist()
        tolerance_days = 0 if is_recidivist else default_tolerance

        # 4. Cálculo de dias úteis de atraso
        # Utilizando workalendar para verificar dias úteis
        cal = Brazil()
        today = fields.Date.context_today(self)
        days_overdue = 0
        
        # Iteramos do dia atual para trás até a data de vencimento
        check_date = today
        while check_date > self.invoice_date_due:
            if cal.is_working_day(check_date):
                days_overdue += 1
            check_date -= timedelta(days=1)

        _logger.info(f"Move {self.id}: Dias Atraso (Úteis)={days_overdue}, Tolerância={tolerance_days}, Reincidente={is_recidivist}")

        # 5. Margem de Segurança: Compensação Bancária
        # Esta margem de segurança SÓ se aplica quando NÃO há tolerância de atraso (ex: reincidentes).
        # Se o motorista já possui dias de tolerância, ele já teve tempo suficiente para a compensação.
        if tolerance_days == 0 and days_overdue == 1:
            compensation_limit_hour = float(ICP.get_param('fleet.compensation_limit_hour', default=12.0))
            
            tz_name = self.env.user.tz or 'America/Sao_Paulo'
            local_tz = pytz.timezone(tz_name)
            now_local = datetime.datetime.now(pytz.utc).astimezone(local_tz)
            
            # Converte float (ex: 12.5) para horas e minutos
            comp_hour = int(compensation_limit_hour)
            comp_min = int((compensation_limit_hour - comp_hour) * 60)
            
            # Se a hora atual local é menor que o limite, aguardamos
            if (now_local.hour < comp_hour) or (now_local.hour == comp_hour and now_local.minute < comp_min):
                _logger.info(f"Move {self.id}: Bloqueio adiado aguardando compensação bancária (Limite: {compensation_limit_hour}h, Agora: {now_local.strftime('%H:%M')})")
                return

        # 6. Execução do Bloqueio
        if days_overdue > tolerance_days:
            self._execute_vehicle_block(days_overdue, tolerance_days, is_recidivist)

    def _execute_vehicle_block(self, days_overdue, tolerance_days, is_recidivist):
        """
        Método auxiliar para separar a lógica de busca e comando do rastreador.
        """
        vehicles = self.env['fleet.vehicle'].search([('driver_id', '=', self.partner_id.id)])
        
        if not vehicles:
            _logger.warning(f"Move {self.id}: Nenhum veículo encontrado para o parceiro {self.partner_id.name}.")
            return

        for vehicle in vehicles:
            # Otimização: Pula se não tiver rastreador ou já estiver bloqueado
            if not vehicle.tracker_device or vehicle.tracker_device.engine_last_cmd == 'blocked':
                continue

            try:
                _logger.info(f"Sending BLOCK command to vehicle {vehicle.license_plate}")
                response = vehicle.tracker_device.stop_engine()
                
                if response:
                    # Log no Veículo
                    msg_vehicle = _(
                        "Veículo bloqueado automaticamente por inadimplência.<br/>"
                        "<b>Fatura:</b> %s<br/>"
                        "<b>Dias de atraso:</b> %s<br/>"
                        "<b>Reincidente:</b> %s"
                    ) % (self.name, days_overdue, "Sim" if is_recidivist else "Não")
                    vehicle.message_post(body=msg_vehicle)

                    # Log na Fatura
                    msg_move = _(
                        "Comando de bloqueio enviado para o veículo %s.<br/>"
                        "Atraso superior a %s dias de tolerância."
                    ) % (vehicle.license_plate, tolerance_days)
                    self.message_post(body=msg_move)

            except Exception as e:
                _logger.error(f"Erro ao bloquear veículo {vehicle.license_plate} (Fatura {self.id}): {e}")

    def _batch_unlock_vehicle_clean_record(self):
        """
        Itera sobre veículos bloqueados, verifica as faturas do motorista
        e desbloqueia se não houver mais pendências financeiras.
        """
        _logger.info("Starting batch vehicle unlock check...")

        # 1. Busca veículos que estão atualmente bloqueados
        blocked_vehicles = self.env['fleet.vehicle'].search([
            ('tracker_device', '!=', False),
            ('tracker_device.engine_last_cmd', '=', 'blocked')
        ])

        _logger.info(f"Found {len(blocked_vehicles)} blocked vehicles to evaluate for unblock.")

        for vehicle in blocked_vehicles:
            driver = vehicle.driver_id
            if not driver:
                continue
            
            # 2. Busca todas as faturas em aberto do motorista
            overdue_invoices = self.env['account.move'].search([
                ('partner_id', '=', driver.id),
                ('type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('invoice_payment_state', '=', 'not_paid'),
            ])
            
            # 3. Força a verificação de pagamento no gateway para cada fatura em aberto
            for move in overdue_invoices:
                transactions = move.transaction_ids.filtered(
                    lambda t: t.state not in ('cancel', 'error')
                )
                for tx in transactions:
                    try:
                        # Chama o método de verificação de transação (Inter/Gateway)
                        tx.action_verify_transaction()
                        # Commit imediato para atualizar o status da fatura/transação no banco
                        self.env.cr.commit()
                    except Exception as e:
                        self.env.cr.rollback()
                        _logger.error(f"Error verifying transaction {tx.id} for move {move.id}: {e}")

            # 4. Avalia se o motorista ainda possui faturas que justificam o bloqueio
            # Invalida o cache para ler os estados atualizados após action_verify_transaction
            overdue_invoices.invalidate_cache()
            
            still_has_blocking_debt = False
            for move in overdue_invoices:
                # Se a fatura foi paga ou tem promessa ativa, ela não mantém o bloqueio
                if move.invoice_payment_state == 'paid' or move._active_payment_promise():
                    continue

                # Verifica a tolerância de dias úteis
                ICP = self.env['ir.config_parameter'].sudo()
                default_tolerance = int(ICP.get_param('fleet.block_tolerance_days', default=2))
                is_recidivist = move._is_recidivist()
                tolerance_days = 0 if is_recidivist else default_tolerance

                cal = Brazil()
                today = fields.Date.context_today(self)
                days_overdue = 0
                check_date = today
                while check_date > move.invoice_date_due:
                    if cal.is_working_day(check_date):
                        days_overdue += 1
                    check_date -= timedelta(days=1)
                
                if days_overdue > tolerance_days:
                    # Encontrou pelo menos uma fatura que justifica manter o bloqueio
                    still_has_blocking_debt = True
                    break

            # 5. Se não houver mais débitos impeditivos, envia o comando de desbloqueio
            if not still_has_blocking_debt:
                try:
                    _logger.info(f"Unblocking vehicle {vehicle.license_plate} for driver {driver.name}")
                    vehicle.tracker_device.resume_engine()
                    
                    vehicle.message_post(body=_("Veículo desbloqueado automaticamente: Pendências financeiras regularizadas."))
                    self.env.cr.commit()
                except Exception as e:
                    self.env.cr.rollback()
                    _logger.error(f"Error unblocking vehicle {vehicle.license_plate}: {e}")
