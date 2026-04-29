"""
Business logic services for the Sales module.

This module encapsulates transactional business logic, keeping it separate
from the ViewSet layer. All state transitions that involve multiple models
are handled here under explicit transaction control.

CRITICAL SECURITY PATTERNS:
1. transaction.atomic() — All-or-nothing for multi-model operations.
2. select_for_update() — Row-level locking to prevent race conditions.
3. Price Lockdown — Product.price is fetched from DB, never from client.
4. Inventory integration — Stock is deducted atomically on confirmation.
"""
import uuid
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.products.models import Product
from apps.inventory.models import Inventory, InventoryMovement
from .models import SalesOrder, SalesOrderLine, Invoice, InvoicePayment


def _generate_order_number(tenant):
    """
    Generate a sequential order number unique to the tenant.
    Format: OV-0001, OV-0002, etc.
    """
    last_order = (
        SalesOrder.original_objects
        .filter(tenant=tenant)
        .order_by('-created_at')
        .values_list('order_number', flat=True)
        .first()
    )
    if last_order:
        try:
            last_num = int(last_order.split('-')[-1])
            return f"OV-{last_num + 1:04d}"
        except (ValueError, IndexError):
            pass
    return "OV-0001"


def _generate_invoice_number(tenant):
    """
    Generate a sequential invoice number unique to the tenant.
    Format: FAC-0001, FAC-0002, etc.
    """
    last_invoice = (
        Invoice.original_objects
        .filter(tenant=tenant)
        .order_by('-created_at')
        .values_list('invoice_number', flat=True)
        .first()
    )
    if last_invoice:
        try:
            last_num = int(last_invoice.split('-')[-1])
            return f"FAC-{last_num + 1:04d}"
        except (ValueError, IndexError):
            pass
    return "FAC-0001"


def assign_order_number(order):
    """Assign an auto-generated order number if not already set."""
    if not order.order_number:
        order.order_number = _generate_order_number(order.tenant)
        order.save(update_fields=['order_number'])


# ═══════════════════════════════════════════════════════════════════
# CONFIRM SALES ORDER
# ═══════════════════════════════════════════════════════════════════

@transaction.atomic
def confirm_sales_order(order, user):
    """
    Confirm a draft sales order.

    ATOMIC TRANSACTION FLOW:
    ════════════════════════

    Step 1:  BEGIN TRANSACTION (via @transaction.atomic)
    Step 2:  Validate order is in 'draft' status
    Step 3:  For EACH line in the order:
             a) SELECT ... FOR UPDATE on Product row
                → Acquires exclusive lock, reads current Product.price
             b) SET unit_price = Product.price (PRICE LOCKDOWN)
             c) SET line_total = quantity × unit_price
             d) SELECT ... FOR UPDATE on Inventory row
                → Acquires exclusive lock, reads current stock
             e) Validate stock >= requested quantity
             f) Deduct stock (create InventoryMovement of type 'sale')
    Step 4:  Compute order totals (subtotal, tax_amount, total)
    Step 5:  Update order status to 'confirmed'
    Step 6:  COMMIT (all locks released, all writes visible)

    If ANY step fails → ROLLBACK (no partial state).

    Args:
        order: SalesOrder instance (must be in 'draft' status)
        user: User performing the confirmation

    Returns:
        The updated SalesOrder instance

    Raises:
        ValidationError: If order is not draft, stock is insufficient, etc.
    """
    # ── Step 2: Validate current status ───────────────────────────
    if order.status != SalesOrder.Status.DRAFT:
        raise ValidationError({
            'status': f"Solo se pueden confirmar órdenes en estado 'borrador'. "
                      f"Estado actual: '{order.get_status_display()}'."
        })

    lines = order.lines.select_related('product').all()
    if not lines.exists():
        raise ValidationError({
            'lines': "La orden no tiene líneas de producto."
        })

    subtotal = Decimal('0.00')

    for line in lines:
        # ── Step 3a: Lock the product row to read current price ───
        # PRICE LOCKDOWN: We fetch the price from the DB under a lock,
        # ensuring no concurrent price change can cause inconsistency.
        product = Product.original_objects.select_for_update().get(
            pk=line.product_id
        )

        if not product.is_active:
            raise ValidationError({
                'lines': f"El producto '{product.name}' está desactivado."
            })

        # ── Step 3b-c: Assign server-side price ──────────────────
        # This is the PRICE LOCKDOWN mechanism:
        # The unit_price is ALWAYS taken from Product.price in the DB,
        # never from any client-supplied value.
        line.unit_price = product.price
        line.line_total = Decimal(str(line.quantity)) * product.price
        line.save(update_fields=['unit_price', 'line_total'])

        subtotal += line.line_total

        # ── Step 3d-f: Deduct inventory atomically ───────────────
        _deduct_inventory(product, line.quantity, order, user)

    # ── Step 4: Compute totals ────────────────────────────────────
    order.subtotal = subtotal
    order.tax_amount = subtotal * order.tax_rate
    order.total = subtotal + order.tax_amount

    # ── Step 5: Update status ─────────────────────────────────────
    order.status = SalesOrder.Status.CONFIRMED
    order.confirmed_at = timezone.now()
    order.save(update_fields=[
        'subtotal', 'tax_amount', 'total',
        'status', 'confirmed_at', 'updated_at',
    ])

    return order


def _deduct_inventory(product, quantity, order, user):
    """
    Deduct stock for a product within the current atomic transaction.

    Uses the same select_for_update() pattern as the inventory module
    to prevent race conditions where two concurrent sales both see
    the same stock level and both succeed.

    Args:
        product: Product instance (already locked)
        quantity: Number of units to deduct
        order: SalesOrder (for the movement reference)
        user: User performing the operation
    """
    # Lock the inventory row
    try:
        inventory = Inventory.original_objects.select_for_update().get(
            product=product,
            tenant=user.tenant,
        )
    except Inventory.DoesNotExist:
        raise ValidationError({
            'lines': f"El producto '{product.name}' no tiene registro de inventario."
        })

    quantity_before = inventory.quantity_on_hand

    # ── Validate stock sufficiency under the lock ─────────────────
    # This is the DEFINITIVE check. Even if a pre-check passed,
    # another transaction may have reduced stock concurrently.
    if quantity_before < quantity:
        raise ValidationError({
            'lines': (
                f"Stock insuficiente para '{product.name}'. "
                f"Disponible: {quantity_before}, Solicitado: {quantity}."
            )
        })

    new_quantity = quantity_before - quantity

    # Backend guarantee: quantity_on_hand >= 0
    if new_quantity < 0:
        raise ValidationError({
            'lines': "La cantidad en mano no puede ser negativa."
        })

    # Update inventory
    inventory.quantity_on_hand = new_quantity
    inventory.last_movement_at = timezone.now()
    try:
        inventory.save(update_fields=[
            'quantity_on_hand', 'last_movement_at', 'updated_at',
        ])
    except IntegrityError as e:
        if 'inventory_quantity_on_hand_non_negative' in str(e):
            raise ValidationError({
                'lines': 'Violación de integridad: stock no puede ser negativo.'
            })
        raise

    # Create immutable movement record
    InventoryMovement.objects.create(
        tenant=user.tenant,
        product=product,
        movement_type=InventoryMovement.MovementType.SALE,
        quantity=quantity,
        quantity_before=quantity_before,
        quantity_after=new_quantity,
        reference=f"OV-{order.order_number}",
        notes=f"Venta automática por confirmación de orden OV-{order.order_number}",
        created_by=user,
    )

    # Sync Product.stock for backward compatibility
    product.stock = new_quantity
    product.save(update_fields=['stock'])


# ═══════════════════════════════════════════════════════════════════
# CANCEL SALES ORDER
# ═══════════════════════════════════════════════════════════════════

def cancel_sales_order(order, user):
    """
    Cancel a draft sales order. Only draft orders can be cancelled.
    Confirmed orders cannot be cancelled (stock has already been deducted).
    """
    if order.status != SalesOrder.Status.DRAFT:
        raise ValidationError({
            'status': (
                "Solo se pueden cancelar órdenes en estado 'borrador'. "
                f"Estado actual: '{order.get_status_display()}'."
            )
        })

    order.status = SalesOrder.Status.CANCELLED
    order.cancelled_at = timezone.now()
    order.save(update_fields=['status', 'cancelled_at', 'updated_at'])
    return order


# ═══════════════════════════════════════════════════════════════════
# GENERATE INVOICE
# ═══════════════════════════════════════════════════════════════════

@transaction.atomic
def generate_invoice(order, user):
    """
    Generate an invoice from a confirmed sales order.

    The order must be in 'confirmed' status. After invoice creation,
    the order transitions to 'invoiced'.
    """
    if order.status != SalesOrder.Status.CONFIRMED:
        raise ValidationError({
            'status': (
                "Solo se puede facturar una orden confirmada. "
                f"Estado actual: '{order.get_status_display()}'."
            )
        })

    # Check if invoice already exists
    if hasattr(order, 'invoice') and order.invoice:
        raise ValidationError({
            'sales_order': "Esta orden ya tiene una factura generada."
        })

    invoice_number = _generate_invoice_number(order.tenant)

    invoice = Invoice.objects.create(
        tenant=order.tenant,
        sales_order=order,
        invoice_number=invoice_number,
        status=Invoice.Status.DRAFT,
        subtotal=order.subtotal,
        tax_rate=order.tax_rate,
        tax_amount=order.tax_amount,
        total=order.total,
        created_by=user,
    )

    # Transition order to 'invoiced'
    order.status = SalesOrder.Status.INVOICED
    order.save(update_fields=['status', 'updated_at'])

    return invoice


# ═══════════════════════════════════════════════════════════════════
# ISSUE INVOICE
# ═══════════════════════════════════════════════════════════════════

@transaction.atomic
def issue_invoice(invoice, user):
    """
    Issue a draft invoice (draft → issued).

    Once issued, the invoice becomes IMMUTABLE — no further edits allowed.
    This also triggers PDF generation.
    """
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError({
            'status': (
                "Solo se pueden emitir facturas en estado 'borrador'. "
                f"Estado actual: '{invoice.get_status_display()}'."
            )
        })

    invoice.status = Invoice.Status.ISSUED
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=['status', 'issued_at', 'updated_at'])

    # Generate PDF
    from .pdf_utils import generate_invoice_pdf
    generate_invoice_pdf(invoice)

    return invoice


# ═══════════════════════════════════════════════════════════════════
# REGISTER PAYMENT
# ═══════════════════════════════════════════════════════════════════

@transaction.atomic
def register_payment(invoice, amount, payment_method, payment_date,
                     reference, notes, user):
    """
    Register a payment against an invoice.

    If total payments >= invoice.total, the invoice transitions to 'paid'.
    Uses select_for_update() on the invoice to prevent concurrent payments
    from exceeding the total.
    """
    # Lock the invoice row to prevent concurrent payment race conditions
    invoice = Invoice.original_objects.select_for_update().get(pk=invoice.pk)

    if invoice.status == Invoice.Status.CANCELLED:
        raise ValidationError({
            'invoice': "No se puede registrar un pago en una factura cancelada."
        })
    if invoice.status == Invoice.Status.PAID:
        raise ValidationError({
            'invoice': "Esta factura ya está completamente pagada."
        })

    remaining = invoice.total - invoice.amount_paid
    if amount > remaining:
        raise ValidationError({
            'amount': (
                f"El pago excede el saldo pendiente. "
                f"Saldo: ${remaining}, Pago: ${amount}"
            )
        })

    # Create immutable payment record
    payment = InvoicePayment.objects.create(
        tenant=invoice.tenant,
        invoice=invoice,
        amount=amount,
        payment_method=payment_method,
        payment_date=payment_date,
        reference=reference,
        notes=notes,
        created_by=user,
    )

    # Update running total
    invoice.amount_paid += amount
    update_fields = ['amount_paid', 'updated_at']

    # Auto-transition to 'paid' if fully paid
    if invoice.amount_paid >= invoice.total:
        invoice.status = Invoice.Status.PAID
        invoice.paid_at = timezone.now()
        update_fields.extend(['status', 'paid_at'])

    invoice.save(update_fields=update_fields)

    return payment
