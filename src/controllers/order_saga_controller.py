"""
Order saga controller
SPDX - License - Identifier: LGPL - 3.0 - or -later
Auteurs : Gabriel C. Ullmann, Fabio Petrillo, 2025
"""
from opentelemetry import trace
from handlers.create_order_handler import CreateOrderHandler
from handlers.create_payment_handler import CreatePaymentHandler
from handlers.decrease_stock_handler import DecreaseStockHandler
from controllers.controller import Controller
from order_saga_state import OrderSagaState

class OrderSagaController(Controller):
    """ 
    This class manages states and transitions of an order saga. The current state is persisted only in memory, as an instance variable, therefore it does not allow retrying in case the application fails.
    Please read section 11 of the arc42 document of this project to understand the limitations of this implementation in more detail.
    """

    def __init__(self):
        """ Constructor method """
        super().__init__()
        # NOTE: veuillez lire le commentaire de ce classe pour mieux comprendre les limitations de ce implémentation
        self.current_saga_state = OrderSagaState.CREATING_ORDER
    
    def run(self, request):
        """ Perform steps of order saga """
        tracer = trace.get_tracer(__name__)
        
        with tracer.start_as_current_span("order_saga_execution") as saga_span:
            payload = request.get_json() or {}
            order_data = {
                "user_id": payload.get('user_id'),
                "items": payload.get('items', [])
            }
            
            saga_span.set_attribute("user_id", order_data.get("user_id", "unknown"))
            saga_span.set_attribute("items_count", len(order_data.get("items", [])))
            
            # Initialiser les handlers
            self.create_order_handler = CreateOrderHandler(order_data)
            self.decrease_stock_handler = None
            self.create_payment_handler = None
            
            # Stack des handlers complétés pour le rollback
            completed_handlers = []

            while self.current_saga_state is not OrderSagaState.COMPLETED:
                saga_span.set_attribute("current_state", self.current_saga_state.name)
                
                if self.current_saga_state == OrderSagaState.CREATING_ORDER:
                    with tracer.start_as_current_span("create_order"):
                        self.current_saga_state = self.create_order_handler.run()
                        if self.current_saga_state != OrderSagaState.COMPLETED:
                            completed_handlers.append(self.create_order_handler)
                    
                elif self.current_saga_state == OrderSagaState.DECREASING_STOCK:
                    with tracer.start_as_current_span("decrease_stock"):
                        self.decrease_stock_handler = DecreaseStockHandler(order_data["items"])
                        self.current_saga_state = self.decrease_stock_handler.run()
                        if self.current_saga_state == OrderSagaState.CREATING_PAYMENT:
                            completed_handlers.append(self.decrease_stock_handler)
                    
                elif self.current_saga_state == OrderSagaState.CREATING_PAYMENT:
                    with tracer.start_as_current_span("create_payment"):
                        self.create_payment_handler = CreatePaymentHandler(self.create_order_handler.order_id, order_data)
                        self.current_saga_state = self.create_payment_handler.run()
                        if self.current_saga_state == OrderSagaState.COMPLETED:
                            completed_handlers.append(self.create_payment_handler)
                    
                elif self.current_saga_state == OrderSagaState.INCREASING_STOCK:
                    with tracer.start_as_current_span("rollback_decrease_stock"):
                        # Rollback: remettre le stock
                        if self.decrease_stock_handler:
                            self.current_saga_state = self.decrease_stock_handler.rollback()
                        else:
                            self.current_saga_state = OrderSagaState.CANCELLING_ORDER
                    
                elif self.current_saga_state == OrderSagaState.CANCELLING_ORDER:
                    with tracer.start_as_current_span("rollback_create_order"):
                        # Rollback: annuler la commande
                        if self.create_order_handler:
                            self.current_saga_state = self.create_order_handler.rollback()
                        else:
                            self.current_saga_state = OrderSagaState.COMPLETED
                        self.is_error_occurred = True
                    
                else:
                    with tracer.start_as_current_span("saga_error_handling"):
                        self.is_error_occurred = True
                        self.logger.error(f"L'état saga n'est pas valide : {self.current_saga_state}")
                        
                        # Rollback en cascade des handlers complétés
                        for handler in reversed(completed_handlers):
                            try:
                                handler.rollback()
                            except Exception as e:
                                self.logger.error(f"Erreur lors du rollback: {str(e)}")
                        
                        self.current_saga_state = OrderSagaState.COMPLETED

            saga_span.set_attribute("final_state", self.current_saga_state.name)
            saga_span.set_attribute("error_occurred", self.is_error_occurred)
            
            result = {
                "order_id": self.create_order_handler.order_id,
                "status": "Une erreur s'est produite lors de la création de la commande." if self.is_error_occurred else "OK"
            }
            
            saga_span.set_attribute("order_id", result["order_id"] or "none")
            saga_span.set_attribute("saga_status", result["status"])
            
            return result



    
