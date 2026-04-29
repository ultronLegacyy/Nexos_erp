from django.contrib import admin
from .models import SalesOrder, SalesOrderLine, Invoice, InvoicePayment


class SalesOrderLineInline(admin.TabularInline):
    model = SalesOrderLine
    extra = 0
    readonly_fields = ('id', 'unit_price', 'line_total')
    fields = ('product', 'quantity', 'unit_price', 'line_total')


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number', 'customer', 'status', 'total',
        'created_by', 'created_at', 'tenant',
    )
    list_filter = ('status', 'tenant', 'created_at')
    search_fields = ('order_number', 'customer__name')
    readonly_fields = (
        'id', 'order_number', 'status',
        'subtotal', 'tax_amount', 'total',
        'created_by', 'confirmed_at', 'cancelled_at',
        'created_at', 'updated_at',
    )
    inlines = [SalesOrderLineInline]
    date_hierarchy = 'created_at'

    def has_change_permission(self, request, obj=None):
        """Only allow editing draft orders."""
        if obj and obj.status != SalesOrder.Status.DRAFT:
            return False
        return super().has_change_permission(request, obj)


class InvoicePaymentInline(admin.TabularInline):
    model = InvoicePayment
    extra = 0
    readonly_fields = ('id', 'created_by', 'created_at')
    fields = (
        'amount', 'payment_method', 'payment_date',
        'reference', 'created_by', 'created_at',
    )

    def has_change_permission(self, request, obj=None):
        """Payments are immutable."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Payments are immutable."""
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'invoice_number', 'sales_order', 'status',
        'total', 'amount_paid', 'created_by', 'created_at', 'tenant',
    )
    list_filter = ('status', 'tenant', 'created_at')
    search_fields = ('invoice_number', 'sales_order__order_number')
    readonly_fields = (
        'id', 'sales_order', 'invoice_number', 'status',
        'subtotal', 'tax_rate', 'tax_amount', 'total', 'amount_paid',
        'issued_at', 'paid_at',
        'created_by', 'created_at', 'updated_at',
    )
    inlines = [InvoicePaymentInline]
    date_hierarchy = 'created_at'

    def has_change_permission(self, request, obj=None):
        """
        SECURITY: Block editing for issued or paid invoices.
        This is the admin-level enforcement of invoice immutability.
        """
        if obj and obj.is_locked:
            return False
        return super().has_change_permission(request, obj)


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(admin.ModelAdmin):
    list_display = (
        'invoice', 'amount', 'payment_method', 'payment_date',
        'reference', 'created_by', 'created_at', 'tenant',
    )
    list_filter = ('payment_method', 'tenant', 'created_at')
    search_fields = ('reference', 'invoice__invoice_number')
    readonly_fields = (
        'id', 'invoice', 'amount', 'payment_method', 'payment_date',
        'reference', 'notes', 'created_by', 'created_at',
    )
    date_hierarchy = 'created_at'

    def has_change_permission(self, request, obj=None):
        """Payments are immutable — no editing allowed."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Payments are immutable — no deletion allowed."""
        return False
