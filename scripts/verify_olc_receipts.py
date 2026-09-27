"""Independently verify public OLC pilot receipts and Ed25519 signatures."""

import argparse
import json
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature

from splitchain.olc_receipts import verify_document


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("receipts URL redirected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://bokiloki.ddns.net/splitchain/receipts")
    parser.add_argument("--public-key", help="expected verifier public key in hexadecimal")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("HTTPS is required")
    with build_opener(NoRedirect).open(Request(args.url), timeout=10) as response:
        body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("receipt document too large")
        document = json.loads(body)
    try:
        verified, pending = verify_document(document, args.public_key)
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"INVALID OLC receipt: {exc}") from exc
    print(f"Valid signed receipts: {verified}; pending signatures: {pending}")
    if pending:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
