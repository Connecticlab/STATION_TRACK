"""URLconf pour le sous-domaine admin.<BASE_DOMAIN> uniquement. Fixe dynamiquement par
TenantMiddleware via request.urlconf. Contient l'admin Django classique (auth.User) et
les vues Super Admin (platform_admin) — jamais expose sur un autre sous-domaine."""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('platform_admin.urls')),
]
