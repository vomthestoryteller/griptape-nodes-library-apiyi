"""How the nodes reach APIYI: the key, the gateway address, the retry rule, and the error mapping."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from griptape_nodes.exe_types.core_types import NodeError, NodeErrorLink
from griptape_nodes.retained_mode.events.secrets_events import GetSecretValueRequest, GetSecretValueResultSuccess
from griptape_nodes.retained_mode.griptape_nodes import GriptapeNodes

logger = logging.getLogger("griptape_nodes")

__all__ = [
    "API_KEY_NAME",
    "API_KEY_URL",
    "ApiyiRequest",
    "MultipartFile",
    "download",
    "gateway_url",
    "missing_key_error",
    "read_secret",
    "send",
]

PROVIDER = "APIYI"
API_KEY_NAME = "APIYI_API_KEY"
API_KEY_URL = "https://api.apiyi.com/token"
# Optional. Set this secret to use the backup host, https://b.apiyi.com.
BASE_URL_NAME = "APIYI_BASE_URL"
DEFAULT_BASE_URL = "https://api.apiyi.com"
IMAGE_API_GUIDE_URL = "https://docs.apiyi.com/api-capabilities/image-api-best-practices"

HTTP_UNAUTHORIZED = 401
# Seconds to wait before each new attempt, by status. APIYI does not bill these statuses, so another
# attempt costs nothing. Its guides advise one or two retries after a 500. Nothing else is retried:
# a 400 fails the same way again, a client timeout is billed even though no image comes back, and a
# 502 or 504 can mean that the model is still running.
RETRY_DELAYS_SECONDS: dict[int, tuple[int, ...]] = {429: (5, 15, 45), 500: (5, 15), 503: (5, 15, 45)}

CONNECT_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 120

# (form field name, (filename, content, MIME type)), the shape httpx takes for a repeated field.
MultipartFile = tuple[str, tuple[str, bytes, str]]


@dataclass(frozen=True)
class ApiyiRequest:
    """One POST to the gateway. It holds a JSON body, or form fields with files for a multipart body."""

    path: str
    json: dict[str, Any] | None = None
    form: dict[str, str] | None = None
    files: list[MultipartFile] = field(default_factory=list)


def read_secret(name: str) -> str | None:
    """The value of a secret, or None when it is not set.

    Reads with a request, not through the SecretsManager, so that a node in a worker process also gets
    the value.
    """
    result = GriptapeNodes.handle_request(
        GetSecretValueRequest(
            key=name,
            should_error_on_not_found=False,
            broadcast_result=False,
            failure_log_level=logging.DEBUG,
        )
    )
    if isinstance(result, GetSecretValueResultSuccess) and result.value:
        return result.value
    return None


def missing_key_error() -> NodeError:
    return NodeError.from_missing_secret(API_KEY_NAME, key_url=API_KEY_URL)


def gateway_url() -> str:
    """The gateway root, with no trailing slash and no version segment."""
    value = (read_secret(BASE_URL_NAME) or DEFAULT_BASE_URL).strip().rstrip("/")
    return value.removesuffix("/v1")


async def send(request: ApiyiRequest, *, api_key: str, timeout_seconds: int) -> dict[str, Any]:
    """Send `request` and return the JSON object in the answer.

    Raises:
        NodeError: The gateway refused the request, or no usable answer came back.
    """
    url = f"{gateway_url()}{request.path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        retries = 0
        while True:
            response = await _post(client, url, headers, request, timeout_seconds)
            delays = RETRY_DELAYS_SECONDS.get(response.status_code, ())
            if retries >= len(delays):
                break
            logger.info("APIYI answered HTTP %s. Trying again in %s seconds.", response.status_code, delays[retries])
            await asyncio.sleep(delays[retries])
            retries += 1

    if response.is_error:
        raise NodeError.from_http_response(
            response,
            provider=PROVIDER,
            secret_name=API_KEY_NAME,
            # APIYI also answers 403 for content review, so only a 401 means a bad key.
            key_refused=response.status_code == HTTP_UNAUTHORIZED,
        )
    return _json_object(response)


async def download(url: str) -> bytes:
    """Fetch a generated image from the URL in an answer.

    These URLs point at the storage of the model vendor, so this request must not carry the API key.

    Raises:
        NodeError: The file did not come back. The URL is attached, because it works for a short time.
    """
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise NodeError(
            "The image was generated, but the download failed. "
            "Open the 'image_url' value in a browser soon, because the link expires.",
            fields={"image_url": url},
        ) from e
    return response.content


async def _post(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], request: ApiyiRequest, timeout_seconds: int
) -> httpx.Response:
    try:
        if request.files:
            return await client.post(url, headers=headers, data=request.form or {}, files=request.files)
        return await client.post(url, headers=headers, json=request.json)
    except httpx.TimeoutException as e:
        raise NodeError(
            f"APIYI did not answer in {timeout_seconds} seconds. APIYI can still bill this request. "
            "Increase 'Timeout' before you run the node again.",
            links=[NodeErrorLink(label="APIYI timeout guide", url=IMAGE_API_GUIDE_URL)],
        ) from e
    except httpx.HTTPError as e:
        raise NodeError.from_request_exception(e, provider=PROVIDER, secret_name=API_KEY_NAME) from e


def _json_object(response: httpx.Response) -> dict[str, Any]:
    fields = {"status_code": response.status_code}
    try:
        body = response.json()
    except ValueError as e:
        text = (response.text or "").strip()
        raise NodeError(
            "The answer from APIYI is not JSON. Run the node again.",
            fields=fields,
            response={"text": text[:2000]} if text else None,
        ) from e
    if not isinstance(body, dict):
        raise NodeError("The answer from APIYI is not a JSON object. Run the node again.", fields=fields)
    return body
