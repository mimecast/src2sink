import logging

logger = logging.getLogger(__name__)


def send_sms(phoneNumber: str, body: str) -> None:
    logger.info("SMS to %s: %s", phoneNumber, body)
