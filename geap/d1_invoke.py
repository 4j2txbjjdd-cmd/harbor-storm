"""Invoke a deployed Harbor Agent Runtime and print the evidence."""
import argparse, json, os, sys
import vertexai

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "harbor-storm-fleet")
LOCATION = os.environ.get("AGENT_RUNTIME_LOCATION", "us-central1")

ap = argparse.ArgumentParser()
ap.add_argument("--engine", required=True)
ap.add_argument("--method", default="whoami")
ap.add_argument("--kwargs", default="{}")
ap.add_argument("--out", default=None)
a = ap.parse_args()

client = vertexai.Client(project=PROJECT, location=LOCATION,
                         http_options={"api_version": "v1beta1"})
engine = client.agent_engines.get(name=a.engine)
out = getattr(engine, a.method)(**json.loads(a.kwargs))
text = json.dumps(out, indent=2, default=str)
print(text)
if a.out:
    open(a.out, "w").write(text)
