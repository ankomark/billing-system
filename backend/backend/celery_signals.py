import traceback
from celery.signals import task_failure
from billing.tasks.alert_tasks import notify_admin_task

@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, einfo=None, **extra):
    msg = (
        f"🚨 Celery Task Failed\n"
        f"Task: {sender.name if sender else 'unknown'}\n"
        f"Task ID: {task_id}\n"
        f"Exception: {exception}\n"
        f"Args: {args}\n"
        f"Kwargs: {kwargs}\n"
    )
    notify_admin_task.delay(msg)
import backend.celery_signals  # noqa