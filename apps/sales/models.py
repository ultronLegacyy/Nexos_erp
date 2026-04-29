import uuid
from django.db import models
from django.conf import settings
from apps.tenants.models import TenantModel
from apps.customers.models import Customer
from apps.products.models import Product


# ═══════════════════════════════════════════════════════════════════
# SALES ORDER
# ═══════════════════════════════════════════════════════════════════

class SalesOrder(TenantModel):
    """
    A sales order groups one or more product lines for a customer.

    LIFECYCLE:  draft → confirmed → invoiced
                draft → cancelled

    SECURITY:
    - order_number is auto-generated, unique per tenant.
    - created_by is immutable and auto-assigned from request.user.
    - Totals (subtotal, tax_amount, total) are computed server-side,
      NEVER accepted from client input.
    - Only 'draft' orders can be edited or cancelled.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        CONFIRMED = 'confirmed', 'Confirmada'
        INVOICED = 'invoiced', 'Facturada'
        CANCELLED = 'cancelled', 'Cancelada'

    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='sales_orders',
    )
    order_number = models.CharField(
        max_length=50,
        help_text='Auto-generated. Unique per tenant.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    notes = models.TextField(blank=True, default='')

    # ─── Computed totals (server-side only) ────────────────────────
    subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Sum of all line_total values. Computed server-side.',
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=4, default=0,
        help_text='Tax rate as a decimal (e.g. 0.16 = 16%).',
    )
    tax_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='subtotal × tax_rate. Computed server-side.',
    )
    total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='subtotal + tax_amount. Computed server-side.',
    )

    # ─── Audit fields ─────────────────────────────────────────────
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='sales_orders_created',
        help_text='Immutable. Auto-assigned from the authenticated user.',
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sales_orders'
        verbose_name = 'Sales Order'
        verbose_name_plural = 'Sales Orders'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'order_number'],
                name='unique_order_number_per_tenant',
            ),
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=0),
                name='sales_order_subtotal_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=0),
                name='sales_order_total_non_negative',
            ),
        ]

    def __str__(self):
        return f"OV-{self.order_number} ({self.get_status_display()})"


# ═══════════════════════════════════════════════════════════════════
# SALES ORDER LINE
# ═══════════════════════════════════════════════════════════════════

class SalesOrderLine(TenantModel):
    """
    A single product line within a sales order.

    SECURITY — PRICE LOCKDOWN:
    - unit_price is NEVER accepted from the frontend.
    - It is populated server-side from Product.price at confirmation time.
    - line_total = quantity × unit_price is also computed server-side.
    - This prevents price-manipulation attacks where a malicious client
      sends a modified price in the API payload.
    """
    order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='sales_order_lines',
    )
    quantity = models.PositiveIntegerField(
        help_text='Number of units. Must be > 0.',
    )
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Copied from Product.price at confirmation time. NEVER from frontend.',
    )
    line_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='quantity × unit_price. Computed server-side.',
    )

    class Meta:
        db_table = 'sales_order_lines'
        verbose_name = 'Sales Order Line'
        verbose_name_plural = 'Sales Order Lines'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='sales_order_line_quantity_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name='sales_order_line_unit_price_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.product.name} × {self.quantity}"


# ═══════════════════════════════════════════════════════════════════
# INVOICE
# ═══════════════════════════════════════════════════════════════════

class Invoice(TenantModel):
    """
    Invoice generated from a confirmed sales order.

    LIFECYCLE:  draft → issued → paid
                draft → cancelled

    SECURITY — IMMUTABILITY:
    - Once status is 'issued' or 'paid', ALL fields are locked.
    - PUT/PATCH requests are rejected at the ViewSet level.
    - The DB CHECK constraint ensures total >= 0.
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Borrador'
        ISSUED = 'issued', 'Emitida'
        PAID = 'paid', 'Pagada'
        CANCELLED = 'cancelled', 'Cancelada'

    sales_order = models.OneToOneField(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name='invoice',
    )
    invoice_number = models.CharField(
        max_length=50,
        help_text='Auto-generated. Unique per tenant.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # ─── Amounts (copied from the sales order) ────────────────────
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Running total of payments received.',
    )

    # ─── PDF ──────────────────────────────────────────────────────
    pdf_file = models.FileField(
        upload_to='invoices/pdfs/%Y/%m/',
        blank=True,
        null=True,
        help_text='Generated PDF file.',
    )

    # ─── Audit ────────────────────────────────────────────────────
    issued_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='invoices_created',
        help_text='Immutable. Auto-assigned from the authenticated user.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'invoice_number'],
                name='unique_invoice_number_per_tenant',
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=0),
                name='invoice_total_non_negative',
            ),
        ]

    @property
    def is_locked(self):
        """Returns True if the invoice is in an immutable state."""
        return self.status in (self.Status.ISSUED, self.Status.PAID)

    def __str__(self):
        return f"FAC-{self.invoice_number} ({self.get_status_display()})"


# ═══════════════════════════════════════════════════════════════════
# INVOICE PAYMENT
# ═══════════════════════════════════════════════════════════════════

class InvoicePayment(TenantModel):
    """
    Immutable record of a payment applied to an invoice.

    Once created, payments cannot be updated or deleted (enforced at
    ViewSet and Admin level). This ensures a complete audit trail.
    """

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Efectivo'
        CARD = 'card', 'Tarjeta'
        TRANSFER = 'transfer', 'Transferencia'
        CHECK = 'check', 'Cheque'
        OTHER = 'other', 'Otro'

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text='Payment amount. Must be > 0.',
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
    )
    payment_date = models.DateField(
        help_text='Date the payment was received.',
    )
    reference = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='External payment reference (transaction ID, check number, etc.).',
    )
    notes = models.TextField(blank=True, default='')

    # ─── Audit ────────────────────────────────────────────────────
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='invoice_payments_created',
        help_text='Immutable. Auto-assigned from the authenticated user.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invoice_payments'
        verbose_name = 'Invoice Payment'
        verbose_name_plural = 'Invoice Payments'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='invoice_payment_amount_positive',
            ),
        ]

    def __str__(self):
        return (
            f"Pago {self.get_payment_method_display()} — "
            f"${self.amount} → FAC-{self.invoice.invoice_number}"
        )
