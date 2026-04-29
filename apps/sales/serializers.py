from decimal import Decimal
from rest_framework import serializers
from apps.products.sanitizers import sanitize_text
from apps.products.models import Product
from apps.customers.models import Customer
from .models import SalesOrder, SalesOrderLine, Invoice, InvoicePayment


# ═══════════════════════════════════════════════════════════════════
# SALES ORDER LINE SERIALIZER
# ═══════════════════════════════════════════════════════════════════

class SalesOrderLineSerializer(serializers.ModelSerializer):
    """
    Serializer for individual order lines.

    SECURITY — PRICE LOCKDOWN:
    ┌──────────────────────────────────────────────────────────────┐
    │  unit_price and line_total are READ-ONLY.                   │
    │  They are NEVER accepted from the frontend.                 │
    │  The backend fetches Product.price directly from the DB     │
    │  at confirmation time (in services.confirm_sales_order).    │
    │                                                             │
    │  Even if a malicious client includes unit_price in the      │
    │  JSON payload, DRF will silently ignore it because it is    │
    │  listed in read_only_fields.                                │
    └──────────────────────────────────────────────────────────────┘
    """
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = SalesOrderLine
        fields = [
            'id', 'product', 'product_name', 'product_sku',
            'quantity', 'unit_price', 'line_total',
        ]
        read_only_fields = ['id', 'unit_price', 'line_total']

    def validate_product(self, value):
        """
        SECURITY — Anti-ID Crossing:
        Validates that the product belongs to the same tenant as the
        authenticated user, preventing cross-tenant data manipulation.
        """
        user = self.context['request'].user
        if value.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                "El producto seleccionado no pertenece a su empresa. "
                "Acceso denegado."
            )
        if not value.is_active:
            raise serializers.ValidationError(
                "El producto seleccionado está desactivado."
            )
        return value

    def validate_quantity(self, value):
        """Ensure quantity is strictly positive."""
        if value <= 0:
            raise serializers.ValidationError(
                "La cantidad debe ser mayor a cero."
            )
        return value


# ═══════════════════════════════════════════════════════════════════
# SALES ORDER SERIALIZER
# ═══════════════════════════════════════════════════════════════════

class SalesOrderSerializer(serializers.ModelSerializer):
    """
    Serializer for SalesOrder with nested lines (writable on create).

    SECURITY:
    - customer validated for tenant ownership (Anti-ID Crossing)
    - created_by is read-only and auto-assigned
    - subtotal, tax_amount, total are read-only (computed server-side)
    - status transitions enforced in ViewSet actions, not via direct update
    """
    lines = SalesOrderLineSerializer(many=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True
    )

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'customer', 'customer_name', 'order_number',
            'status', 'notes', 'tax_rate',
            'subtotal', 'tax_amount', 'total',
            'lines',
            'created_by', 'created_by_username',
            'confirmed_at', 'cancelled_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'order_number', 'status',
            'subtotal', 'tax_amount', 'total',
            'created_by', 'confirmed_at', 'cancelled_at',
            'created_at', 'updated_at',
        ]

    # ─── AUDIT: Immutable created_by ──────────────────────────────
    def validate_created_by(self, value):
        """
        SECURITY: Reject any client-supplied created_by.
        This field is always auto-assigned from request.user.
        """
        raise serializers.ValidationError(
            "El campo 'created_by' es inmutable y se asigna automáticamente."
        )

    # ─── Anti-ID Crossing: Customer Ownership ─────────────────────
    def validate_customer(self, value):
        """
        SECURITY: Validates that the customer belongs to the same tenant
        as the authenticated user, preventing cross-tenant data access.
        """
        user = self.context['request'].user
        if value.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                "El cliente seleccionado no pertenece a su empresa. "
                "Acceso denegado."
            )
        if not value.is_active:
            raise serializers.ValidationError(
                "El cliente seleccionado está desactivado."
            )
        return value

    # ─── XSS Sanitization ─────────────────────────────────────────
    def validate_notes(self, value):
        """Sanitize notes against XSS."""
        return sanitize_text(value)

    def validate_tax_rate(self, value):
        """Validate tax rate is between 0 and 1."""
        if value < 0 or value > 1:
            raise serializers.ValidationError(
                "La tasa de impuesto debe estar entre 0 y 1 (ej: 0.16 = 16%)."
            )
        return value

    def validate_lines(self, value):
        """Ensure at least one line is provided."""
        if not value:
            raise serializers.ValidationError(
                "La orden debe tener al menos una línea de producto."
            )
        return value

    def create(self, validated_data):
        """
        Create the order and its nested lines in a single operation.
        Lines are created as-is; unit_price will be populated at confirmation.
        """
        lines_data = validated_data.pop('lines')
        order = SalesOrder.objects.create(**validated_data)
        for line_data in lines_data:
            SalesOrderLine.objects.create(
                order=order,
                tenant=order.tenant,
                **line_data,
            )
        return order

    def update(self, instance, validated_data):
        """
        Update order fields and replace lines if provided.
        Only allowed when status is 'draft' (enforced at ViewSet level).
        """
        lines_data = validated_data.pop('lines', None)

        # Update order scalar fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Replace lines if provided
        if lines_data is not None:
            instance.lines.all().delete()
            for line_data in lines_data:
                SalesOrderLine.objects.create(
                    order=instance,
                    tenant=instance.tenant,
                    **line_data,
                )

        return instance


class SalesOrderListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views (no nested lines).
    """
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    line_count = serializers.IntegerField(source='lines.count', read_only=True)
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True
    )

    class Meta:
        model = SalesOrder
        fields = [
            'id', 'customer', 'customer_name', 'order_number',
            'status', 'subtotal', 'total', 'line_count',
            'created_by_username', 'created_at',
        ]
        read_only_fields = fields


# ═══════════════════════════════════════════════════════════════════
# INVOICE SERIALIZER
# ═══════════════════════════════════════════════════════════════════

class InvoiceSerializer(serializers.ModelSerializer):
    """
    Serializer for Invoice.

    SECURITY — IMMUTABILITY:
    ┌──────────────────────────────────────────────────────────────┐
    │  Once an invoice reaches 'issued' or 'paid' status,        │
    │  ALL modifications are BLOCKED.                             │
    │                                                             │
    │  This is enforced at TWO levels:                            │
    │  1. ViewSet: update/partial_update check is_locked          │
    │  2. Serializer: validate() rejects updates on locked state  │
    │                                                             │
    │  Defense-in-depth: even if one layer is bypassed, the       │
    │  other will catch the attempt.                              │
    └──────────────────────────────────────────────────────────────┘
    """
    customer_name = serializers.CharField(
        source='sales_order.customer.name', read_only=True
    )
    order_number = serializers.CharField(
        source='sales_order.order_number', read_only=True
    )
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True
    )
    payments_summary = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'sales_order', 'order_number', 'customer_name',
            'invoice_number', 'status',
            'subtotal', 'tax_rate', 'tax_amount', 'total', 'amount_paid',
            'payments_summary',
            'issued_at', 'paid_at',
            'created_by', 'created_by_username',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'sales_order', 'invoice_number', 'status',
            'subtotal', 'tax_rate', 'tax_amount', 'total', 'amount_paid',
            'issued_at', 'paid_at',
            'created_by', 'created_at', 'updated_at',
        ]

    def get_payments_summary(self, obj):
        """Return summary of payments for this invoice."""
        payments = obj.payments.all()
        return {
            'count': payments.count(),
            'total_paid': sum(p.amount for p in payments),
            'remaining': obj.total - obj.amount_paid,
        }

    def validate(self, attrs):
        """
        SECURITY: Block any update attempt on locked invoices.
        This is a defense-in-depth layer alongside ViewSet checks.
        """
        if self.instance and self.instance.is_locked:
            raise serializers.ValidationError(
                "Esta factura está emitida o pagada y no puede ser modificada."
            )
        return attrs


# ═══════════════════════════════════════════════════════════════════
# INVOICE PAYMENT SERIALIZER
# ═══════════════════════════════════════════════════════════════════

class InvoicePaymentSerializer(serializers.ModelSerializer):
    """
    Serializer for InvoicePayment (create + read only, immutable).

    SECURITY:
    - created_by is read-only and auto-assigned
    - Validates that invoice belongs to the user's tenant
    - Validates that payment does not exceed invoice total
    """
    invoice_number = serializers.CharField(
        source='invoice.invoice_number', read_only=True
    )
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True
    )

    class Meta:
        model = InvoicePayment
        fields = [
            'id', 'invoice', 'invoice_number',
            'amount', 'payment_method', 'payment_date',
            'reference', 'notes',
            'created_by', 'created_by_username', 'created_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_at']

    # ─── AUDIT: Immutable created_by ──────────────────────────────
    def validate_created_by(self, value):
        """SECURITY: Reject any client-supplied created_by."""
        raise serializers.ValidationError(
            "El campo 'created_by' es inmutable y se asigna automáticamente."
        )

    # ─── Anti-ID Crossing: Invoice Ownership ──────────────────────
    def validate_invoice(self, value):
        """Validate invoice belongs to the user's tenant."""
        user = self.context['request'].user
        if value.tenant_id != user.tenant_id:
            raise serializers.ValidationError(
                "La factura seleccionada no pertenece a su empresa."
            )
        if value.status == Invoice.Status.CANCELLED:
            raise serializers.ValidationError(
                "No se puede registrar un pago en una factura cancelada."
            )
        if value.status == Invoice.Status.PAID:
            raise serializers.ValidationError(
                "Esta factura ya está completamente pagada."
            )
        return value

    def validate_amount(self, value):
        """Ensure amount is positive."""
        if value <= 0:
            raise serializers.ValidationError(
                "El monto del pago debe ser mayor a cero."
            )
        return value

    # ─── XSS Sanitization ─────────────────────────────────────────
    def validate_reference(self, value):
        return sanitize_text(value)

    def validate_notes(self, value):
        return sanitize_text(value)

    def validate(self, attrs):
        """Cross-field: ensure payment doesn't exceed remaining balance."""
        invoice = attrs.get('invoice')
        amount = attrs.get('amount', Decimal('0'))

        if invoice:
            remaining = invoice.total - invoice.amount_paid
            if amount > remaining:
                raise serializers.ValidationError({
                    'amount': (
                        f"El pago excede el saldo pendiente. "
                        f"Saldo: ${remaining}, Pago: ${amount}"
                    )
                })

        return attrs
