import uuid
from django.db import models
from threading import local

_thread_locals = local()

def get_current_tenant():
    return getattr(_thread_locals, "tenant", None)

def set_current_tenant(tenant):
    _thread_locals.tenant = tenant

class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class TenantManager(models.Manager):
    def get_queryset(self):
        tenant = get_current_tenant()
        queryset = super().get_queryset()
        if tenant:
            return queryset.filter(tenant=tenant)
        return queryset

class TenantModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="%(class)s_related")
    
    objects = TenantManager()
    original_objects = models.Manager()

    class Meta:
        abstract = True
