# Question 6 : Orchestrateur Saga vs Appels Directs aux Services

## Vue d'ensemble

Cette question compare deux approches pour gérer les transactions distribuées :
1. **Orchestration Saga** : Un orchestrateur central coordonne toutes les étapes
2. **Appels Directs** : Le client appelle chaque service individuellement

## 1. Approche avec Orchestrateur Saga

### 🎯 Fonctionnement

L'orchestrateur Saga (`saga_orchestrator`) coordonne automatiquement :
- Création de commande (Store Manager)
- Diminution de stock (Store Manager)
- Création de paiement (Payments API)
- **Rollback automatique** en cas d'échec

### 📝 Exemple d'appel

```bash
# UN SEUL appel au client
curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 10, "quantity": 2}]}' \
  http://localhost:5123/saga/order
```

**Réponse :**
```json
{"order_id": 123, "status": "OK"}
```

### 🔍 Code de l'orchestrateur (extrait)

```python
def run(self, request):
    """Exécute automatiquement toutes les étapes"""
    while self.current_saga_state is not OrderSagaState.COMPLETED:
        if self.current_saga_state == OrderSagaState.CREATING_ORDER:
            self.current_saga_state = self.create_order_handler.run()
            
        elif self.current_saga_state == OrderSagaState.DECREASING_STOCK:
            self.current_saga_state = self.decrease_stock_handler.run()
            
        elif self.current_saga_state == OrderSagaState.CREATING_PAYMENT:
            self.current_saga_state = self.create_payment_handler.run()
            
        # Gestion automatique des rollbacks
        elif self.current_saga_state == OrderSagaState.INCREASING_STOCK:
            self.current_saga_state = self.decrease_stock_handler.rollback()
```

### ✅ Avantages de l'Orchestration Saga

1. **Simplicité client** : Un seul appel déclenche toute la transaction
2. **Cohérence garantie** : Rollback automatique en cas d'échec
3. **Observabilité centralisée** : Toutes les traces dans Jaeger
4. **Gestion d'erreurs robuste** : Logic de compensation intégrée
5. **État centralisé** : Machine à états claire et prévisible

### ❌ Inconvénients de l'Orchestration Saga

1. **Point de défaillance unique** : Si l'orchestrateur tombe, tout s'arrête
2. **Latence supplémentaire** : Passage par un service intermédiaire
3. **Complexité d'implémentation** : Machine à états sophistiquée
4. **Couplage** : L'orchestrateur doit connaître tous les services

## 2. Approche avec Appels Directs

### 🎯 Fonctionnement

Le client doit manuellement :
1. Appeler Store Manager pour créer la commande
2. Appeler Store Manager pour diminuer le stock
3. Appeler Payments API pour le paiement
4. **Gérer manuellement les rollbacks** en cas d'échec

### 📝 Exemple d'appels (séquence complète)

```bash
# ÉTAPE 1: Créer la commande
RESPONSE1=$(curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 10, "quantity": 2}]}' \
  http://localhost:8080/store-api/orders)

ORDER_ID=$(echo $RESPONSE1 | jq -r '.order_id')

# ÉTAPE 2: Diminuer le stock
curl -X POST -H "Content-Type: application/json" \
  -d '{"product_id": 10, "quantity": -2}' \
  http://localhost:8080/store-api/stocks

# ÉTAPE 3: Créer le paiement
curl -X POST -H "Content-Type: application/json" \
  -d '{"order_id": '$ORDER_ID', "user_id": 1, "total_amount": 150}' \
  http://localhost:8080/payments-api/payments

# EN CAS D'ÉCHEC: ROLLBACK MANUEL
# Remettre le stock
curl -X POST -H "Content-Type: application/json" \
  -d '{"product_id": 10, "quantity": 2}' \
  http://localhost:8080/store-api/stocks

# Supprimer la commande
curl -X DELETE http://localhost:8080/store-api/orders/$ORDER_ID
```

### 💻 Code client (exemple)

```python
def create_order_manual(user_id, items):
    """Gestion manuelle de la transaction distribuée"""
    order_id = None
    stock_updated = False
    
    try:
        # Étape 1: Créer commande
        order_response = requests.post(
            f"{API_GATEWAY}/store-api/orders",
            json={"user_id": user_id, "items": items}
        )
        order_id = order_response.json()["order_id"]
        
        # Étape 2: Diminuer stock
        for item in items:
            stock_response = requests.post(
                f"{API_GATEWAY}/store-api/stocks",
                json={"product_id": item["product_id"], "quantity": -item["quantity"]}
            )
            if not stock_response.ok:
                raise Exception("Stock insufficient")
        stock_updated = True
        
        # Étape 3: Créer paiement
        payment_response = requests.post(
            f"{API_GATEWAY}/payments-api/payments",
            json={"order_id": order_id, "user_id": user_id, "total_amount": 150}
        )
        if not payment_response.ok:
            raise Exception("Payment failed")
            
        return {"order_id": order_id, "status": "OK"}
        
    except Exception as e:
        # ROLLBACK MANUEL
        if stock_updated:
            for item in items:
                requests.post(
                    f"{API_GATEWAY}/store-api/stocks",
                    json={"product_id": item["product_id"], "quantity": item["quantity"]}
                )
        if order_id:
            requests.delete(f"{API_GATEWAY}/store-api/orders/{order_id}")
        
        return {"order_id": 0, "status": f"Error: {str(e)}"}
```

### ✅ Avantages des Appels Directs

1. **Performance** : Pas de latence d'orchestrateur
2. **Simplicité d'architecture** : Pas de service supplémentaire
3. **Flexibilité** : Le client contrôle totalement la logique
4. **Pas de SPOF** : Pas de point de défaillance unique
5. **Couplage faible** : Services indépendants

### ❌ Inconvénients des Appels Directs

1. **Complexité client** : Logique de rollback dans chaque client
2. **Risque d'incohérence** : Erreurs de rollback = données corrompues
3. **Code dupliqué** : Chaque client doit implémenter la même logique
4. **Observabilité difficile** : Traces dispersées entre services
5. **Gestion d'erreurs complexe** : Rollbacks partiels, timeouts, etc.
6. **Navigation Jaeger complexe** : Traces non corrélées entre services
7. **Tests Postman fragmentés** : Multiple requêtes à orchestrer manuellement
8. **Debugging difficile** : Reconstitution manuelle du flux de transaction

## 3. Comparaison Pratique avec Jaeger

### 🔍 Traces Saga (Orchestrateur)

Dans Jaeger, pour l'orchestrateur Saga :
```
saga_order (204ms)
└── order_saga_execution (195ms)
    ├── create_order (45ms)
    ├── decrease_stock (38ms)
    │   ├── decrease_stock_item_0 (35ms)
    │   └── store_api_decrease_stock (32ms)
    └── create_payment (89ms)
        ├── get_order_details (42ms)
        └── create_payment_transaction (44ms)
```

**Attributs visibles :**
- `user_id: 1`
- `product_0_id: 10`, `product_0_quantity: 2`
- `success: true`
- `order_id: 123`

**Avantages Jaeger avec Saga :**
- **Vue unifiée** : Une seule trace contient toute la transaction
- **Navigation simple** : Sélectionner `saga-orchestrator` → voir tout le flux
- **Corrélation automatique** : Tous les spans sont liés hiérarchiquement
- **Debugging facile** : Identifier rapidement l'étape qui échoue

### 🔍 Traces Appels Directs

Pour les appels directs, vous auriez des traces séparées :
```
store-manager: POST /orders (45ms)
store-manager: POST /stocks (38ms)  
payments-api: POST /payments (44ms)
```

**Problème :** Aucune corrélation entre les traces !

### 🚧 Difficultés d'Observabilité avec Appels Directs

#### Navigation Jaeger Complexe
Avec les appels directs, l'observabilité devient un cauchemar :

1. **Services dispersés** : Vous devez naviguer entre plusieurs services dans Jaeger
   - Sélectionner `store-manager` → chercher trace de création commande
   - Sélectionner `store-manager` → chercher trace de diminution stock  
   - Sélectionner `payments-api` → chercher trace de paiement
   - **Aucun lien visuel** entre ces 3 traces !

2. **Corrélation manuelle** : Vous devez manuellement :
   - Noter les timestamps de chaque trace
   - Identifier les traces qui appartiennent à la même transaction
   - Reconstruire mentalement le flux complet

3. **Jaeger "All in One" nécessaire** :
   ```
   # Dans Jaeger UI, vous devez faire :
   Service: store-manager → Find Traces → POST /orders
   Service: store-manager → Find Traces → POST /stocks  
   Service: payments-api → Find Traces → POST /payments
   
   # Puis essayer de deviner quelles traces vont ensemble !
   ```

#### Postman Collections Fragmentées
Avec les appels directs, vos tests Postman deviennent :

```json
{
  "name": "Manual Transaction",
  "item": [
    {
      "name": "1. Create Order",
      "request": {"method": "POST", "url": "{{gateway}}/store-api/orders"}
    },
    {
      "name": "2. Decrease Stock", 
      "request": {"method": "POST", "url": "{{gateway}}/store-api/stocks"}
    },
    {
      "name": "3. Create Payment",
      "request": {"method": "POST", "url": "{{gateway}}/payments-api/payments"}
    },
    {
      "name": "4. ROLLBACK - Increase Stock",
      "request": {"method": "POST", "url": "{{gateway}}/store-api/stocks"}
    },
    {
      "name": "5. ROLLBACK - Delete Order",
      "request": {"method": "DELETE", "url": "{{gateway}}/store-api/orders/{{order_id}}"}
    }
  ]
}
```

**Problèmes :**
- **Tests multiples** : 5 requêtes au lieu d'une seule
- **Variables partagées** : Passer `order_id` entre requêtes
- **Tests de rollback** : Impossible de tester automatiquement les échecs
- **Maintenance complexe** : Changer un endpoint = modifier plusieurs tests

## 4. Démonstration Pratique

### Test Orchestrateur Saga

```bash
# Test de succès
curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 10, "quantity": 1}]}' \
  http://localhost:5123/saga/order

# Résultat: {"order_id": 123, "status": "OK"}
# Dans Jaeger: Une trace complète avec tous les spans
```

### Test Appels Directs

```bash
# Test équivalent (3 appels séparés)
curl -X POST -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 10, "quantity": 1}]}' \
  http://localhost:8080/store-api/orders

curl -X POST -H "Content-Type: application/json" \
  -d '{"product_id": 10, "quantity": -1}' \
  http://localhost:8080/store-api/stocks

curl -X POST -H "Content-Type: application/json" \
  -d '{"order_id": 124, "user_id": 1, "total_amount": 75}' \
  http://localhost:8080/payments-api/payments

# Résultat: 3 réponses séparées
# Dans Jaeger: 3 traces disconnectées
```

## 5. Recommandations

### 🎯 Utilisez l'Orchestrateur Saga quand :

- **Cohérence critique** : E-commerce, banking, réservations
- **Transactions complexes** : Plusieurs étapes avec dépendances
- **Observabilité importante** : Debugging et monitoring essentiels
- **Équipes multiples** : Logique centralisée pour éviter la duplication

### 🎯 Utilisez les Appels Directs quand :

- **Performance critique** : Latence très faible requise
- **Transactions simples** : Peu d'étapes, peu de risque d'échec
- **Autonomie des équipes** : Chaque équipe contrôle sa logique
- **Systèmes legacy** : Difficile d'ajouter un orchestrateur

## 6. Conclusion

L'**Orchestrateur Saga** est généralement préférable pour les transactions métier critiques car il garantit la cohérence et simplifie la logique client, même si cela introduit une latence et complexité supplémentaires.

Les **Appels Directs** conviennent mieux pour des opérations simples où la performance prime sur la robustesse transactionnelle.

### Impact sur l'Expérience Développeur

**Avec Orchestrateur Saga :**
- **Jaeger** : Clic sur `saga-orchestrator` → voir toute la transaction en un coup d'œil
- **Postman** : Une requête simple pour tester le scenario complet
- **Debugging** : Erreur visible immédiatement avec contexte complet

**Avec Appels Directs :**
- **Jaeger** : Navigation fastidieuse entre 3+ services pour reconstituer une transaction
- **Postman** : Collections complexes avec 5+ requêtes et gestion de variables
- **Debugging** : Chasse aux indices à travers plusieurs traces déconnectées

Dans notre contexte de commande e-commerce, l'orchestrateur Saga est le choix approprié car :
- Cohérence financière critique
- Logique de compensation complexe
- Observabilité essentielle pour le debugging
- Évite la duplication de code entre clients
- **Expérience développeur simplifiée** pour le monitoring et les tests