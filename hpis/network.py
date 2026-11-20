import ssl
from pathlib import Path

from aiohttp import ClientResponseError, web
from nacl.exceptions import BadSignatureError, CryptoError
from cryptography.exceptions import InvalidTag

from .codec import pack, unpack


class RetrySearch(Exception):
    pass


def tls_server(folder, require_client):
    folder = Path(folder)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(folder / "tls.pem", folder / "tls.key")
    if require_client:
        ctx.load_verify_locations(folder / "ca.pem")
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def tls_client(folder, *, client_cert=False):
    folder = Path(folder)
    ctx = ssl.create_default_context(cafile=str(folder / "ca.pem"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    if client_cert:
        ctx.load_cert_chain(folder / "tls.pem", folder / "tls.key")
    return ctx


async def post(session, base, route, value, role):
    async with session.post(
        base.rstrip("/") + route,
        data=pack(value),
        server_hostname=role + ".internal",
        headers={"Content-Type": "application/msgpack"},
        allow_redirects=False,
    ) as response:
        response.raise_for_status()
        if response.status != 200:
            raise ValueError("unexpected HTTP status")
        return unpack(await response.read())


def reply(value):
    return web.Response(body=pack(value), content_type="application/msgpack")


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except (BadSignatureError, CryptoError, InvalidTag, PermissionError):
        raise web.HTTPForbidden(text="authentication failed")
    except RetrySearch:
        raise web.HTTPConflict(text="retry the query")
    except ClientResponseError as exc:
        return web.Response(
            status=exc.status if exc.status in (400, 403, 409, 413) else 502
        )
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        raise web.HTTPBadRequest(text="invalid request")
