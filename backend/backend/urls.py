from django.conf import settings
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Configurable, so production can take it off the path every scanner
    # tries first. See DJANGO_ADMIN_PATH in settings.
    path(settings.DJANGO_ADMIN_PATH, admin.site.urls),
    path('', include('billing.urls')),
    path('', include('fiber.urls')),
]
