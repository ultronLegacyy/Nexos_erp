from rest_framework import serializers
from .models import Category, Product
from .sanitizers import sanitize_text


class CategorySerializer(serializers.ModelSerializer):
    """
    Serializer for Category with:
    - XSS sanitization on name and description
    - Read-only tenant assignment (set in view's perform_create)
    """
    product_count = serializers.IntegerField(source='products.count', read_only=True)

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'description', 'is_active',
            'created_at', 'updated_at', 'product_count',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        """Sanitize name against XSS and validate tenant-scoped uniqueness."""
        cleaned = sanitize_text(value)
        if not cleaned:
            raise serializers.ValidationError("El nombre no puede estar vacío después de la sanitización.")

        # Check uniqueness within the tenant
        user = self.context['request'].user
        qs = Category.original_objects.filter(tenant=user.tenant, name=cleaned)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe una categoría con este nombre en su empresa.")
        return cleaned

    def validate_description(self, value):
        """Sanitize description against XSS."""
        return sanitize_text(value)


class ProductSerializer(serializers.ModelSerializer):
    """
    Serializer for Product with:
    - Cross-tenant validation on category_id (Anti-ID Crossing)
    - XSS sanitization on name and description
    - Tenant-scoped SKU uniqueness validation
    """
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'category', 'category_name', 'name', 'description',
            'sku', 'price', 'stock', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    # ─── Anti-ID Crossing: Category Ownership Validation ──────────────
    def validate_category(self, value):
        """
        SECURITY: Validates that the category belongs to the same tenant
        as the authenticated user. This prevents 'ID Crossing' attacks where
        a malicious user sends a category_id from another tenant.
        """
        user = self.context['request'].user
        if value.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                "La categoría seleccionada no pertenece a su empresa. "
                "Acceso denegado."
            )
        if not value.is_active:
            raise serializers.ValidationError(
                "La categoría seleccionada está desactivada."
            )
        return value

    # ─── Tenant-Scoped SKU Uniqueness ─────────────────────────────────
    def validate_sku(self, value):
        """
        SECURITY: Validates SKU uniqueness within the user's tenant only.
        Uses original_objects (unfiltered manager) to explicitly check
        against the specific tenant, avoiding false negatives.
        """
        user = self.context['request'].user
        qs = Product.original_objects.filter(tenant=user.tenant, sku=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Ya existe un producto con este SKU en su empresa."
            )
        return value

    # ─── XSS Sanitization ─────────────────────────────────────────────
    def validate_name(self, value):
        """Sanitize product name against XSS."""
        cleaned = sanitize_text(value)
        if not cleaned:
            raise serializers.ValidationError(
                "El nombre no puede estar vacío después de la sanitización."
            )
        return cleaned

    def validate_description(self, value):
        """Sanitize product description against XSS."""
        return sanitize_text(value)

    def validate_price(self, value):
        """Ensure price is positive."""
        if value <= 0:
            raise serializers.ValidationError("El precio debe ser mayor a cero.")
        return value
