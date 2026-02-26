# -*- coding: utf-8 -*-
from odoo import models, api, fields, _
import re
import logging

_logger = logging.getLogger(__name__)

class MailBrokerChannel(models.Model):
    _inherit = 'mail.broker.channel'

    def message_post(self, **kwargs):
        # Captura a mensagem depois do super para garantir que temos os dados
        new_message = super(MailBrokerChannel, self).message_post(**kwargs)

        # Verifica se new_message é um objeto válido (pode ser False ou lista)
        if not new_message or not isinstance(new_message, models.Model):
            return new_message

        # Verifica se é uma mensagem do tipo comentário (padrão para mensagens de chat)
        if new_message.message_type == 'comment':
            # Verifica se é uma mensagem de entrada (não interna, autor não é o usuário atual)
            # Em canais de broker, mensagens de entrada geralmente têm o autor setado como o parceiro externo
            # Se o autor for diferente do usuário logado (geralmente OdooBot ou usuário sistema), consideramos externa
            # Adicionalmente, podemos verificar se o canal é do tipo WhatsApp
            if new_message.author_id and new_message.author_id.id != self.env.user.partner_id.id:
                 # Check broker type if available on channel
                if hasattr(self, 'broker_type') and self.broker_type == 'whatsapp':
                    self._check_debt_collection_keywords(new_message)
                # Fallback: se não tiver broker_type, assume comportamento padrão se o autor não for interno
                elif not new_message.is_internal:
                     self._check_debt_collection_keywords(new_message)

        return new_message

    def _check_debt_collection_keywords(self, message):
        """Analisa o conteúdo da mensagem por palavras-chave de cobrança."""
        if not message.body:
            return

        # Limpa HTML tags para análise de texto
        # message.body geralmente é HTML
        body_text = re.sub('<[^<]+?>', '', message.body).upper()
        keywords = ['PAGUEI', 'COMPROVANTE', 'PIX', 'BOLETO', 'DESBLOQUEIO', 'PAGAMENTO', 'JÁ PAGUEI']

        if any(k in body_text for k in keywords):
            self._handle_debt_collection_alert(message)

    def _handle_debt_collection_alert(self, message):
        """Cria atividade de cobrança na fatura mais antiga em aberto."""
        # Tenta identificar o parceiro
        partner = self.env['res.partner']
        if self.partner_id:
            partner = self.partner_id
        elif message.author_id:
            partner = message.author_id

        if not partner:
            return

        # Busca faturas vencidas ou em aberto (ordenadas pela mais antiga)
        invoices = self.env['account.move'].search([
            ('partner_id', '=', partner.id),
            ('type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('invoice_payment_state', '=', 'not_paid')
        ], order='invoice_date_due asc')

        if not invoices:
            return

        # Foca na fatura mais antiga (provável motivo do contato)
        target_invoice = invoices[0]

        # Verifica se há transações do Boleto Inter pendentes e tenta sincronizar
        # Se a sincronização confirmar o pagamento, a fatura mudará de estado
        inter_txs = target_invoice.transaction_ids.filtered(
            lambda t: t.acquirer_id.provider == 'apiboletointer' and t.state not in ['done', 'cancel', 'error']
        )

        if inter_txs:
            try:
                for tx in inter_txs:
                    tx.action_verify_transaction()

                # Invalidamos o cache para garantir que o estado da fatura esteja atualizado
                target_invoice.invalidate_cache(['invoice_payment_state'], [target_invoice.id])

                if target_invoice.invoice_payment_state != 'not_paid':
                    # Se confirmou o pagamento, agradece e encerra
                    self.message_post(
                        body=_("Obrigado! Identificamos o pagamento da fatura %s automaticamente.") % target_invoice.name,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment'
                    )
                    return
            except Exception as e:
                # Loga o erro mas não interrompe o fluxo para criar a atividade manual
                _logger.error("Erro ao verificar boleto Inter para fatura %s: %s", target_invoice.name, str(e))

        summary = _('WhatsApp: Cliente informou pagamento/comprovante')
        clean_body = re.sub('<[^<]+?>', '', message.body)
        note = _('Cliente enviou mensagem sugestiva de pagamento: "%s". Verifique o comprovante no chat.') % (clean_body)

        # Evita duplicidade de atividades do mesmo tipo no mesmo dia para a mesma fatura
        domain = [
            ('res_id', '=', target_invoice.id),
            ('res_model', '=', 'account.move'),
            ('summary', '=', summary),
            ('date_deadline', '>=', fields.Date.today())
        ]

        if not self.env['mail.activity'].search_count(domain):
            # Agenda a atividade para o usuário responsável pela fatura ou o usuário atual (sistema/admin)
            user_id = target_invoice.invoice_user_id.id or self.env.user.id

            # Garante que temos um activity type id, se não, usamos padrão
            activity_type_xml_id = 'mail.mail_activity_data_todo'

            target_invoice.activity_schedule(
                activity_type_xml_id,
                summary=summary,
                note=note,
                user_id=user_id,
                date_deadline=fields.Date.today()
            )

