from authlib.integrations.django_client import OAuth
from django.conf import settings

oauth = OAuth()

oauth.register(
    name="okta",
    client_id=settings.OKTA_CLIENT_ID,
    client_secret=settings.OKTA_CLIENT_SECRET,
    server_metadata_url=f"{settings.OKTA_ISSUER}/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid profile email groups"
    },
)