from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="menu", permanent=False)),
    # Customer (kiosk): menu is open, the card is tapped only at payment
    path("kiosk/", views.menu, name="menu"),
    path("kiosk/order/", views.order, name="order"),
    path("kiosk/receipt/<int:order_id>/", views.receipt, name="receipt"),
    path("kiosk/finish/", views.kiosk_finish, name="kiosk_finish"),
    # Staff (kitchen)
    path("staff/", views.kitchen, name="kitchen"),
    path("staff/order/<int:order_id>/advance/", views.advance, name="advance"),
    path("staff/login/", auth_views.LoginView.as_view(template_name="core/staff_login.html"), name="staff_login"),
    path("staff/logout/", auth_views.LogoutView.as_view(), name="staff_logout"),
]
