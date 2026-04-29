import uuid
from django.db import models
from django.conf import settings
from apps.tenants.models import TenantModel
from apps.products.models import Product


class Inventory(TenantModel):
    """
    Tracks the current quantity_on_hand for each product within a tenant.

    SECURITY:
    - DB-level CHECK constraint ensures quantity_on_hand can NEVER go below 0,
      even if application-level validations are bypassed (e.g. raw SQL, admin).
    - This is the single source of truth for stock availability.
    - One Inventory row per (tenant, product) pair, enforced by UniqueConstraint.
    """
    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        related_name='inventory',
    )
    quantity_on_hand = models.IntegerField(
        default=0,
        help_text='Current stock level. Cannot be negative (DB constraint).',
    )
    last_movement_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the most recent inventory movement.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inventory'
        verbose_name = 'Inventory'
        verbose_name_plural = 'Inventories'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'product'],
                name='unique_inventory_per_product',
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_on_hand__gte=0),
                name='inventory_quantity_on_hand_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.product.name} — Stock: {self.quantity_on_hand}"


class InventoryMovement(TenantModel):
    """
    Immutable audit log for every stock movement.

    Each movement records WHO did WHAT, WHEN, and WHY. Once created,
    movements cannot be updated or deleted (enforced at ViewSet level).

    SECURITY:
    - created_by is auto-assigned from request.user and is immutable.
    - select_for_update() on the Inventory row prevents race conditions.
    - transaction.atomic() ensures the movement + inventory update happen
      together or not at all.
    """

    class MovementType(models.TextChoices):
        INBOUND = 'inbound', 'Entrada'
        OUTBOUND = 'outbound', 'Salida'
        ADJUSTMENT = 'adjustment', 'Ajuste'
        SALE = 'sale', 'Venta'
        RETURN = 'return', 'Devolución'

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='inventory_movements',
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
    )
    quantity = models.PositiveIntegerField(
        help_text='Quantity of units in this movement (always positive).',
    )
    quantity_before = models.IntegerField(
        help_text='Stock level BEFORE this movement was applied.',
    )
    quantity_after = models.IntegerField(
        help_text='Stock level AFTER this movement was applied.',
    )
    reference = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='External reference (e.g. PO number, invoice, sale order).',
    )
    notes = models.TextField(
        blank=True,
        default='',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='inventory_movements',
        help_text='Immutable. Auto-assigned from the authenticated user.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'inventory_movements'
        verbose_name = 'Inventory Movement'
        verbose_name_plural = 'Inventory Movements'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f"{self.get_movement_type_display()} — "
            f"{self.product.name} × {self.quantity} "
            f"({self.quantity_before} → {self.quantity_after})"
        )


# ─── Keep backward-compatible alias ──────────────────────────────
InventoryTransaction = InventoryMovement
