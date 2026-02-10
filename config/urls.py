from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    
    path("login/", auth_views.LoginView.as_view(template_name="auth/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("", include("core.urls")),  # o donde tengas tu home/dashboard

    path("caja/", include(("caja.urls", "caja"), namespace="caja")),
     
    path("catalogo/", include(("catalogo.urls", "catalogo"), namespace="catalogo")),
   # path("ventas/", include(("ventas.urls", "ventas"), namespace="ventas")),
   path("admin-panel/", include("admin_panel.urls", namespace="admin_panel")),
]