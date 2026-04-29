from django.contrib import admin
from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'tax_id', 'tenant', 'is_active', 'created_at')
    list_filter = ('is_active', 'tenant')
    search_fields = ('name', 'email', 'tax_id', 'phone')
    readonly_fields = ('id', 'created_at', 'updated_at')
