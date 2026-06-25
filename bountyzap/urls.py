from django.contrib import admin
from django.urls import path, include
from bot import views
from bot.lnurl import lnurl_patterns

urlpatterns = [
    path('admin/', admin.site.urls),
    path('webhook/', views.github_webhook, name='webhook'),
    path('badge/', views.bounty_badge, name='badge'),
    path('lnurl/', include(lnurl_patterns)),
]
