"""Keep credential-bearing SDK authentication messages out of logs."""


def suppress_sdk_auth_logs() -> None:
    """Disable only SDK modules that can log cookie cache paths containing PSIDs."""
    from loguru import logger

    logger.disable("gemini_webapi.utils.rotate_1psidts")
    logger.disable("gemini_webapi.utils.get_access_token")
