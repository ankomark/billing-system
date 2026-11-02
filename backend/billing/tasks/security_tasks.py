"""Housekeeping for the sign-in record."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

# Long enough to answer "was that me, three months ago?"; short enough that an
# attacker hammering the login cannot grow the table without bound.
LOGIN_EVENT_RETENTION_DAYS = 180


@shared_task
def prune_login_events_task():
    from billing.models import LoginEvent

    cutoff = timezone.now() - timedelta(days=LOGIN_EVENT_RETENTION_DAYS)
    deleted, _ = LoginEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted
