import mimetypes
from django.core.signing import BadSignature, SignatureExpired
from django.http import FileResponse, Http404
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.permissions import IsStaff, IsAdmin
from .models import SalesOrder, Invoice, InvoicePayment
from .serializers import (
    SalesOrderSerializer,
    SalesOrderListSerializer,
    InvoiceSerializer,
    InvoicePaymentSerializer,
)
from . import services
from .pdf_utils import generate_signed_url_token, verify_signed_url_token


# ═══════════════════════════════════════════════════════════════════
# SALES ORDER VIEWSET
# ═══════════════════════════════════════════════════════════════════

class SalesOrderViewSet(viewsets.ModelViewSet):
    """
    Full CRUD + state transitions for Sales Orders.

    SECURITY:
    ┌──────────────────────────────────────────────────────────────┐
    │  1. Only 'draft' orders can be updated or deleted.          │
    │  2. confirm/ action triggers atomic inventory deduction     │
    │     with select_for_update() and Price Lockdown.            │
    │  3. created_by is auto-assigned and immutable.              │
    │  4. All monetary fields are computed server-side.           │
    └──────────────────────────────────────────────────────────────┘

    Endpoints:
        GET    /api/sales/orders/              — List orders
        POST   /api/sales/orders/              — Create draft order
        GET    /api/sales/orders/{id}/         — Retrieve order
        PUT    /api/sales/orders/{id}/         — Update draft order
        PATCH  /api/sales/orders/{id}/         — Partial update draft order
        DELETE /api/sales/orders/{id}/         — Soft-cancel draft order
        POST   /api/sales/orders/{id}/confirm/ — Confirm (locks prices + deducts stock)
        POST   /api/sales/orders/{id}/cancel/  — Cancel draft order
        POST   /api/sales/orders/{id}/invoice/ — Generate invoice from confirmed order
    """
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['order_number', 'customer__name', 'notes']
    ordering_fields = ['created_at', 'total', 'order_number']

    def get_serializer_class(self):
        if self.action == 'list':
            return SalesOrderListSerializer
        return SalesOrderSerializer

    def get_queryset(self):
        """Return orders for the current tenant with related data."""
        qs = SalesOrder.objects.select_related(
            'customer', 'created_by'
        ).prefetch_related('lines__product').all()

        # Optional filters
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        customer_id = self.request.query_params.get('customer')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        return qs

    def perform_create(self, serializer):
        """Create order with auto-assigned tenant, created_by, and order_number."""
        order = serializer.save(
            tenant=self.request.user.tenant,
            created_by=self.request.user,
        )
        services.assign_order_number(order)

    def perform_update(self, serializer):
        """Only allow updates on draft orders."""
        if serializer.instance.status != SalesOrder.Status.DRAFT:
            raise PermissionDenied(
                "Solo se pueden editar órdenes en estado 'borrador'."
            )
        serializer.save()

    def perform_destroy(self, instance):
        """Soft-cancel instead of deleting."""
        if instance.status != SalesOrder.Status.DRAFT:
            raise PermissionDenied(
                "Solo se pueden cancelar órdenes en estado 'borrador'."
            )
        services.cancel_sales_order(instance, self.request.user)

    # ─── Custom Actions ───────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """
        Confirm a draft order.

        This triggers the ATOMIC TRANSACTION that:
        1. Locks product rows and fetches current prices (PRICE LOCKDOWN)
        2. Locks inventory rows and deducts stock
        3. Computes totals server-side
        4. Transitions status to 'confirmed'
        """
        order = self.get_object()
        order = services.confirm_sales_order(order, request.user)
        serializer = SalesOrderSerializer(order, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a draft order."""
        order = self.get_object()
        order = services.cancel_sales_order(order, request.user)
        serializer = SalesOrderSerializer(order, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def invoice(self, request, pk=None):
        """Generate an invoice from a confirmed order."""
        order = self.get_object()
        inv = services.generate_invoice(order, request.user)
        serializer = InvoiceSerializer(inv, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ═══════════════════════════════════════════════════════════════════
# INVOICE VIEWSET
# ═══════════════════════════════════════════════════════════════════

class InvoiceViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Read-only ViewSet for invoices + state transition actions.

    SECURITY — IMMUTABILITY:
    ┌──────────────────────────────────────────────────────────────┐
    │  Invoices are created via SalesOrder.invoice/ action.       │
    │  There is NO create/update/delete on this ViewSet.          │
    │                                                             │
    │  Once an invoice is 'issued' or 'paid':                     │
    │  - No fields can be modified                                │
    │  - The is_locked property returns True                      │
    │  - API rejects any mutation attempt                         │
    └──────────────────────────────────────────────────────────────┘

    Endpoints:
        GET    /api/sales/invoices/                    — List invoices
        GET    /api/sales/invoices/{id}/               — Retrieve invoice
        POST   /api/sales/invoices/{id}/issue/         — Issue invoice (generates PDF)
        GET    /api/sales/invoices/{id}/download_pdf/  — Get signed download URL
        GET    /api/sales/invoices/download/?token=X   — Download PDF with signed token
    """
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['invoice_number', 'sales_order__order_number']
    ordering_fields = ['created_at', 'total', 'invoice_number']

    def get_queryset(self):
        """Return invoices for the current tenant with related data."""
        qs = Invoice.objects.select_related(
            'sales_order__customer', 'created_by',
        ).prefetch_related('payments').all()

        # Optional filters
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)

        return qs

    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        """
        Issue a draft invoice (draft → issued).
        Generates the PDF and makes the invoice IMMUTABLE.
        """
        invoice = self.get_object()
        invoice = services.issue_invoice(invoice, request.user)
        serializer = self.get_serializer(invoice)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='download_pdf')
    def download_pdf(self, request, pk=None):
        """
        Generate a time-limited signed URL token for PDF download.

        SECURITY: The token is HMAC-signed and expires after
        INVOICE_PDF_LINK_EXPIRY_SECONDS (default: 30 minutes).
        """
        invoice = self.get_object()

        if not invoice.pdf_file:
            raise ValidationError({
                'pdf_file': "Esta factura no tiene un PDF generado. "
                            "Emítala primero."
            })

        token = generate_signed_url_token(invoice.id)
        download_url = request.build_absolute_uri(
            f"/api/sales/invoices/download/?token={token}"
        )

        return Response({
            'download_url': download_url,
            'expires_in_seconds': 1800,
            'note': 'Esta URL es temporal y expirará en 30 minutos.',
        })

    @action(detail=False, methods=['get'])
    def download(self, request):
        """
        Download a PDF using a signed token.

        SECURITY — FILE PROTECTION:
        1. Extracts and verifies the HMAC-signed token
        2. Rejects expired tokens (SignatureExpired)
        3. Rejects tampered tokens (BadSignature)
        4. Serves the file only after all checks pass
        """
        token = request.query_params.get('token')
        if not token:
            raise ValidationError({'token': 'Se requiere un token de descarga.'})

        try:
            invoice_id = verify_signed_url_token(token)
        except SignatureExpired:
            raise PermissionDenied(
                "El enlace de descarga ha expirado. "
                "Solicite uno nuevo."
            )
        except BadSignature:
            raise PermissionDenied(
                "Token de descarga inválido."
            )

        # Fetch invoice (verify tenant ownership)
        try:
            invoice = Invoice.objects.get(pk=invoice_id)
        except Invoice.DoesNotExist:
            raise Http404("Factura no encontrada.")

        # Additional tenant check
        if invoice.tenant_id != request.user.tenant_id:
            raise PermissionDenied("No tiene acceso a esta factura.")

        if not invoice.pdf_file:
            raise Http404("PDF no disponible.")

        # Serve the file
        response = FileResponse(
            invoice.pdf_file.open('rb'),
            content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="factura_{invoice.invoice_number}.pdf"'
        )
        return response


# ═══════════════════════════════════════════════════════════════════
# INVOICE PAYMENT VIEWSET
# ═══════════════════════════════════════════════════════════════════

class InvoicePaymentViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Create + Read-only ViewSet for invoice payments.

    Payments are IMMUTABLE once created — no update or delete.

    Endpoints:
        GET    /api/sales/payments/     — List payments
        POST   /api/sales/payments/     — Register a payment
        GET    /api/sales/payments/{id}/ — Retrieve payment
    """
    serializer_class = InvoicePaymentSerializer
    permission_classes = [IsAuthenticated, IsStaff]
    search_fields = ['reference', 'invoice__invoice_number']
    ordering_fields = ['created_at', 'amount']

    def get_queryset(self):
        """Return payments for the current tenant."""
        qs = InvoicePayment.objects.select_related(
            'invoice', 'created_by',
        ).all()

        # Filter by invoice
        invoice_id = self.request.query_params.get('invoice')
        if invoice_id:
            qs = qs.filter(invoice_id=invoice_id)

        return qs

    def perform_create(self, serializer):
        """
        Register a payment using the service layer.

        The service handles:
        - select_for_update() on the invoice (concurrency)
        - Validation of remaining balance
        - Auto-transition to 'paid' if fully paid
        - Auto-assignment of tenant and created_by
        """
        validated = serializer.validated_data
        payment = services.register_payment(
            invoice=validated['invoice'],
            amount=validated['amount'],
            payment_method=validated['payment_method'],
            payment_date=validated['payment_date'],
            reference=validated.get('reference', ''),
            notes=validated.get('notes', ''),
            user=self.request.user,
        )
        # Update the serializer instance so DRF returns the created object
        serializer.instance = payment
