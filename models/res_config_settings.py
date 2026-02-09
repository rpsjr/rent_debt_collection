# Copyright <2023> <Raimundo Pereira da Silva Junior <raimundopsjr@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models, fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    traccar_api_url = fields.Char(
        string='Traccar API URL',
        config_parameter='fleet.traccar_api_url',
        help='URL da API do Traccar'
    )

    traccar_api_key = fields.Char(
        string='Traccar API Key',
        config_parameter='fleet.traccar_api_key',
        help='Chave de acesso à API do Traccar'
    )
    
    fleet_block_start_hour = fields.Float(
        string='Inicio do Bloqueio (Hora)',
        config_parameter='fleet.block_start_hour',
        default=6.0,
        help='Hora do dia (0-23) a partir da qual o bloqueio de veículos inadimplentes é permitido.'
    )

    fleet_block_end_hour = fields.Float(
        string='Fim do Bloqueio (Hora)',
        config_parameter='fleet.block_end_hour',
        default=18.0,
        help='Hora do dia (0-23) até a qual o bloqueio de veículos inadimplentes é permitido.'
    )

    fleet_compensation_limit_hour = fields.Float(
        string='Limite de Compensação (Hora)',
        config_parameter='fleet.compensation_limit_hour',
        default=12.0,
        help='No primeiro dia útil após o vencimento (ou tolerância), aguarda até esta hora pela compensação bancária.'
    )

    fleet_recidivism_window_days = fields.Integer(
        string='Janela de Reincidência (Dias)',
        config_parameter='fleet.recidivism_window_days',
        default=28,
        help='Número de dias anteriores ao vencimento para verificar histórico de atrasos.'
    )

    fleet_block_tolerance_days = fields.Integer(
        string='Tolerância de Bloqueio (Dias)',
        config_parameter='fleet.block_tolerance_days',
        default=2,
        help='Dias de carência após o vencimento antes do bloqueio para bons pagadores.'
    )
