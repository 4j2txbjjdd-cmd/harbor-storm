"""Sanitise Cloud Logging records before they can reach disk.

This exists because of a specific failure, and the shape of that failure is the
reason the module is written the way it is. An egress probe redacted the URL it
built itself, and a live API key was committed anyway -- because the Cloud
Logging record carries its *own* copy of the request under
`httpRequest.requestUrl`. Redacting the copy you wrote says nothing about the
copy the platform wrote.

So the rule here is: sanitise the parsed object recursively, wherever a URL or a
credential-shaped value appears, and then refuse to write at all if anything
key-shaped survives. `capture` never lets the raw response touch the filesystem
-- it is parsed in memory, sanitised, checked, and only then serialised.

The check is a backstop for the scrubber, not a substitute for it: a redactor
that quietly missed a field would otherwise produce a file that looks clean
because nobody looked.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, Iterable, List

REDACTED = "[REDACTED]"

# Query parameters whose *value* is a credential. The parameter name is kept:
# a reader needs to see that a key was sent, just not which one.
SECRET_QUERY_PARAMS = frozenset({"key", "api_key", "apikey", "access_token",
                                 "token", "client_secret"})

# Structured fields that carry a credential rather than a URL.
SECRET_FIELD_NAMES = frozenset({"authorization", "proxy-authorization",
                                "x-goog-api-key", "api_key", "apikey",
                                "access_token", "id_token", "refresh_token",
                                "client_secret", "private_key", "keystring",
                                "key_string"})

# What must never survive into a written artifact. `AIza…{35}` is the Google API
# key shape that actually leaked; the others are the credential forms most
# likely to appear in a gateway or auth log.
RESIDUE_PATTERNS: Dict[str, re.Pattern] = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "oauth_access_token": re.compile(r"ya29\.[A-Za-z0-9._\-]{20,}"),
    "bearer_header": re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{20,}"),
    "pem_private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


class SecretResidueError(RuntimeError):
    """A sanitised object still contains something credential-shaped.

    Raised instead of writing. A partially-redacted evidence file is worse than
    no evidence file: it reads as safe and is not.
    """


def sanitize_url(url: str) -> str:
    """Strip credential *values* from a URL, keeping everything else intact.

    Scheme, host, path, and every non-secret parameter survive unchanged, and so
    does the name of the secret parameter -- the evidence should still show that
    a key was sent and to where.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(k.lower() in SECRET_QUERY_PARAMS for k, _ in pairs):
        return url
    cleaned = [(k, REDACTED if k.lower() in SECRET_QUERY_PARAMS else v)
               for k, v in pairs]
    # safe="[]" keeps the marker readable rather than percent-encoded.
    query = urllib.parse.urlencode(cleaned, safe="[]")
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _scrub_text(value: str) -> str:
    """Last-resort scrub of credential shapes embedded in free text."""
    out = value
    for pattern in RESIDUE_PATTERNS.values():
        out = pattern.sub(REDACTED, out)
    return out


def sanitize(obj: Any, _key: str = "") -> Any:
    """Recursively sanitise a parsed log structure.

    Recursive on purpose: the leak was nested three levels inside a record, and
    a scrubber that only knows the fields someone remembered to list will miss
    the next one.
    """
    if isinstance(obj, dict):
        return {k: sanitize(v, _key=str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, _key=_key) for v in obj]
    if isinstance(obj, str):
        if _key.lower() in SECRET_FIELD_NAMES:
            return REDACTED
        if "://" in obj:
            return _scrub_text(sanitize_url(obj))
        return _scrub_text(obj)
    return obj


def find_residue(obj: Any, path: str = "$") -> List[str]:
    """Every place a credential shape survived, as readable paths."""
    hits: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits.extend(find_residue(v, f"{path}.{k}"))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(find_residue(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        for name, pattern in RESIDUE_PATTERNS.items():
            if pattern.search(obj):
                hits.append(f"{path} matches {name}")
    return hits


def sanitize_or_raise(obj: Any) -> Any:
    """Sanitise, then refuse to return anything that still looks like a secret."""
    cleaned = sanitize(obj)
    residue = find_residue(cleaned)
    if residue:
        raise SecretResidueError(
            "refusing to emit sanitised output; credential-shaped values "
            "survived at: " + "; ".join(residue))
    return cleaned


def write_sanitized(obj: Any, path: str) -> Any:
    """Sanitise, verify, and only then write. Nothing unsanitised is written."""
    cleaned = sanitize_or_raise(obj)
    with open(path, "w") as fh:
        json.dump(cleaned, fh, indent=2)
        fh.write("\n")
    return cleaned
