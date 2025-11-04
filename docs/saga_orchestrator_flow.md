# Saga Orchestrator — déroulement et architecture

Ce document explique le déroulement d'une transaction orchestrée (saga) dans le projet : de l'appel HTTP entrant, à la logique du contrôleur, jusqu'aux handlers et leurs actions/compensations. Il couvre aussi l'instrumentation OpenTelemetry ajoutée pour Jaeger.

## 1. Vue d'ensemble rapide

- Entrée : appel HTTP POST vers l'orchestrateur (service `saga_orchestrator`).
- Orchestrateur : `src/saga_orchestrator.py` expose l'endpoint `/saga/order` qui délègue au `OrderSagaController`.
- Contrôleur (machine à états) : `src/controllers/order_saga_controller.py` exécute la saga comme une séquence d'étapes (handlers).
- Handlers : implémentent chaque étape et sa compensation (rollback) :
  - `src/handlers/create_order_handler.py`
  - `src/handlers/decrease_stock_handler.py`
  - `src/handlers/create_payment_handler.py`

L'orchestrateur parle aux microservices via l'API Gateway (KrakenD). Les URLs appelées sont de la forme `http(s)://<gateway>/store-api/...` et `http(s)://<gateway>/payments-api/...` — l'usage de `config.API_GATEWAY_URL` centralise cette adresse.

## 2. Exemple de requête (entrée)

POST /saga/order
Content-Type: application/json

Payload JSON attendu :

{
  "user_id": 123,
  "items": [
    {"product_id": 10, "quantity": 2},
    {"product_id": 20, "quantity": 1}
  ]
}

L'appel arrive à `src/saga_orchestrator.py` qui crée un `OrderSagaController` et appelle `run(request)`.

## 3. OrderSagaController — machine à états

Fichier : `src/controllers/order_saga_controller.py`

Comportement principal (méthode `run`):

1. Initialise `order_data` à partir du JSON entrant.
2. Initialise les handlers : `CreateOrderHandler`, `DecreaseStockHandler` (lorsque nécessaire), `CreatePaymentHandler` (lorsque nécessaire).
3. Maintient un `current_saga_state` et une pile `completed_handlers` (pour rollback en cascade si nécessaire).
4. Boucle tant que `current_saga_state` != `COMPLETED` et fait :
   - Si `CREATING_ORDER` → appelle `create_order_handler.run()`.
   - Si `DECREASING_STOCK` → instancie `DecreaseStockHandler(items)` et appelle `.run()`.
   - Si `CREATING_PAYMENT` → instancie `CreatePaymentHandler(order_id, order_data)` et appelle `.run()`.
   - Si `INCREASING_STOCK` → appelle `decrease_stock_handler.rollback()` (compensation du stock).
   - Si `CANCELLING_ORDER` → appelle `create_order_handler.rollback()` (supprime la commande créée).
   - En cas d'état inconnu → déclenche rollback en cascade sur `completed_handlers`.
5. Retourne le résultat : { "order_id": <id>, "status": "OK" | "Une erreur..." }

États (enum `OrderSagaState`) — résumé :
- CREATING_ORDER
- DECREASING_STOCK
- CREATING_PAYMENT
- INCREASING_STOCK (compensation stock)
- CANCELLING_ORDER (compensation commande)
- COMPLETED

## 4. Handlers — responsabilités et compensation

### CreateOrderHandler (`src/handlers/create_order_handler.py`)
- run(): POST vers `config.API_GATEWAY_URL + /store-api/orders` pour créer une commande.
  - En succès : stocke `order_id` retourné, renvoie `DECREASING_STOCK`.
  - En échec : log, renvoie `COMPLETED` (fin de saga).
- rollback(): DELETE vers `/store-api/orders/{order_id}` pour supprimer la commande créée.

### DecreaseStockHandler (`src/handlers/decrease_stock_handler.py`)
- run(): pour chaque item, POST vers `/store-api/stocks` avec {product_id, quantity: -N} pour diminuer.
  - Si toutes les diminutions réussissent : renvoie `CREATING_PAYMENT`.
  - Si une diminution échoue : log, renvoie `CANCELLING_ORDER` (on remonte pour annuler la commande déjà créée).
- rollback(): pour chaque item, POST vers `/store-api/stocks` avec {product_id, quantity: +N} pour remettre le stock.
  - Tente de compenser chaque item même si certains échecs surviennent (best-effort).

### CreatePaymentHandler (`src/handlers/create_payment_handler.py`)
- run():
  1. GET `/store-api/orders/{order_id}` pour récupérer `total_amount`.
  2. POST `/payments-api/payments` avec {order_id, user_id, total_amount}.
  - En succès : stocke `payment_id` et renvoie `COMPLETED`.
  - En échec (obtention de la commande ou création du paiement) : log et renvoie `INCREASING_STOCK` afin de relancer la compensation de stock.
- rollback(): DELETE `/payments-api/payments/{payment_id}` si `payment_id` > 0.

**Note importante :** Le constructeur est `CreatePaymentHandler(order_id, order_data)` - l'ordre des paramètres est crucial.

## 5. Flux normal et flux d'erreur (exemples détaillés)

### 🎯 Flux Normal (Happy Path) - Tout réussit

Quand tout fonctionne bien, voici ce qui se passe :

```
CREATING_ORDER → DECREASING_STOCK → CREATING_PAYMENT → COMPLETED
```

**Dans le code :**
```python
# Étape 1: Création de commande réussie
if self.current_saga_state == OrderSagaState.CREATING_ORDER:
    self.current_saga_state = self.create_order_handler.run()
    # Retourne: DECREASING_STOCK
    completed_handlers.append(self.create_order_handler)

# Étape 2: Diminution de stock réussie  
elif self.current_saga_state == OrderSagaState.DECREASING_STOCK:
    self.current_saga_state = self.decrease_stock_handler.run()
    # Retourne: CREATING_PAYMENT
    completed_handlers.append(self.decrease_stock_handler)

# Étape 3: Paiement réussi
elif self.current_saga_state == OrderSagaState.CREATING_PAYMENT:
    self.current_saga_state = self.create_payment_handler.run()
    # Retourne: COMPLETED ✅
    completed_handlers.append(self.create_payment_handler)

# Fin: Saga terminée avec succès
# Résultat: {"order_id": 123, "status": "OK"}
```

**Important :** Dans le flux normal, les états `INCREASING_STOCK` et `CANCELLING_ORDER` ne sont **jamais utilisés**. Ils existent uniquement pour la compensation.

### ❌ Flux de Compensation (Rollback) - Une étape échoue

#### Scénario A : Échec du paiement
```
CREATING_ORDER ✅ → DECREASING_STOCK ✅ → CREATING_PAYMENT ❌ → INCREASING_STOCK → CANCELLING_ORDER → COMPLETED
```

**Dans le code :**
```python
# Étapes 1-2: Réussies et ajoutées à completed_handlers
# Étape 3: Paiement échoue
elif self.current_saga_state == OrderSagaState.CREATING_PAYMENT:
    self.current_saga_state = self.create_payment_handler.run()
    # En cas d'erreur, retourne: INCREASING_STOCK

# Compensation 1: Remettre le stock
elif self.current_saga_state == OrderSagaState.INCREASING_STOCK:
    if self.decrease_stock_handler:
        self.current_saga_state = self.decrease_stock_handler.rollback()
        # Retourne: CANCELLING_ORDER
    else:
        self.current_saga_state = OrderSagaState.CANCELLING_ORDER

# Compensation 2: Annuler la commande
elif self.current_saga_state == OrderSagaState.CANCELLING_ORDER:
    if self.create_order_handler:
        self.current_saga_state = self.create_order_handler.rollback()
        # Retourne: COMPLETED
    else:
        self.current_saga_state = OrderSagaState.COMPLETED
    self.is_error_occurred = True

# Résultat: {"order_id": 0, "status": "Une erreur s'est produite..."}
```

#### Scénario B : Échec du stock
```
CREATING_ORDER ✅ → DECREASING_STOCK ❌ → CANCELLING_ORDER → COMPLETED
```

Si la diminution de stock échoue, on passe directement à `CANCELLING_ORDER` pour annuler la commande créée.

### 🛡️ Clause `else` - Gestion d'État Invalide

La clause `else` agit comme un **filet de sécurité** pour gérer les états saga invalides ou corrompus :

```python
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
```

**Pourquoi cette clause existe :**
1. **Protection contre les bugs** : Si un handler retourne un état non prévu
2. **Corruption de données** : Si `current_saga_state` est modifié par erreur
3. **États futurs** : Si de nouveaux états sont ajoutés mais pas gérés
4. **Robustesse** : Évite les boucles infinies dans la saga

**Rollback en cascade :**
- Utilise `reversed(completed_handlers)` pour annuler dans l'ordre inverse
- Try/catch pour chaque rollback (si un rollback échoue, on continue avec les autres)
- Garantit qu'aucune saga ne reste dans un état incohérent

### 📊 Gestion de la Pile de Handlers

La pile `completed_handlers` track les étapes réussies pour permettre un rollback ordonné :

```python
# Stack des handlers complétés pour le rollback
completed_handlers = []

# Ajout uniquement si l'étape réussit
if self.current_saga_state == OrderSagaState.CREATING_ORDER:
    self.current_saga_state = self.create_order_handler.run()
    if self.current_saga_state != OrderSagaState.COMPLETED:  # Si pas d'erreur immédiate
        completed_handlers.append(self.create_order_handler)

# En cas d'erreur dans le else, rollback dans l'ordre inverse
for handler in reversed(completed_handlers):
    handler.rollback()
```

**Ordre de rollback :**
1. Last In, First Out (LIFO)
2. Payment → Stock → Order
3. Compense exactement les actions effectuées

## 6. Instrumentation OpenTelemetry & Jaeger

Le code a été enrichi de spans (trace) pour obtenir une visibilité complète :

- Endpoint `/saga/order` dans `src/saga_orchestrator.py` : span `saga_order` (span racine pour la requête HTTP entrante).
- Controller : span principal `order_saga_execution` qui entoure toute la boucle de la saga.
  - Attributs ajoutés : `user_id`, `items_count`, `current_state`, `final_state`, `error_occurred`, `order_id`, `saga_status`.
- Spans pour chaque étape (donnés dans `OrderSagaController`): `create_order`, `decrease_stock`, `create_payment`, `rollback_decrease_stock`, `rollback_create_order`, `saga_error_handling`.
- Handlers : spans plus détaillés et imbriqués, par ex. :
  - `create_order_handler_run`, `store_api_create_order` (API call)
  - `decrease_stock_handler_run`, `decrease_stock_item_N`, `store_api_decrease_stock` (par item)
  - `create_payment_handler_run`, `get_order_details`, `create_payment_transaction`, `create_payment_handler_rollback`.

Attributs typiques ajoutés aux spans : `order_id`, `payment_id`, `total_amount`, `user_id`, `product_id`, `quantity`, `success`, `error_code`, `error_message`, `failure_step`.

Comment voir les traces :
1. Démarrer Jaeger (ou réutiliser l'instance existante). UI : `http://localhost:16686`.
2. Lancer un appel de test à `/saga/order`.
3. Dans Jaeger UI : rechercher le service `saga-orchestrator` (ou les services instrumentés) et ouvrir la trace pour voir les spans imbriqués et leurs attributs.

> Remarque : KrakenD doit être configuré pour laisser passer les headers de tracing (ex. ajouter `"input_headers": ["*"]` à chaque endpoint) et pour exporter OTLP vers Jaeger si vous souhaitez visualiser les traces côté gateway.

## 6. Exemple concret avec réponses

### ✅ Cas de succès complet
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 1, "quantity": 1}]}' \
  http://localhost:5123/saga/order

# Réponse attendue: {"order_id": 123, "status": "OK"}
# Résultat: Commande créée, stock diminué, paiement effectué
```

**Traces Jaeger attendues :**
- `saga_order` → `order_saga_execution` → `create_order` → `decrease_stock` → `create_payment`
- États visibles : `CREATING_ORDER` → `DECREASING_STOCK` → `CREATING_PAYMENT` → `COMPLETED`

### ❌ Cas d'échec avec rollback
```bash
# Test avec un utilisateur inexistant (devrait faire échouer le paiement)
curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 999999, "items": [{"product_id": 1, "quantity": 1}]}' \
  http://localhost:5123/saga/order

# Réponse attendue: {"order_id": 0, "status": "Une erreur s'est produite..."}
# Résultat: Commande annulée, stock restauré, pas de paiement
```

**Traces Jaeger attendues :**
- `saga_order` → `order_saga_execution` → `create_order` → `decrease_stock` → `create_payment` → `rollback_decrease_stock` → `rollback_create_order`
- États visibles : `CREATING_ORDER` → `DECREASING_STOCK` → `CREATING_PAYMENT` → `INCREASING_STOCK` → `CANCELLING_ORDER` → `COMPLETED`

### 🚨 Cas d'état invalide (théorique)
Si un bug introduit un état non géré, la clause `else` se déclenche :

**Traces Jaeger attendues :**
- `saga_error_handling` span avec rollback en cascade
- Log d'erreur : "L'état saga n'est pas valide : [ÉTAT_INCONNU]"

## 8. Points de vigilance / debug

- Docker : si plusieurs stacks démarrent Jaeger, vous aurez un conflit de nom de container `/jaeger`. Solution : arrêter l'autre Jaeger (`docker stop <container>` / `docker rm <container>`) ou réutiliser l'instance existante (supprimer la définition Jaeger dans `docker-compose.yml` du repo courant).
- KrakenD : assurez-vous que les endpoints `/store-api/...` et `/payments-api/...` existent et transmettent les headers de tracing.
- Données stock : le stock peut être authoritative dans Redis ou MySQL selon votre setup — surveiller les erreurs de synchronisation et logs.
- Fields payload : la `payments-api` attend `total_amount` (pas seulement `amount`), vérifier les schémas d'API lors des erreurs.

## 9. Prochaines étapes recommandées

- Instrumenter de la même façon les microservices `store_manager` et `payments_api` pour obtenir une trace distribuée complète.
- Mettre à jour la configuration KrakenD (OTLP export + input_headers) si vous voulez que la gateway apparaisse dans les traces.
- Ajouter tests automatisés (unitaires et d'intégration) couvrant happy path et cas d'échec (ex. paiement refusé) et vérifier que les spans apparaissent correctement dans Jaeger.

---

Fichiers clés à relire :
- `src/saga_orchestrator.py`
- `src/controllers/order_saga_controller.py`
- `src/handlers/create_order_handler.py`
- `src/handlers/decrease_stock_handler.py`
- `src/handlers/create_payment_handler.py`

Ce document est un point d'entrée pour comprendre la logique et l'observabilité de la saga. Si tu veux, je peux générer un diagramme de séquence UML (PlantUML) ou ajouter des extraits de trace Jaeger exemples.
