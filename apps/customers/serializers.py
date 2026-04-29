from rest_framework import serializers
from apps.products.sanitizers import sanitize_text
from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    """
    Serializer for Customer with:
    - XSS sanitization on all text fields
    - Tenant-scoped uniqueness validation for email and tax_id
    - Read-only tenant assignment (set in view's perform_create)
    """

    class Meta:
        model = Customer
        fields = [
            'id', 'name', 'email', 'phone', 'address',
            'tax_id', 'notes', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    # ─── XSS Sanitization ─────────────────────────────────────────
    def validate_name(self, value):
        """Sanitize name against XSS."""
        cleaned = sanitize_text(value)
        if not cleaned:
            raise serializers.ValidationError(
                "El nombre no puede estar vacío después de la sanitización."
            )
        return cleaned

    def validate_address(self, value):
        """Sanitize address against XSS."""
        return sanitize_text(value)

    def validate_notes(self, value):
        """Sanitize notes against XSS."""
        return sanitize_text(value)

    def validate_phone(self, value):
        """Sanitize phone against XSS."""
        return sanitize_text(value)

    # ─── Tenant-Scoped Uniqueness ─────────────────────────────────
    def validate_email(self, value):
        """Validate email uniqueness within the tenant."""
        if not value:
            return value
        user = self.context['request'].user
        qs = Customer.original_objects.filter(tenant=user.tenant, email=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Ya existe un cliente con este email en su empresa."
            )
        return value

    def validate_tax_id(self, value):
        """Sanitize and validate tax_id uniqueness within the tenant."""
        value = sanitize_text(value)
        if not value:
            return value
        user = self.context['request'].user
        qs = Customer.original_objects.filter(tenant=user.tenant, tax_id=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Ya existe un cliente con este identificador fiscal en su empresa."
            )
        return value
