"""Submit or inspect the fixed OLC pilot job without exposing a token in argv."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("submit", "jobs"))
    parser.add_argument("--token-file", default="/srv/splitchain-testnet/olc/operator-token")
    args = parser.parse_args()
    token = Path(args.token_file).read_text().strip()
    route = "/operator/job" if args.action == "submit" else "/operator/jobs"
    body = json.dumps({"kind": "sha256-fixed-v1", "node_id": "olc-worker-001"}).encode() if args.action == "submit" else None
    request = Request("http://127.0.0.1:8088" + route, data=body,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                      method="POST" if body is not None else "GET")
    with urlopen(request, timeout=10) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == "__main__":
    main()
