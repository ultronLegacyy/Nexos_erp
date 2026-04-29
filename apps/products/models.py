import uuid
from django.db import models
from apps.tenants.models import TenantModel


class Category(TenantModel):
    """
    Product category scoped to a tenant.
    The combination of (tenant, name) is enforced as unique at the DB level.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'categories'
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'name'],
                name='unique_category_name_per_tenant'
            ),
        ]

    def __str__(self):
        return self.name


class Product(TenantModel):
    """
    Product scoped to a tenant with a tenant-unique SKU.
    The category must belong to the same tenant (enforced at serializer level).
    """
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products'
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    sku = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'products'
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'sku'],
                name='unique_sku_per_tenant'
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku})"
