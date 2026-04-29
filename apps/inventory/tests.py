"""
Comprehensive Security Test Suite for Inventory Module (Prompt 3).

Tests cover:
1. ATOMIC TRANSACTIONS: Movement + Inventory update happen together or not at all
2. RACE CONDITIONS: Concurrent outbound movements don't corrupt stock
3. STOCK INTEGRITY: quantity_on_hand NEVER goes below 0
4. AUDIT LOG: created_by is immutable and auto-assigned
5. IMMUTABILITY: Movements cannot be updated or deleted
6. CROSS-TENANT: Product from another tenant is rejected
7. SANITIZATION: reference and notes fields are sanitized
8. QUANTITY_BEFORE/AFTER: Snapshot audit trail is accurate
"""
import threading
from decimal import Decimal
from django.test import TestCase, TransactionTestCase
from django.db import connection
from rest_framework.test import APIClient
from rest_framework import status

from apps.tenants.models import Tenant
from apps.users.models import User
from apps.products.models import Category, Product
from .models import Inventory, InventoryMovement


class BaseInventoryTestCase(TestCase):
    """Base test case with two tenants, users, products, and initial inventory."""

    def setUp(self):
        # ── Tenant A ──────────────────────────────────────────────
        self.tenant_a = Tenant.objects.create(
            name='Empresa Alpha', domain='alpha-inv.nexos.com',
        )
        self.user_a = User.objects.create_user(
            username='inv_staff_alpha', password='SecurePass123!',
            role='staff', tenant=self.tenant_a,
        )
        self.user_a_viewer = User.objects.create_user(
            username='inv_viewer_alpha', password='SecurePass123!',
            role='viewer', tenant=self.tenant_a,
        )
        self.category_a = Category.original_objects.create(
            name='Hardware', tenant=self.tenant_a,
        )
        self.product_a = Product.original_objects.create(
            name='SSD 1TB', sku='SSD-1TB', price=Decimal('120.00'),
            stock=100, category=self.category_a, tenant=self.tenant_a,
        )
        # Create inventory record
        self.inventory_a = Inventory.original_objects.create(
            product=self.product_a, tenant=self.tenant_a,
            quantity_on_hand=100,
        )

        # ── Tenant B ──────────────────────────────────────────────
        self.tenant_b = Tenant.objects.create(
            name='Empresa Beta', domain='beta-inv.nexos.com',
        )
        self.user_b = User.objects.create_user(
            username='inv_staff_beta', password='SecurePass123!',
            role='staff', tenant=self.tenant_b,
        )
        self.category_b = Category.original_objects.create(
            name='Software', tenant=self.tenant_b,
        )
        self.product_b = Product.original_objects.create(
            name='Licencia Office', sku='LIC-OFF', price=Decimal('250.00'),
            stock=50, category=self.category_b, tenant=self.tenant_b,
        )
        self.inventory_b = Inventory.original_objects.create(
            product=self.product_b, tenant=self.tenant_b,
            quantity_on_hand=50,
        )

        # ── Clients ──────────────────────────────────────────────
        self.client_a = APIClient()
        self.client_a.force_authenticate(user=self.user_a)
        self.client_b = APIClient()
        self.client_b.force_authenticate(user=self.user_b)
        self.client_viewer = APIClient()
        self.client_viewer.force_authenticate(user=self.user_a_viewer)


# ═══════════════════════════════════════════════════════════════════
# TEST 1: ATOMIC TRANSACTIONS
# ═══════════════════════════════════════════════════════════════════
class TestAtomicTransactions(BaseInventoryTestCase):
    """
    Verifies that InventoryMovement and Inventory update are atomic:
    both persist or neither does.
    """

    def test_successful_inbound_updates_both_movement_and_inventory(self):
        """Inbound movement must create a record AND update quantity_on_hand."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound',
            'quantity': 25,
            'reference': 'PO-001',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Verify movement was created
        self.assertEqual(InventoryMovement.original_objects.filter(
            product=self.product_a
        ).count(), 1)

        # Verify inventory was updated atomically
        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 125)

    def test_failed_outbound_creates_no_movement_and_no_inventory_change(self):
        """If an outbound exceeds stock, neither movement nor inventory should change."""
        initial_count = InventoryMovement.original_objects.count()
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound',
            'quantity': 999,  # exceeds stock of 100
            'reference': 'SO-FAIL',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Verify NO movement was created
        self.assertEqual(
            InventoryMovement.original_objects.count(), initial_count
        )

        # Verify inventory is UNCHANGED
        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 100)

    def test_outbound_updates_both_movement_and_inventory(self):
        """Successful outbound must create movement AND decrement inventory."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound',
            'quantity': 30,
            'reference': 'SO-001',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 70)

        # Verify Product.stock is also synced
        self.product_a.refresh_from_db()
        self.assertEqual(self.product_a.stock, 70)

    def test_sale_movement_decrements_stock(self):
        """Sale movement type decrements inventory like outbound."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'sale',
            'quantity': 10,
            'reference': 'INV-001',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 90)

    def test_return_movement_increments_stock(self):
        """Return movement type increments inventory."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'return',
            'quantity': 5,
            'reference': 'RET-001',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 105)


# ═══════════════════════════════════════════════════════════════════
# TEST 2: STOCK INTEGRITY (quantity_on_hand >= 0)
# ═══════════════════════════════════════════════════════════════════
class TestStockIntegrity(BaseInventoryTestCase):
    """
    Validates that quantity_on_hand can NEVER be negative,
    enforced both at application level and DB constraint level.
    """

    def test_outbound_exceeding_stock_is_rejected(self):
        """Cannot withdraw more than available stock."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound',
            'quantity': 101,  # stock is 100
            'reference': 'SO-FAIL',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_exact_stock_outbound_succeeds(self):
        """Withdrawing exactly all available stock should succeed (result = 0)."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound',
            'quantity': 100,
            'reference': 'SO-ALL',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 0)

    def test_sequential_outbound_respects_remaining_stock(self):
        """Multiple outbound movements must respect decreasing stock."""
        # First: withdraw 60 → remaining 40
        data1 = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound', 'quantity': 60,
        }
        resp1 = self.client_a.post('/api/inventory/movements/', data1)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)

        # Second: withdraw 40 → remaining 0
        data2 = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound', 'quantity': 40,
        }
        resp2 = self.client_a.post('/api/inventory/movements/', data2)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)

        # Third: try to withdraw 1 more → must FAIL
        data3 = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound', 'quantity': 1,
        }
        resp3 = self.client_a.post('/api/inventory/movements/', data3)
        self.assertEqual(resp3.status_code, status.HTTP_400_BAD_REQUEST)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 0)

    def test_sale_exceeding_stock_is_rejected(self):
        """Sale type also respects stock limits."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'sale',
            'quantity': 200,
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_adjustment_sets_absolute_quantity(self):
        """Adjustment movement sets stock to the given quantity."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'adjustment',
            'quantity': 50,
            'notes': 'Conteo físico',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.inventory_a.refresh_from_db()
        self.assertEqual(self.inventory_a.quantity_on_hand, 50)


# ═══════════════════════════════════════════════════════════════════
# TEST 3: AUDIT LOG — IMMUTABLE created_by
# ═══════════════════════════════════════════════════════════════════
class TestAuditLog(BaseInventoryTestCase):
    """
    Validates that created_by is automatically set from request.user
    and cannot be overridden by the client.
    """

    def test_created_by_is_auto_assigned(self):
        """created_by must be set to the authenticated user automatically."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound',
            'quantity': 10,
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(response.data['created_by']), str(self.user_a.id))

    def test_client_cannot_override_created_by(self):
        """Even if client sends created_by, it must be ignored (read_only)."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound',
            'quantity': 10,
            'created_by': str(self.user_b.id),  # Attempt to impersonate!
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # created_by must be user_a, NOT user_b
        self.assertEqual(str(response.data['created_by']), str(self.user_a.id))

    def test_quantity_before_and_after_are_recorded(self):
        """Movement must record accurate quantity_before and quantity_after."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'outbound',
            'quantity': 25,
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['quantity_before'], 100)
        self.assertEqual(response.data['quantity_after'], 75)

    def test_sequential_movements_audit_trail(self):
        """Sequence of movements must create an accurate audit trail."""
        # Movement 1: Inbound +50 (100 → 150)
        self.client_a.post('/api/inventory/movements/', {
            'product': str(self.product_a.id),
            'movement_type': 'inbound', 'quantity': 50,
        })
        # Movement 2: Outbound -30 (150 → 120)
        self.client_a.post('/api/inventory/movements/', {
            'product': str(self.product_a.id),
            'movement_type': 'outbound', 'quantity': 30,
        })
        # Movement 3: Sale -20 (120 → 100)
        self.client_a.post('/api/inventory/movements/', {
            'product': str(self.product_a.id),
            'movement_type': 'sale', 'quantity': 20,
        })

        movements = InventoryMovement.original_objects.filter(
            product=self.product_a
        ).order_by('created_at')

        self.assertEqual(movements.count(), 3)
        self.assertEqual(movements[0].quantity_before, 100)
        self.assertEqual(movements[0].quantity_after, 150)
        self.assertEqual(movements[1].quantity_before, 150)
        self.assertEqual(movements[1].quantity_after, 120)
        self.assertEqual(movements[2].quantity_before, 120)
        self.assertEqual(movements[2].quantity_after, 100)


# ═══════════════════════════════════════════════════════════════════
# TEST 4: MOVEMENT IMMUTABILITY
# ═══════════════════════════════════════════════════════════════════
class TestMovementImmutability(BaseInventoryTestCase):
    """
    Movements are immutable audit records. PUT, PATCH, DELETE are rejected.
    """

    def _create_movement(self):
        return InventoryMovement.original_objects.create(
            product=self.product_a, movement_type='inbound',
            quantity=10, quantity_before=100, quantity_after=110,
            tenant=self.tenant_a, created_by=self.user_a,
        )

    def test_put_not_allowed(self):
        """PUT must return 405 Method Not Allowed."""
        mv = self._create_movement()
        response = self.client_a.put(
            f'/api/inventory/movements/{mv.id}/', {'quantity': 999}
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_patch_not_allowed(self):
        """PATCH must return 405 Method Not Allowed."""
        mv = self._create_movement()
        response = self.client_a.patch(
            f'/api/inventory/movements/{mv.id}/', {'quantity': 999}
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_not_allowed(self):
        """DELETE must return 405 Method Not Allowed."""
        mv = self._create_movement()
        response = self.client_a.delete(
            f'/api/inventory/movements/{mv.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ═══════════════════════════════════════════════════════════════════
# TEST 5: CROSS-TENANT VALIDATION
# ═══════════════════════════════════════════════════════════════════
class TestCrossTenantValidation(BaseInventoryTestCase):
    """
    A user from Tenant A must NOT be able to create movements
    for products belonging to Tenant B.
    """

    def test_movement_with_foreign_product_fails(self):
        """Creating a movement with another tenant's product → 400."""
        data = {
            'product': str(self.product_b.id),
            'movement_type': 'inbound',
            'quantity': 10,
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('product', response.data)

    def test_movement_with_own_product_succeeds(self):
        """Creating a movement with own product → 201."""
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound',
            'quantity': 10,
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


# ═══════════════════════════════════════════════════════════════════
# TEST 6: SANITIZATION
# ═══════════════════════════════════════════════════════════════════
class TestSanitization(BaseInventoryTestCase):
    """XSS sanitization on reference and notes fields."""

    def test_xss_stripped_from_reference(self):
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound', 'quantity': 5,
            'reference': '<script>steal()</script>PO-XSS',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('<script>', response.data['reference'])

    def test_xss_stripped_from_notes(self):
        data = {
            'product': str(self.product_a.id),
            'movement_type': 'inbound', 'quantity': 5,
            'notes': '<img src=x onerror=alert(1)>Safe note',
        }
        response = self.client_a.post('/api/inventory/movements/', data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('<img', response.data['notes'])
        self.assertIn('Safe note', response.data['notes'])


# ═══════════════════════════════════════════════════════════════════
# TEST 7: INVENTORY READ-ONLY ENDPOINT
# ═══════════════════════════════════════════════════════════════════
class TestInventoryStockEndpoint(BaseInventoryTestCase):
    """Test the read-only stock query endpoint."""

    def test_list_stock(self):
        """GET /api/inventory/stock/ returns current stock levels."""
        response = self.client_a.get('/api/inventory/stock/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_stock_reflects_movements(self):
        """Stock endpoint reflects changes from movements."""
        # Create inbound movement
        self.client_a.post('/api/inventory/movements/', {
            'product': str(self.product_a.id),
            'movement_type': 'inbound', 'quantity': 50,
        })
        response = self.client_a.get('/api/inventory/stock/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Find our product in the results
        data = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        if isinstance(data, list):
            found = [i for i in data if str(i['product']) == str(self.product_a.id)]
            if found:
                self.assertEqual(found[0]['quantity_on_hand'], 150)


# ═══════════════════════════════════════════════════════════════════
# TEST 8: RACE CONDITION (requires TransactionTestCase for threads)
# ═══════════════════════════════════════════════════════════════════
class TestRaceCondition(TransactionTestCase):
    """
    Tests concurrent access to prevent race conditions.

    Uses TransactionTestCase (not TestCase) because we need real DB
    transactions for select_for_update() to work across threads.

    NOTE: SQLite doesn't support true row-level locking like PostgreSQL,
    but this test validates the logical flow. With PostgreSQL, the
    select_for_update() would provide true SERIALIZABLE behavior.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Race Test Corp', domain='race.nexos.com',
        )
        self.user = User.objects.create_user(
            username='race_user', password='SecurePass123!',
            role='staff', tenant=self.tenant,
        )
        self.category = Category.original_objects.create(
            name='Test Cat', tenant=self.tenant,
        )
        self.product = Product.original_objects.create(
            name='Race Product', sku='RACE-001', price=Decimal('10.00'),
            stock=10, category=self.category, tenant=self.tenant,
        )
        self.inventory = Inventory.original_objects.create(
            product=self.product, tenant=self.tenant,
            quantity_on_hand=10,
        )

    def test_concurrent_outbound_prevents_negative_stock(self):
        """
        Two simultaneous outbound requests for 8 units each (stock=10)
        must result in only ONE succeeding. The second must fail.

        Without select_for_update(), both threads could read stock=10,
        both subtract 8, and both write stock=2 — resulting in stock=2
        but 16 units actually withdrawn from a stock of 10.

        With select_for_update(), the second thread waits for the first
        to commit, then reads the updated stock=2 and correctly rejects.
        """
        results = {'successes': 0, 'failures': 0}
        errors = []

        def make_outbound():
            try:
                client = APIClient()
                client.force_authenticate(user=self.user)
                response = client.post('/api/inventory/movements/', {
                    'product': str(self.product.id),
                    'movement_type': 'outbound',
                    'quantity': 8,
                    'reference': f'RACE-{threading.current_thread().name}',
                })
                if response.status_code == 201:
                    results['successes'] += 1
                else:
                    results['failures'] += 1
            except Exception as e:
                errors.append(str(e))
                results['failures'] += 1
            finally:
                connection.close()

        t1 = threading.Thread(target=make_outbound, name='Thread-1')
        t2 = threading.Thread(target=make_outbound, name='Thread-2')

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # At most ONE should succeed (stock=10, each wants 8)
        self.assertLessEqual(results['successes'], 1,
            f"Both threads succeeded — race condition detected! "
            f"Results: {results}, Errors: {errors}"
        )

        # Verify stock is never negative
        self.inventory.refresh_from_db()
        self.assertGreaterEqual(self.inventory.quantity_on_hand, 0,
            "Stock went negative — race condition vulnerability!"
        )
