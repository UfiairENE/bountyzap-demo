from django.contrib import admin
from .models import Bounty

@admin.register(Bounty)
class BountyAdmin(admin.ModelAdmin):
    list_display = ('issue_number', 'amount', 'paid', 'contributor', 'lnurl')
    list_filter = ('paid',)
    search_fields = ('issue_number', 'contributor', 'payment_hash')
    readonly_fields = ('payment_hash',)
