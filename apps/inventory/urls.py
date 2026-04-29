from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InventoryViewSet, InventoryMovementViewSet

router = DefaultRouter()
router.register(r'stock', InventoryViewSet, basename='inventory-stock')
router.register(r'movements', InventoryMovementViewSet, basename='inventory-movement')

# Backward-compatible alias
router.register(r'transactions', InventoryMovementViewSet, basename='inventory-transaction')

urlpatterns = [
    path('', include(router.urls)),
]
