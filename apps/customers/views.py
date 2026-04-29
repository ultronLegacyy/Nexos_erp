from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.users.permissions import IsStaff
from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(viewsets.ModelViewSet):
    """
    CRUD ViewSet for Customers.

    - Queryset is automatically filtered by tenant via TenantManager.
    - Only staff+ roles can create/update/delete.
    - perform_create assigns the tenant from the authenticated user.
    - DELETE performs a soft-delete (deactivate).
    """
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['name', 'email', 'tax_id', 'phone']
    ordering_fields = ['name', 'created_at']

    def get_queryset(self):
        """Return customers for the current tenant."""
        qs = Customer.objects.all()
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
