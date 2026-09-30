from django.contrib import admin

from .models import Review, Run, Ticket, TicketEvent


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "mode", "subject", "input_tokens", "output_tokens", "created_at")
    list_filter = ("status", "mode")
    readonly_fields = ("state", "pending_review")


admin.site.register(Ticket)
admin.site.register(TicketEvent)
admin.site.register(Review)
