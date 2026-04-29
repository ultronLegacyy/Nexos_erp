from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SalesOrderViewSet, InvoiceViewSet, InvoicePaymentViewSet

router = DefaultRouter()
router.register(r'orders', SalesOrderViewSet, basename='sales-order')
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'payments', InvoicePaymentViewSet, basename='invoice-payment')

urlpatterns = [
    path('', include(router.urls)),
]
