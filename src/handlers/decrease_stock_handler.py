"""
Handler: decrease stock
SPDX - License - Identifier: LGPL - 3.0 - or -later
Auteurs : Gabriel C. Ullmann, Fabio Petrillo, 2025
"""
import config
import requests
from opentelemetry import trace
from logger import Logger
from handlers.handler import Handler
from order_saga_state import OrderSagaState

class DecreaseStockHandler(Handler):
    """ Handle the stock check-out of a given list of products and quantities. Trigger rollback of previous steps in case of failure. """

    def __init__(self, order_item_data):
        """ Constructor method """
        self.order_item_data = order_item_data
        super().__init__()

    def run(self):
        """Call StoreManager to decrease stock for each item"""
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("decrease_stock_handler_run") as span:
            span.set_attribute("items_count", len(self.order_item_data))
            
            # Ajouter les détails des produits au span principal
            for idx, item in enumerate(self.order_item_data):
                span.set_attribute(f"product_{idx}_id", item["product_id"])
                span.set_attribute(f"product_{idx}_quantity", item["quantity"])
            
            try:
                for i, item in enumerate(self.order_item_data):
                    with tracer.start_as_current_span(f"decrease_stock_item_{i}") as item_span:
                        item_span.set_attribute("product_id", item["product_id"])
                        item_span.set_attribute("quantity", item["quantity"])
                        
                        # Diminuer le stock pour chaque item de la commande
                        stock_data = {
                            "product_id": item["product_id"],
                            "quantity": -item["quantity"]  # Quantité négative pour diminuer le stock
                        }
                        
                        with tracer.start_as_current_span("store_api_decrease_stock"):
                            response = requests.post(f'{config.API_GATEWAY_URL}/store-api/stocks',
                                json=stock_data,
                                headers={'Content-Type': 'application/json'}
                            )
                        
                        if not response.ok:
                            text = response.json() if response.content else "Aucun contenu de réponse"
                            item_span.set_attribute("success", False)
                            item_span.set_attribute("error_code", response.status_code)
                            item_span.set_attribute("error_message", str(text))
                            span.set_attribute("success", False)
                            self.logger.error(f"Erreur {response.status_code} lors de la diminution du stock pour le produit {item['product_id']}: {text}")
                            return OrderSagaState.CANCELLING_ORDER
                        else:
                            item_span.set_attribute("success", True)
                
                span.set_attribute("success", True)
                self.logger.debug("La sortie des articles du stock a réussi")
                return OrderSagaState.CREATING_PAYMENT
                
            except Exception as e:
                span.set_attribute("success", False)
                span.set_attribute("error_message", str(e))
                self.logger.error("La sortie des articles du stock a échoué : " + str(e))
                return OrderSagaState.CANCELLING_ORDER
        
    def rollback(self):
        """ Call StoreManager to revert stock check out (in other words, check-in the previously checked-out product and quantity) """
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("decrease_stock_handler_rollback") as span:
            span.set_attribute("items_count", len(self.order_item_data))
            
            # Ajouter les détails des produits au span principal
            for idx, item in enumerate(self.order_item_data):
                span.set_attribute(f"product_{idx}_id", item["product_id"])
                span.set_attribute(f"product_{idx}_quantity", item["quantity"])
            
            try:
                success_count = 0
                for i, item in enumerate(self.order_item_data):
                    with tracer.start_as_current_span(f"increase_stock_item_{i}") as item_span:
                        item_span.set_attribute("product_id", item["product_id"])
                        item_span.set_attribute("quantity", item["quantity"])
                        
                        # Remettre le stock pour chaque item de la commande (compensation)
                        stock_data = {
                            "product_id": item["product_id"],
                            "quantity": item["quantity"]  # Quantité positive pour remettre le stock
                        }
                        
                        with tracer.start_as_current_span("store_api_increase_stock"):
                            response = requests.post(f'{config.API_GATEWAY_URL}/store-api/stocks',
                                json=stock_data,
                                headers={'Content-Type': 'application/json'}
                            )
                        
                        if not response.ok:
                            text = response.json() if response.content else "Aucun contenu de réponse"
                            item_span.set_attribute("success", False)
                            item_span.set_attribute("error_code", response.status_code)
                            item_span.set_attribute("error_message", str(text))
                            self.logger.error(f"Erreur {response.status_code} lors de la remise en stock du produit {item['product_id']}: {text}")
                            # Continue quand même avec les autres items pour tenter de compenser autant que possible
                        else:
                            item_span.set_attribute("success", True)
                            success_count += 1
                
                span.set_attribute("success_count", success_count)
                span.set_attribute("total_items", len(self.order_item_data))
                self.logger.debug("L'entrée des articles dans le stock a réussi")
                return OrderSagaState.CANCELLING_ORDER
                
            except Exception as e:
                span.set_attribute("error_message", str(e))
                self.logger.error("La remise en stock a échoué : " + str(e))
                return OrderSagaState.CANCELLING_ORDER