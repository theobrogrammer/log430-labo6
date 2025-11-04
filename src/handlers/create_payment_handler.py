"""
Handler: create payment transaction
SPDX - License - Identifier: LGPL - 3.0 - or -later
Auteurs : Gabriel C. Ullmann, Fabio Petrillo, 2025
"""
import config
import requests
from opentelemetry import trace
from logger import Logger
from handlers.handler import Handler
from order_saga_state import OrderSagaState

class CreatePaymentHandler(Handler):
    """ Handle the creation of a payment transaction for a given order. Trigger rollback of previous steps in case of failure. """

    def __init__(self, order_id, order_data):
        """ Constructor method """
        self.order_id = order_id
        self.order_data = order_data
        self.total_amount = 0
        self.payment_id = 0
        super().__init__()

    def run(self):
        """Call payment microservice to generate payment transaction"""
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("create_payment_handler_run") as span:
            span.set_attribute("order_id", self.order_id)
            span.set_attribute("user_id", self.order_data.get('user_id', 'unknown'))
            
            try:
                # Étape 1: Obtenir le total de la commande
                with tracer.start_as_current_span("get_order_details") as order_span:
                    order_response = requests.get(f'{config.API_GATEWAY_URL}/store-api/orders/{self.order_id}')
                    
                if not order_response.ok:
                    text = order_response.json() if order_response.content else "Aucun contenu de réponse"
                    order_span.set_attribute("success", False)
                    order_span.set_attribute("error_code", order_response.status_code)
                    order_span.set_attribute("error_message", str(text))
                    span.set_attribute("success", False)
                    span.set_attribute("failure_step", "get_order_details")
                    self.logger.error(f"Erreur {order_response.status_code} lors de la récupération de la commande: {text}")
                    return OrderSagaState.INCREASING_STOCK
                
                order_details = order_response.json()
                self.total_amount = order_details.get('total_amount', 0)
                order_span.set_attribute("success", True)
                span.set_attribute("total_amount", self.total_amount)
                
                # Étape 2: Créer la transaction de paiement
                payment_data = {
                    "order_id": self.order_id,
                    "user_id": self.order_data.get('user_id'),
                    "total_amount": self.total_amount
                }
                
                with tracer.start_as_current_span("create_payment_transaction") as payment_span:
                    payment_response = requests.post(f'{config.API_GATEWAY_URL}/payments-api/payments',
                        json=payment_data,
                        headers={'Content-Type': 'application/json'}
                    )
                
                if payment_response.ok:
                    payment_result = payment_response.json()
                    self.payment_id = payment_result.get('payment_id', 0)
                    payment_span.set_attribute("success", True)
                    payment_span.set_attribute("payment_id", self.payment_id)
                    span.set_attribute("success", True)
                    span.set_attribute("payment_id", self.payment_id)
                    self.logger.debug("La création d'une transaction de paiement a réussi")
                    return OrderSagaState.COMPLETED
                else:
                    text = payment_response.json() if payment_response.content else "Aucun contenu de réponse"
                    payment_span.set_attribute("success", False)
                    payment_span.set_attribute("error_code", payment_response.status_code)
                    payment_span.set_attribute("error_message", str(text))
                    span.set_attribute("success", False)
                    span.set_attribute("failure_step", "create_payment_transaction")
                    self.logger.error(f"Erreur {payment_response.status_code} lors de la création du paiement: {text}")
                    return OrderSagaState.INCREASING_STOCK

            except Exception as e:
                span.set_attribute("success", False)
                span.set_attribute("error_message", str(e))
                self.logger.error("La création d'une transaction de paiement a échoué : " + str(e))
                return OrderSagaState.INCREASING_STOCK
        
    def rollback(self):
        """Call payment microservice to delete payment transaction"""
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("create_payment_handler_rollback") as span:
            span.set_attribute("payment_id", self.payment_id)
            
            try:
                if self.payment_id > 0:
                    with tracer.start_as_current_span("delete_payment_transaction"):
                        response = requests.delete(f'{config.API_GATEWAY_URL}/payments-api/payments/{self.payment_id}')
                        
                    if response.ok:
                        span.set_attribute("success", True)
                        self.logger.debug("La suppression d'une transaction de paiement a réussi")
                    else:
                        text = response.json() if response.content else "Aucun contenu de réponse"
                        span.set_attribute("success", False)
                        span.set_attribute("error_code", response.status_code)
                        span.set_attribute("error_message", str(text))
                        self.logger.error(f"Erreur {response.status_code} lors de la suppression du paiement: {text}")
                else:
                    span.set_attribute("success", True)
                    span.set_attribute("skipped", True)
                    self.logger.debug("Aucun paiement à supprimer (payment_id = 0)")
                
                return OrderSagaState.INCREASING_STOCK
                
            except Exception as e:
                span.set_attribute("success", False)
                span.set_attribute("error_message", str(e))
                self.logger.error("La suppression d'une transaction de paiement a échoué : " + str(e))
                return OrderSagaState.INCREASING_STOCK