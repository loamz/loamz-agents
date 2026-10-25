"""Push endpoints are delivery capabilities, never arbitrary URLs the server should fetch."""

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class SubscriptionIn(BaseModel):
    device: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,128}$")
    endpoint: str = Field(max_length=4096)
    p256dh: str = Field(pattern=r"^[A-Za-z0-9_-]{87}={0,2}$")
    auth: str = Field(pattern=r"^[A-Za-z0-9_-]{22}={0,2}$")

    @field_validator("endpoint")
    @classmethod
    def push_service(cls, value: str) -> str:
        url = urlsplit(value)
        host = url.hostname or ""
        approved = host in {
            "fcm.googleapis.com",
            "web.push.apple.com",
            "updates.push.services.mozilla.com",
        } or host.endswith(".push.services.mozilla.com")
        if (
            url.scheme != "https"
            or not approved
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("Use a supported browser's HTTPS push service")
        return value
