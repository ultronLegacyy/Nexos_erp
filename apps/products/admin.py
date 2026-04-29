from django.contrib import admin
from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'is_active', 'created_at')
    list_filter = ('is_active', 'tenant')
    search_fields = ('name',)
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'category', 'price', 'stock', 'tenant', 'is_active')
    list_filter = ('is_active', 'category', 'tenant')
    search_fields = ('name', 'sku')
    readonly_fields = ('id', 'created_at', 'updated_at')
