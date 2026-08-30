"""D1.1: deploy the bounded window-agent to a managed Agent Runtime.

Agent Identity, no service account. Optionally bound to an egress Agent
Gateway. The agent it deploys is the one Harbor already had -- the scope
table, the toolkit and the verifier all come from the frozen core.
"""
import argparse
import json
import os
import sys

import vertexai
from vertexai import types

from app.geap.runtime_app import HarborWindowRuntime

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "harbor-storm-fleet")
LOCATION = os.environ.get("AGENT_RUNTIME_LOCATION", "us-central1")
BUCKET = os.environ.get("AGENT_STAGING_BUCKET", "gs://harbor-storm-fleet-agent-staging")

REQUIREMENTS = [
    "google-cloud-aiplatform[agent_engines,adk]",
    "google-adk>=2.8.0",
    "google-cloud-firestore>=2.29.0",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--display-name", default="harbor-window-agent")
    ap.add_argument("--gateway", default=None,
                    help="agent gateway resource for governed egress")
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--out", default="geap/d1_deployed.json")
    ap.add_argument("--weather-key", action="store_true",
                    help="inject the restricted Weather API key so the allow "
                         "arm can authenticate to the destination service")
    ap.add_argument("--no-token-sharing", action="store_true",
                    help="leave certificate-bound-token protection ON; for a "
                         "probe-only engine that never touches Firestore gRPC")
    a = ap.parse_args()

    client = vertexai.Client(project=PROJECT, location=LOCATION,
                             http_options={"api_version": "v1beta1"})
    app_obj = HarborWindowRuntime(project=PROJECT, location=LOCATION,
                                  model=a.model)

    config = {
        "display_name": a.display_name,
        # No service_account key at all. Agent Identity requires its absence,
        # and an absent key is the only way to be sure it was never set.
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "requirements": REQUIREMENTS,
        "extra_packages": ["./app"],
        "staging_bucket": BUCKET,
        # GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION are reserved by the
        # platform and rejected here; the runtime reads them from the
        # environment Agent Runtime already provides, and set_up() only fills
        # gaps rather than overriding.
        "env_vars": {
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "STATE_BACKEND": "firestore",
            "FIRESTORE_DATABASE": os.environ.get("FIRESTORE_DATABASE", "harbor"),
            # With Agent Identity the agent's credential is NOT handed to
            # Google client libraries by default, so google-cloud-firestore
            # authenticates as nothing and Firestore answers 401. Sharing it
            # is what makes IAM grants written against the agent-identity
            # principal actually govern this runtime's reads and writes --
            # which is the whole point: the identity Google issued is the one
            # Firestore checks.
            "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "False",
        },
    }
    if a.no_token_sharing:
        config["env_vars"].pop(
            "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", None)
    config["env_vars"]["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "true"
    if a.weather_key:
        key = os.environ.get("GOOGLE_WEATHER_API_KEY", "")
        if not key:
            raise SystemExit("--weather-key needs GOOGLE_WEATHER_API_KEY in the env")
        config["env_vars"]["GOOGLE_WEATHER_API_KEY"] = key

    if a.gateway:
        config["agent_gateway_config"] = {
            "agent_to_anywhere_config": {"agent_gateway": a.gateway}
        }

    print(f"deploying to {PROJECT}/{LOCATION}"
          f"{' via gateway ' + a.gateway if a.gateway else ' (no gateway)'} ...",
          flush=True)
    remote = client.agent_engines.create(agent=app_obj, config=config)
    res = remote.api_resource
    print("\nname:              ", res.name)
    print("identity_type:     ", getattr(res.spec, "identity_type", None))
    print("service_account:   ", getattr(res.spec, "service_account", None))
    print("effective_identity:", getattr(res.spec, "effective_identity", None))
    with open(a.out, "w") as fh:
        json.dump({"name": res.name, "project": PROJECT, "location": LOCATION,
                   "display_name": a.display_name, "model": a.model,
                   "gateway": a.gateway,
                   "effective_identity": getattr(res.spec, "effective_identity", None)},
                  fh, indent=2)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
