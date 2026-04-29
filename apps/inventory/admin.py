from django.contrib import admin
from .models import Inventory, InventoryMovement


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = (
        'product', 'quantity_on_hand', 'last_movement_at',
        'tenant', 'updated_at',
    )
    list_filter = ('tenant',)
    search_fields = ('product__name', 'product__sku')
    readonly_fields = ('id', 'updated_at', 'last_movement_at')


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = (
        'product', 'movement_type', 'quantity',
        'quantity_before', 'quantity_after',
        'reference', 'created_by', 'created_at', 'tenant',
    )
    list_filter = ('movement_type', 'tenant', 'created_at')
    search_fields = ('product__name', 'product__sku', 'reference')
    readonly_fields = (
        'id', 'created_at', 'created_by',
        'quantity_before', 'quantity_after',
    )
    date_hierarchy = 'created_at'

    def has_change_permission(self, request, obj=None):
        """Movements are immutable — no editing allowed."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Movements are immutable — no deletion allowed."""
        return False
