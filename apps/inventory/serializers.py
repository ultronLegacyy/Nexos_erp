from rest_framework import serializers
from apps.products.sanitizers import sanitize_text
from apps.products.models import Product
from .models import Inventory, InventoryMovement


class InventorySerializer(serializers.ModelSerializer):
    """Read-only serializer for the Inventory (quantity_on_hand) view."""
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = Inventory
        fields = [
            'id', 'product', 'product_name', 'product_sku',
            'quantity_on_hand', 'last_movement_at', 'updated_at',
        ]
        read_only_fields = fields  # Entirely read-only; mutations go through movements


class InventoryMovementSerializer(serializers.ModelSerializer):
    """
    Serializer for InventoryMovement with:
    - Cross-tenant product validation (Anti-ID Crossing)
    - Immutable created_by (auto-assigned, rejected if sent by client)
    - Sanitization of reference and notes fields
    - Early stock sufficiency check for outbound movements
    """
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True
    )

    class Meta:
        model = InventoryMovement
        fields = [
            'id', 'product', 'product_name', 'product_sku',
            'movement_type', 'quantity',
            'quantity_before', 'quantity_after',
            'reference', 'notes',
            'created_by', 'created_by_username', 'created_at',
        ]
        read_only_fields = [
            'id', 'created_by', 'created_at',
            'quantity_before', 'quantity_after',
        ]

    # ─── AUDIT: Immutable created_by ──────────────────────────────
    def validate_created_by(self, value):
        """
        SECURITY: Reject any client-supplied created_by.
        This field is always auto-assigned from request.user in perform_create().
        Even if the client sends a created_by value, it is ignored because
        the field is in read_only_fields. This validator is an extra safety net.
        """
        raise serializers.ValidationError(
            "El campo 'created_by' es inmutable y se asigna automáticamente."
        )

    # ─── Anti-ID Crossing: Product Ownership Validation ───────────
    def validate_product(self, value):
        """
        SECURITY: Validates that the product belongs to the same tenant
        as the authenticated user, preventing cross-tenant data access.
        """
        user = self.context['request'].user
        if value.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                "El producto seleccionado no pertenece a su empresa."
            )
        if not value.is_active:
            raise serializers.ValidationError(
                "El producto seleccionado está desactivado."
            )
        return value

    # ─── XSS Sanitization ─────────────────────────────────────────
    def validate_reference(self, value):
        """Sanitize reference field against XSS."""
        return sanitize_text(value)

    def validate_notes(self, value):
        """Sanitize notes field against XSS."""
        return sanitize_text(value)

    def validate_quantity(self, value):
        """Ensure quantity is strictly positive."""
        if value <= 0:
            raise serializers.ValidationError(
                "La cantidad debe ser mayor a cero."
            )
        return value

    def validate(self, attrs):
        """
        Cross-field validation: early stock sufficiency check.

        NOTE: This is an optimistic pre-check for better UX. The definitive
        stock validation happens UNDER THE LOCK in perform_create() to prevent
        race conditions. This avoids unnecessary locking for obviously invalid requests.
        """
        movement_type = attrs.get('movement_type')
        quantity = attrs.get('quantity', 0)
        product = attrs.get('product')

        if movement_type in ('outbound', 'sale') and product:
            # Read current stock (optimistic, no lock)
            try:
                inv = Inventory.original_objects.get(
                    product=product,
                    tenant_id=self.context['request'].user.tenant_id,
                )
                if inv.quantity_on_hand < quantity:
                    raise serializers.ValidationError({
                        'quantity': (
                            f"Stock insuficiente. Disponible: {inv.quantity_on_hand}, "
                            f"Solicitado: {quantity}"
                        )
                    })
            except Inventory.DoesNotExist:
                raise serializers.ValidationError({
                    'product': "Este producto no tiene registro de inventario."
                })

        return attrs


# ─── Backward-compatible alias ────────────────────────────────────
InventoryTransactionSerializer = InventoryMovementSerializer
