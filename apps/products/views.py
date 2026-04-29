from rest_framework import viewsets, status, filters
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action

from apps.users.permissions import IsStaff
from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer


class CategoryViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Categories.
    - Queryset is automatically filtered by tenant via TenantManager.
    - Only staff+ roles can create/update/delete.
    - perform_create assigns the tenant from the authenticated user.
    """
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        """Return active categories for the current tenant."""
        qs = Category.objects.all()
        # Optional: filter only active categories for list
        if self.action == 'list':
            show_inactive = self.request.query_params.get('show_inactive', 'false')
            if show_inactive.lower() != 'true':
                qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        """Auto-assign tenant from authenticated user."""
        serializer.save(tenant=self.request.user.tenant)

    def perform_destroy(self, instance):
        """Soft-delete: deactivate instead of deleting."""
        instance.is_active = False
        instance.save(update_fields=['is_active'])


class ProductViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Products.
    - Queryset is automatically filtered by tenant via TenantManager.
    - Supports filtering by category and searching by name/SKU.
    - Only staff+ roles can create/update/delete.
    """
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['name', 'sku', 'description']
    ordering_fields = ['name', 'price', 'stock', 'created_at']

    def get_queryset(self):
        """Return products for the current tenant, with optional category filter."""
        qs = Product.objects.select_related('category').all()

        # Filter by category
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)

        # Filter active/inactive
        if self.action == 'list':
            show_inactive = self.request.query_params.get('show_inactive', 'false')
            if show_inactive.lower() != 'true':
                qs = qs.filter(is_active=True)

        return qs

    def perform_create(self, serializer):
        """Auto-assign tenant from authenticated user."""
        serializer.save(tenant=self.request.user.tenant)

    def perform_destroy(self, instance):
        """Soft-delete: deactivate instead of deleting."""
        instance.is_active = False
        instance.save(update_fields=['is_active'])

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Return products with stock below a threshold (default: 10)."""
        threshold = int(request.query_params.get('threshold', 10))
        products = self.get_queryset().filter(stock__lte=threshold, is_active=True)
        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)
