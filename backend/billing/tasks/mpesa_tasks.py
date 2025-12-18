from celery import shared_task
import logging

from billing.mpesa_client import initiate_stk_push

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=20,
    retry_kwargs={"max_retries": 3},
    retry_jitter=True,
)
def initiate_stk_push_task(self, phone, amount, account_reference, description):
    response = initiate_stk_push(
        phone_number=phone,
        amount=amount,
        account_reference=account_reference,
        description=description,
    )
    logger.info(f"[initiate_stk_push_task] STK sent for {account_reference}")
    return response
