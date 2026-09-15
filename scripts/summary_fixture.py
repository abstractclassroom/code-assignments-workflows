"""Public, synthetic display check. This is not a valid assignment receipt."""
import base64
import hashlib
import hmac
import json
import os

from runtime import summary


def fixture_token():
    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "public-ci-fixture"}).encode())
    payload = encode(json.dumps({"purpose": "public summary display test, not a grade receipt", "grade": 82.5}).encode())
    message = f"{header}.{payload}"
    signature = encode(hmac.new(b"public synthetic fixture key, never used by the API", message.encode(), hashlib.sha256).digest())
    return "ACGT1_" + encode(f"{message}.{signature}".encode())


if __name__ == "__main__":
    if os.environ["FIXTURE_SCORE"] != "82.5":
        raise ValueError("Composite action score did not reach the caller")
    summary(82.5, fixture_token())
