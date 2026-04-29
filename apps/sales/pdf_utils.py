"""
PDF generation and secure download utilities for invoices.

SECURITY — FILE PROTECTION:
┌──────────────────────────────────────────────────────────────┐
│  PDF download URLs are TEMPORARY and SIGNED.                │
│                                                             │
│  1. Uses Django's TimestampSigner to create HMAC-signed     │
│     tokens that include the invoice ID.                     │
│  2. Tokens expire after INVOICE_PDF_LINK_EXPIRY_SECONDS     │
│     (default: 1800 = 30 minutes).                           │
│  3. The download endpoint verifies the signature before     │
│     serving the file, rejecting expired or tampered tokens. │
│  4. Direct file access is NOT possible — all downloads go   │
│     through the authenticated + signed endpoint.            │
└──────────────────────────────────────────────────────────────┘
"""
import io
import os
from django.conf import settings
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.core.files.base import ContentFile

# PDF generation with reportlab
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


# Signer instance for URL token generation
_signer = TimestampSigner(salt='invoice-pdf-download')

# Expiry time (seconds) — configurable via settings
PDF_LINK_EXPIRY = getattr(settings, 'INVOICE_PDF_LINK_EXPIRY_SECONDS', 1800)


def generate_signed_url_token(invoice_id):
    """
    Generate a time-limited signed token for PDF download.

    The token contains the invoice UUID and is signed with HMAC
    using Django's SECRET_KEY. It expires after PDF_LINK_EXPIRY seconds.

    Args:
        invoice_id: UUID of the invoice

    Returns:
        Signed token string
    """
    return _signer.sign(str(invoice_id))


def verify_signed_url_token(token):
    """
    Verify a signed PDF download token.

    Args:
        token: The signed token string

    Returns:
        The invoice_id (as string) if valid

    Raises:
        BadSignature: If the token has been tampered with
        SignatureExpired: If the token has expired
    """
    return _signer.unsign(token, max_age=PDF_LINK_EXPIRY)


def generate_invoice_pdf(invoice):
    """
    Generate a PDF for the given invoice and attach it to the model.

    Uses reportlab to create a professional invoice PDF with:
    - Header with invoice number and dates
    - Customer information
    - Line items table
    - Totals section
    """
    if not HAS_REPORTLAB:
        # If reportlab is not installed, skip PDF generation silently
        return None

    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#1a1a2e'),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        'InvoiceSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#555555'),
    )
    normal_style = styles['Normal']

    elements = []

    # ─── Header ───────────────────────────────────────────────────
    elements.append(Paragraph(f"FACTURA", title_style))
    elements.append(Paragraph(
        f"No. FAC-{invoice.invoice_number}",
        subtitle_style,
    ))
    elements.append(Spacer(1, 12))

    # Dates
    date_info = f"Fecha de emisión: {invoice.issued_at.strftime('%d/%m/%Y') if invoice.issued_at else 'Pendiente'}"
    elements.append(Paragraph(date_info, normal_style))
    elements.append(Spacer(1, 6))

    # Status
    elements.append(Paragraph(
        f"Estado: {invoice.get_status_display()}",
        normal_style,
    ))
    elements.append(Spacer(1, 18))

    # ─── Customer Info ────────────────────────────────────────────
    order = invoice.sales_order
    customer = order.customer

    elements.append(Paragraph("CLIENTE", subtitle_style))
    elements.append(Paragraph(f"Nombre: {customer.name}", normal_style))
    if customer.tax_id:
        elements.append(Paragraph(f"RFC/NIT: {customer.tax_id}", normal_style))
    if customer.email:
        elements.append(Paragraph(f"Email: {customer.email}", normal_style))
    if customer.address:
        elements.append(Paragraph(f"Dirección: {customer.address}", normal_style))
    elements.append(Spacer(1, 18))

    # ─── Line Items Table ─────────────────────────────────────────
    elements.append(Paragraph("DETALLE", subtitle_style))
    elements.append(Spacer(1, 6))

    table_data = [['Producto', 'SKU', 'Cantidad', 'Precio Unit.', 'Total']]

    for line in order.lines.select_related('product').all():
        table_data.append([
            line.product.name,
            line.product.sku,
            str(line.quantity),
            f"${line.unit_price:,.2f}",
            f"${line.line_total:,.2f}",
        ])

    table = Table(table_data, colWidths=[2.5 * inch, 1.2 * inch, 0.8 * inch, 1.1 * inch, 1.1 * inch])
    table.setStyle(TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a1a2e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),

        # Data rows
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),

        # Alternating row colors
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
            colors.white, colors.HexColor('#f5f5f5'),
        ]),

        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#1a1a2e')),

        # Padding
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 18))

    # ─── Totals ───────────────────────────────────────────────────
    totals_data = [
        ['', '', 'Subtotal:', f"${invoice.subtotal:,.2f}"],
        ['', '', f"IVA ({float(invoice.tax_rate) * 100:.0f}%):",
         f"${invoice.tax_amount:,.2f}"],
        ['', '', 'TOTAL:', f"${invoice.total:,.2f}"],
    ]

    totals_table = Table(
        totals_data,
        colWidths=[2.5 * inch, 1.2 * inch, 1.5 * inch, 1.5 * inch],
    )
    totals_table.setStyle(TableStyle([
        ('FONTNAME', (2, -1), (3, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (2, 0), (3, -1), 10),
        ('ALIGN', (2, 0), (3, -1), 'RIGHT'),
        ('LINEABOVE', (2, -1), (3, -1), 1, colors.HexColor('#1a1a2e')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    elements.append(totals_table)
    elements.append(Spacer(1, 24))

    # ─── Footer ───────────────────────────────────────────────────
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#999999'),
    )
    elements.append(Paragraph(
        f"Documento generado por Nexos ERP — "
        f"Orden: OV-{order.order_number}",
        footer_style,
    ))

    # Build PDF
    doc.build(elements)

    # Save to model
    pdf_content = buffer.getvalue()
    buffer.close()

    filename = f"invoice_{invoice.invoice_number}.pdf"
    invoice.pdf_file.save(filename, ContentFile(pdf_content), save=True)

    return invoice
