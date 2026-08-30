"""Capture Agent Gateway logs with credentials removed before they reach disk.

    PYTHONPATH=. .venv/bin/python geap/capture_gateway_logs.py \
        --out geap/gw_logs_rotated.json --freshness 30m

The raw Cloud Logging response is read into memory, parsed, sanitised and
checked, and only then serialised. It is never written first and cleaned
afterwards, because that is exactly how a live API key was once committed: the
probe redacted the URL it wrote, while the log record carried its own copy under
`httpRequest.requestUrl`.

Exit 2 means something credential-shaped survived sanitisation and no file was
written. That is the intended outcome, not a bug to work around.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from app.geap.log_scrubber import SecretResidueError, write_sanitized

DEFAULT_FILTER = 'resource.type="networkservices.googleapis.com/Gateway"'


def read_logs(log_filter: str, project: str, limit: int, freshness: str) -> list:
    """Return parsed log records. The response never touches the filesystem."""
    proc = subprocess.run(
        ["gcloud", "logging", "read", log_filter,
         f"--project={project}", f"--limit={limit}",
         f"--freshness={freshness}", "--format=json"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"gcloud logging read failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout or "[]")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--project", default="harbor-storm-fleet")
    ap.add_argument("--filter", dest="log_filter", default=DEFAULT_FILTER)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--freshness", default="30m")
    a = ap.parse_args(argv)

    records = read_logs(a.log_filter, a.project, a.limit, a.freshness)
    try:
        cleaned = write_sanitized(records, a.out)
    except SecretResidueError as exc:
        print(f"REFUSED TO WRITE {a.out}\n{exc}", file=sys.stderr)
        return 2

    print(f"wrote {a.out}: {len(cleaned)} record(s), sanitised before write")
    for e in cleaned:
        hr = e.get("httpRequest", {}) or {}
        jp = e.get("jsonPayload", {}) or {}
        pol = jp.get("enforcedGatewaySecurityPolicy", {}) or {}
        agw = jp.get("agentGatewayInfo", {}) or {}
        print(f"  {e.get('timestamp')}  {hr.get('requestMethod')} "
              f"{hr.get('status')}  host={pol.get('hostname')}")
        print(f"      registry: {agw.get('agentRegistryResource', '(none - unregistered)')}")
        print(f"      cert    : {(jp.get('mtls') or {}).get('clientCertPresent')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
