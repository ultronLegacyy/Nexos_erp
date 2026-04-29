import uuid
from django.db import models
from apps.tenants.models import TenantModel


class Customer(TenantModel):
    """
    Customer scoped to a tenant.

    Each customer belongs to exactly one tenant. The (tenant, email) and
    (tenant, tax_id) pairs are enforced as unique at the DB level to prevent
    duplicate customer records within the same organization.
    """
    name = models.CharField(
        max_length=255,
        help_text='Full name or business name of the customer.',
    )
    email = models.EmailField(
        max_length=255,
        blank=True,
        default='',
        help_text='Contact email. Unique per tenant when provided.',
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        default='',
    )
    address = models.TextField(
        blank=True,
        default='',
    )
    tax_id = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text='Tax identification number (RFC, NIT, etc.). Unique per tenant when provided.',
    )
    notes = models.TextField(
        blank=True,
        default='',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customers'
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
        ordering = ['name']
        constraints = [
            # A tenant cannot have two customers with the same email
            models.UniqueConstraint(
                fields=['tenant', 'email'],
                name='unique_customer_email_per_tenant',
                condition=~models.Q(email=''),
            ),
            # A tenant cannot have two customers with the same tax_id
            models.UniqueConstraint(
                fields=['tenant', 'tax_id'],
                name='unique_customer_tax_id_per_tenant',
                condition=~models.Q(tax_id=''),
            ),
        ]

    def __str__(self):
        label = self.name
        if self.tax_id:
            label += f' ({self.tax_id})'
        return label
