from django.contrib import admin

from .models import Account, LedgerEntry, MenuItem, Order, OrderItem, OrderStatusChange

admin.site.register(Account)
admin.site.register(MenuItem)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "account", "status", "total", "created_at")
    inlines = [OrderItemInline]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "kind", "amount", "order")
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderStatusChange)
class OrderStatusChangeAdmin(admin.ModelAdmin):
    list_display = ("order", "status", "changed_at", "changed_by")
