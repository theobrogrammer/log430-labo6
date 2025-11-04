"""
Handler: create order
SPDX - License - Identifier: LGPL - 3.0 - or -later
Auteurs : Gabriel C. Ullmann, Fabio Petrillo, 2025
"""
import config
import requests
from opentelemetry import trace
from logger import Logger
from handlers.handler import Handler
from order_saga_state import OrderSagaState

class CreateOrderHandler(Handler):
    """ Handle order creation. Delete order in case of failure. """

    def __init__(self, order_data):
        """ Constructor method """
        self.order_data = order_data
        self.order_id = 0
        super().__init__()

    def run(self):
        """Call StoreManager to create order"""
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("create_order_handler_run") as span:
            span.set_attribute("user_id", self.order_data.get("user_id", "unknown"))
            span.set_attribute("items_count", len(self.order_data.get("items", [])))
            
            # Ajouter les détails des produits au span principal
            for idx, item in enumerate(self.order_data.get("items", [])):
                span.set_attribute(f"product_{idx}_id", item.get("product_id", "unknown"))
                span.set_attribute(f"product_{idx}_quantity", item.get("quantity", 0))
            
            try:
                # ATTENTION: Si vous exécutez ce code dans Docker, n'utilisez pas localhost. Utilisez plutôt le hostname de votre API Gateway
                with tracer.start_as_current_span("store_api_create_order"):
                    response = requests.post(f'{config.API_GATEWAY_URL}/store-api/orders',
                        json=self.order_data,
                        headers={'Content-Type': 'application/json'}
                    )
                    
                if response.ok:
                    data = response.json() 
                    self.order_id = data['order_id'] if data else 0
                    span.set_attribute("order_id", self.order_id)
                    span.set_attribute("success", True)
                    self.logger.debug("La création de la commande a réussi")
                    return OrderSagaState.DECREASING_STOCK
                else:
                    text = response.json() 
                    span.set_attribute("success", False)
                    span.set_attribute("error_code", response.status_code)
                    span.set_attribute("error_message", str(text))
                    self.logger.error(f"Erreur {response.status_code} : {text}")
                    return OrderSagaState.COMPLETED

            except Exception as e:
                span.set_attribute("success", False)
                span.set_attribute("error_message", str(e))
                self.logger.error("La création de la commande a échoué : " + str(e))
                return OrderSagaState.COMPLETED
        
    def rollback(self):
        """Call StoreManager to delete order"""
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("create_order_handler_rollback") as span:
            span.set_attribute("order_id", self.order_id)
            
            try:
                # ATTENTION: Si vous exécutez ce code dans Docker, n'utilisez pas localhost. Utilisez plutôt le hostname de votre API Gateway
                with tracer.start_as_current_span("store_api_delete_order"):
                    response = requests.delete(f'{config.API_GATEWAY_URL}/store-api/orders/{self.order_id}')
                    
                if response.ok:
                    data = response.json() 
                    self.order_id = data['order_id'] if data else 0
                    span.set_attribute("success", True)
                    self.logger.debug("La supression de la commande a réussi")
                    return OrderSagaState.COMPLETED
                else:
                    text = response.json() 
                    span.set_attribute("success", False)
                    span.set_attribute("error_code", response.status_code)
                    span.set_attribute("error_message", str(text))
                    self.logger.error(f"Erreur {response.status_code} : {text}")
                    return OrderSagaState.COMPLETED

            except Exception as e:
                span.set_attribute("success", False)
                span.set_attribute("error_message", str(e))
                self.logger.error("La supression de la commande a échoué : " + str(e))
                return OrderSagaState.COMPLETED