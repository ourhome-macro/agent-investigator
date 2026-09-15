from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx


class ArtifactStorage(Protocol):
    async def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str: ...

    async def get_bytes(self, reference: str) -> bytes: ...


class LocalArtifactStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        del content_type
        target = self._target(key)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)

        def write() -> None:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                handle.write(content)
                temp_path = Path(handle.name)
            os.replace(temp_path, target)

        await asyncio.to_thread(write)
        return f"local://{key}"

    async def get_bytes(self, reference: str) -> bytes:
        if not reference.startswith("local://"):
            raise ValueError("Invalid local artifact reference")
        return await asyncio.to_thread(self._target(reference.removeprefix("local://")).read_bytes)

    def _target(self, key: str) -> Path:
        candidate = (self._root / key.replace("\\", "/")).resolve()
        if self._root not in candidate.parents:
            raise ValueError("Artifact key escapes its storage root")
        return candidate


class S3ArtifactStorage:
    """Small SigV4 S3-compatible client with no SDK runtime dependency."""

    def __init__(self, *, endpoint: str, bucket: str, region: str, access_key: str, secret_key: str, session_token: str | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._session_token = session_token

    async def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        url = self._url(key)
        headers = self._headers("PUT", url, content, content_type)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.put(url, content=content, headers=headers)
            response.raise_for_status()
        return f"s3://{self._bucket}/{key}"

    async def get_bytes(self, reference: str) -> bytes:
        prefix = f"s3://{self._bucket}/"
        if not reference.startswith(prefix):
            raise ValueError("Invalid S3 artifact reference")
        key = reference.removeprefix(prefix)
        url = self._url(key)
        headers = self._headers("GET", url, b"", "application/octet-stream")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.content

    def _url(self, key: str) -> str:
        encoded = "/".join(quote(part, safe="") for part in key.split("/"))
        return f"{self._endpoint}/{quote(self._bucket, safe='')}/{encoded}"

    def _headers(self, method: str, url: str, body: bytes, content_type: str) -> dict[str, str]:
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        parsed = urlsplit(url)
        canonical_uri = parsed.path
        headers = {
            "content-type": content_type,
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if self._session_token:
            headers["x-amz-security-token"] = self._session_token
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in sorted(headers))
        canonical_request = "\n".join((method, canonical_uri, parsed.query, canonical_headers, signed_headers, payload_hash))
        scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        string_to_sign = "\n".join(("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()))
        date_key = hmac.new(("AWS4" + self._secret_key).encode(), date_stamp.encode(), hashlib.sha256).digest()
        region_key = hmac.new(date_key, self._region.encode(), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        signing_key = hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={self._access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
        return headers


def build_artifact_storage() -> ArtifactStorage:
    endpoint = os.getenv("CI_S3_ENDPOINT")
    if endpoint:
        required = {
            "bucket": os.getenv("CI_S3_BUCKET"),
            "access_key": os.getenv("CI_S3_ACCESS_KEY"),
            "secret_key": os.getenv("CI_S3_SECRET_KEY"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError("Incomplete Competitive Research S3 configuration: " + ", ".join(missing))
        return S3ArtifactStorage(
            endpoint=endpoint,
            bucket=required["bucket"] or "",
            region=os.getenv("CI_S3_REGION", "us-east-1"),
            access_key=required["access_key"] or "",
            secret_key=required["secret_key"] or "",
            session_token=os.getenv("CI_S3_SESSION_TOKEN"),
        )
    if os.getenv("DEER_FLOW_ENV", "development").lower() == "production":
        raise RuntimeError("Competitive Research production requires CI_S3_ENDPOINT")
    root = Path(os.getenv("CI_ARTIFACT_ROOT", ".deer-flow/data/investigations"))
    return LocalArtifactStorage(root)
