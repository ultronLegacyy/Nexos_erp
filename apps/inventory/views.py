from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework import viewsets, status, mixins
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action

from apps.users.permissions import IsStaff
from apps.products.models import Product
from .models import Inventory, InventoryMovement
from .serializers import InventorySerializer, InventoryMovementSerializer


class InventoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for querying current stock levels.

    Stock is only modified through InventoryMovements (never directly),
    ensuring full audit trail and atomic consistency.
    """
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        """Return inventory records for the current tenant."""
        qs = Inventory.objects.select_related('product').all()

        # Filter by product
        product_id = self.request.query_params.get('product')
        if product_id:
            qs = qs.filter(product_id=product_id)

        # Filter low stock
        low_stock = self.request.query_params.get('low_stock')
        if low_stock:
            threshold = int(low_stock)
            qs = qs.filter(quantity_on_hand__lte=threshold)

        return qs

    @action(detail=False, methods=['get'])
    def alerts(self, request):
        """Return inventory items with critically low stock (≤ 10)."""
        threshold = int(request.query_params.get('threshold', 10))
        qs = self.get_queryset().filter(quantity_on_hand__lte=threshold)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)


class InventoryMovementViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    ViewSet for inventory movements (create + read only).

    CRITICAL SECURITY:
    ┌──────────────────────────────────────────────────────────────────┐
    │  1. transaction.atomic()  — All-or-nothing: movement record    │
    │     and inventory update happen together or rollback entirely.  │
    │                                                                │
    │  2. select_for_update()   — Row-level lock on the Inventory    │
    │     row prevents two concurrent requests from reading the same │
    │     stale quantity_on_hand and both decrementing it.            │
    │                                                                │
    │  3. CHECK constraint      — DB-level guarantee that            │
    │     quantity_on_hand >= 0, even if app logic is bypassed.      │
    │                                                                │
    │  4. Immutable created_by  — Auto-assigned from request.user,   │
    │     never accepted from client input.                          │
    │                                                                │
    │  5. No PUT/PATCH/DELETE   — Movements are immutable audit logs. │
    └──────────────────────────────────────────────────────────────────┘
    """
    serializer_class = InventoryMovementSerializer
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        """Return movements for the current tenant with related data."""
        qs = InventoryMovement.objects.select_related(
            'product', 'created_by'
        ).all()

        # Optional filters
        product_id = self.request.query_params.get('product')
        if product_id:
            qs = qs.filter(product_id=product_id)

        movement_type = self.request.query_params.get('type')
        if movement_type:
            qs = qs.filter(movement_type=movement_type)

        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        """
        ATOMIC TRANSACTION FLOW:
        ════════════════════════

        Step 1: BEGIN TRANSACTION (automatic via @transaction.atomic)
        Step 2: SELECT ... FOR UPDATE on the Inventory row
                → Acquires exclusive row lock. Other transactions WAIT here.
        Step 3: Re-validate quantity_on_hand under the lock
                → Prevents race conditions (two sales seeing stock=10)
        Step 4: Calculate new quantity_on_hand
        Step 5: Validate new quantity >= 0 (backend guarantee)
        Step 6: UPDATE inventory SET quantity_on_hand = new_value
        Step 7: INSERT INTO inventory_movements (with before/after snapshots)
        Step 8: COMMIT (releases the lock, both writes are visible)

        If ANY step fails → ROLLBACK (neither write persists).
        """
        product = serializer.validated_data['product']
        quantity = serializer.validated_data['quantity']
        movement_type = serializer.validated_data['movement_type']

        # ── Step 2: Lock the inventory row ────────────────────────
        # select_for_update() issues SELECT ... FOR UPDATE in PostgreSQL,
        # acquiring an exclusive row lock that blocks concurrent transactions
        # from reading or modifying this row until we COMMIT or ROLLBACK.
        try:
            inventory = Inventory.original_objects.select_for_update().get(
                product=product,
                tenant=self.request.user.tenant,
            )
        except Inventory.DoesNotExist:
            # Auto-create inventory record for this product if not exists
            inventory = Inventory.original_objects.select_for_update().create(
                product=product,
                tenant=self.request.user.tenant,
                quantity_on_hand=0,
            )

        # ── Step 3: Capture state before mutation ─────────────────
        quantity_before = inventory.quantity_on_hand

        # ── Step 4: Calculate new stock level ─────────────────────
        if movement_type in (
            InventoryMovement.MovementType.OUTBOUND,
            InventoryMovement.MovementType.SALE,
        ):
            new_quantity = quantity_before - quantity
        elif movement_type in (
            InventoryMovement.MovementType.INBOUND,
            InventoryMovement.MovementType.RETURN,
        ):
            new_quantity = quantity_before + quantity
        elif movement_type == InventoryMovement.MovementType.ADJUSTMENT:
            # Adjustments can increase or decrease. The quantity field
            # represents the new absolute stock level for adjustments.
            new_quantity = quantity
        else:
            raise ValidationError({'movement_type': 'Tipo de movimiento no válido.'})

        # ── Step 5: Backend stock integrity validation ────────────
        # This is the DEFINITIVE check, under the lock. Even if the
        # serializer's optimistic check passed, concurrent transactions
        # may have reduced stock in the meantime.
        if new_quantity < 0:
            raise ValidationError({
                'quantity': (
                    f"Stock insuficiente. "
                    f"Disponible: {quantity_before}, "
                    f"Solicitado: {quantity}. "
                    f"La cantidad en mano no puede ser negativa."
                )
            })

        # ── Step 6: Update the inventory record ───────────────────
        inventory.quantity_on_hand = new_quantity
        inventory.last_movement_at = timezone.now()

        try:
            inventory.save(update_fields=['quantity_on_hand', 'last_movement_at', 'updated_at'])
        except IntegrityError as e:
            # This catches the DB-level CHECK constraint violation as a
            # last-resort safety net, in case application logic has a bug.
            if 'inventory_quantity_on_hand_non_negative' in str(e):
                raise ValidationError({
                    'quantity': 'Violación de integridad: stock no puede ser negativo.'
                })
            raise

        # ── Step 7: Create the movement record ────────────────────
        # created_by is ALWAYS set from request.user (immutable audit field)
        serializer.save(
            tenant=self.request.user.tenant,
            created_by=self.request.user,
            quantity_before=quantity_before,
            quantity_after=new_quantity,
        )

        # Also sync the Product.stock field for backward compatibility
        product_locked = Product.original_objects.select_for_update().get(pk=product.pk)
        product_locked.stock = new_quantity
        product_locked.save(update_fields=['stock'])

        # ── Step 8: COMMIT happens automatically when the
        #    @transaction.atomic block exits without exception ─────


# ─── Backward-compatible alias ────────────────────────────────────
InventoryTransactionViewSet = InventoryMovementViewSet
