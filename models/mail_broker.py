# Copyright 2022 Creu Blanca
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models, _
import requests
import json

from odoo.exceptions import Warning


import logging
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__) #<<<<<<<<<<<<<<<<<<<<

class MailBroker(models.Model):
    _inherit = "mail.broker"

    whatsapp_version = fields.Char(default="16.0")
    whatsapp_templates_namespace = fields.Char(default="ee7e7b5b_d050_40c8_970d_6f98dfa7b160")
    whatsapp_business_account_ID = fields.Char(default="106340775724137")

    def read_message_templates(self):

        url = f"https://graph.facebook.com/v{self.whatsapp_version}/{self.whatsapp_business_account_ID}/message_templates" #whatsapp_business_account_ID
        params = {
            "limit": 3,
            "access_token": self.token #system_user_access_token
        }

        response = requests.get(url, params=params)
        
        #resp = json.dumps(response.text, indent=4, sort_keys=True)
        for template in response.json()["data"]:
            _logger.info(f"################ read_message_templates {template}")
            Warning( template )

