from django.contrib import admin

from .models import CalendarEvent


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ('date', 'category', 'title', 'ticker', 'important')
    list_filter = ('category', 'important')
    search_fields = ('title', 'ticker', 'memo')
