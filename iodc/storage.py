"""A minimal S3 client: PUT, GET, DELETE with SigV4, over the standard library.

boto3 would do this too, but it is a large dependency for three verbs, and the
signing is short enough to read in one sitting — which matters more than
convenience for a job that runs unattended every fifteen minutes.

A local directory implementation stands in for tests and dry runs, so the whole
publish path can be exercised without credentials or network.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request

SERVICE = "s3"
REGION = os.environ.get("S3_REGION", "auto")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


class S3Client:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 region: str = REGION):
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    # ── signing ───────────────────────────────────────────────────────────────

    def _headers(self, method: str, key: str, payload: bytes, extra: dict) -> tuple:
        url = f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(key)}"
        parsed = urllib.parse.urlparse(url)
        now = _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = _sha256(payload)

        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        headers.update({k.lower(): v for k, v in extra.items()})

        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canonical_request = "\n".join([
            method, parsed.path, "", canonical_headers, signed_headers, payload_hash,
        ])

        scope = f"{date_stamp}/{self.region}/{SERVICE}/aws4_request"
        to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope, _sha256(canonical_request.encode()),
        ])

        key_bytes = _sign(f"AWS4{self.secret_key}".encode(), date_stamp)
        key_bytes = _sign(key_bytes, self.region)
        key_bytes = _sign(key_bytes, SERVICE)
        signing_key = _sign(key_bytes, "aws4_request")
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()

        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return url, headers

    def _request(self, method: str, key: str, payload: bytes = b"",
                 extra: dict | None = None) -> bytes:
        url, headers = self._headers(method, key, payload, extra or {})
        req = urllib.request.Request(url, data=payload or None, method=method)
        for name, value in headers.items():
            req.add_header(name, value)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(key) from exc
            raise RuntimeError(f"{method} {key} failed: {exc.code} {exc.read()[:200]!r}") from exc

    # ── verbs ─────────────────────────────────────────────────────────────────

    def put(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        self._request("PUT", key, data, {
            "Content-Type": content_type,
            "Cache-Control": cache_control,
        })

    def get(self, key: str) -> bytes:
        return self._request("GET", key)

    def delete(self, key: str) -> None:
        self._request("DELETE", key)


class LocalClient:
    """Filesystem stand-in — lets the publish path be exercised end to end
    without credentials, which is how it is tested and dry-run."""

    def __init__(self, root: str):
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key.replace("/", os.sep))

    def put(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def get(self, key: str) -> bytes:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            raise FileNotFoundError(key) from None

    def delete(self, key: str) -> None:
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass

    def reset(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
