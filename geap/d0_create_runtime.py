"""D0: create the smallest legitimate Agent Runtime with Agent Identity.

No agent code. The point is to establish that this project and region will
provision an Agent Identity at all, and to read back what Google actually
created -- not what we asked for.
"""
import json
import os
import sys

import vertexai
from vertexai import types

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "harbor-storm-fleet")
LOCATION = os.environ.get("AGENT_RUNTIME_LOCATION", "us-central1")
DISPLAY = os.environ.get("D0_DISPLAY_NAME", "harbor-d0-identity-probe")


def main() -> int:
    client = vertexai.Client(project=PROJECT, location=LOCATION,
                             http_options={"api_version": "v1beta1"})
    print(f"creating in {PROJECT}/{LOCATION} ...", flush=True)
    app = client.agent_engines.create(config={
        "display_name": DISPLAY,
        # Agent Identity requires that service_account is NOT set. It is
        # absent here deliberately: naming one would make Google hand back a
        # service-account identity and the D0 gate would be meaningless.
        "identity_type": types.IdentityType.AGENT_IDENTITY,
    })
    res = app.api_resource
    print("\n=== as returned by create ===")
    print("name:              ", res.name)
    print("identity_type:     ", getattr(res.spec, "identity_type", None))
    print("service_account:   ", getattr(res.spec, "service_account", None))
    print("effective_identity:", getattr(res.spec, "effective_identity", None))
    with open("geap/d0_created.json", "w") as fh:
        json.dump({"name": res.name, "location": LOCATION, "project": PROJECT},
                  fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
