from django.utils.deprecation import MiddlewareMixin
from .models import set_current_tenant

class TenantMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.user.is_authenticated:
            set_current_tenant(request.user.tenant)
        else:
            # Optionally identify tenant by domain or header for public pages
            # For this ERP, we assume tenant is tied to the authenticated user
            set_current_tenant(None)

    def process_response(self, request, response):
        set_current_tenant(None)
        return response
