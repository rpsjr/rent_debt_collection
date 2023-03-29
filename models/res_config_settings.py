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