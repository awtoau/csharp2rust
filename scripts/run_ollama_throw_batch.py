#!/usr/bin/env python3
"""Run one bounded, local-only C# throw-triage batch from corpus facts."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from analysis.corpus_recipes import RECIPE_BY_NAME, open_read_only


ROOT = Path(__file__).resolve().parents[1]
MODEL = "qwen2.5-coder:14b"
MODEL_DIGEST = "9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
KEEP_ALIVE = "30m"


@dataclass(frozen=True)
class ModelIdentity:
    name: str
    digest: str


def request_json(url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urlopen(request, timeout=900) as response:
            result = json.loads(response.read().decode("utf-8"))
    except OSError as error:
        raise ValueError(f"Ollama request failed: {error}") from error
    if not isinstance(result, dict):
        raise ValueError("Ollama response must be a JSON object")
    return result


def endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_HOSTS or parsed.path not in ("", "/"):
        raise ValueError("endpoint must be HTTP on localhost, 127.0.0.1, or ::1")
    return value.rstrip("/")


def resolve_model(base_url: str) -> ModelIdentity:
    models = request_json(f"{base_url}/api/tags").get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama model list does not contain models")
    for item in models:
        if isinstance(item, dict) and item.get("name") == MODEL:
            digest = item.get("digest")
            if digest != MODEL_DIGEST:
                raise ValueError(f"model digest differs: expected {MODEL_DIGEST}, got {digest}")
            return ModelIdentity(MODEL, digest)
    raise ValueError(f"required local model is not installed: {MODEL}")


def entries(database: Path, limit: int) -> list[dict[str, Any]]:
    recipe = RECIPE_BY_NAME["throw-not-implemented"]
    with open_read_only(str(database)) as connection:
        rows = recipe.query(connection, SimpleNamespace(limit=limit))
    if len(rows) < limit:
        raise ValueError(f"only {len(rows)} matching throw facts are available")
    fields = (
        "corpus_run_id",
        "corpus_commit",
        "corpus_tool_version",
        "source_path",
        "containing_type",
        "method_signature",
        "operation_id",
        "span_start",
        "span_len",
        "operation_kind",
        "exception_base_type",
        "constructor_symbol",
    )
    return [{field: row[field] for field in fields} for row in rows]


def payload(entry: dict[str, Any]) -> dict[str, Any]:
    schema = {
        "type": "object",
        "required": [
            "status_claim",
            "source_path",
            "method_signature",
            "span_start",
            "span_len",
            "exception_base_type",
            "constructor_symbol",
            "recommendation",
            "open_question",
        ],
        "properties": {
            "status_claim": {"const": "unvalidated-llm-proposal"},
            "source_path": {"type": "string"},
            "method_signature": {"type": "string"},
            "span_start": {"type": "integer"},
            "span_len": {"type": "integer"},
            "exception_base_type": {"type": "string"},
            "constructor_symbol": {"type": "string"},
            "recommendation": {
                "enum": [
                    "preserve_as_unsupported",
                    "research_rust_panic_mapping",
                    "requires_exception_runtime",
                ]
            },
            "open_question": {"type": "string"},
        },
        "additionalProperties": False,
    }
    return {
        "model": MODEL,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "format": schema,
        "options": {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 512},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a C#-to-Rust corpus-triage assistant. Return exactly one JSON object. "
                    "Treat the supplied operation facts as the only evidence. Copy all corpus fields "
                    "exactly. Do not invent source text, exception behavior, a Rust mapping, tests, "
                    "or a rule. Choose only the next investigation direction. "
                    "status_claim must be unvalidated-llm-proposal."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "source_language": "csharp",
                        "corpus_variant": "rulesdb/patterns.db",
                        "operation_shape": "Throw -> implicit Conversion<System.Exception> -> ObjectCreation",
                        "entry": entry,
                    },
                    sort_keys=True,
                ),
            },
        ],
    }


def validate(entry: dict[str, Any], proposal: Any) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise ValueError("proposal is not an object")
    expected = {
        "status_claim",
        *entry,
        "recommendation",
        "open_question",
    }
    if set(proposal) != expected:
        raise ValueError("proposal fields differ from the triage schema")
    for field, value in entry.items():
        if proposal[field] != value:
            raise ValueError(f"proposal changed evidence field `{field}`")
    if proposal["status_claim"] != "unvalidated-llm-proposal":
        raise ValueError("proposal status claim is invalid")
    if proposal["recommendation"] not in {
        "preserve_as_unsupported",
        "research_rust_panic_mapping",
        "requires_exception_runtime",
    } or not isinstance(proposal["open_question"], str):
        raise ValueError("proposal recommendation is invalid")
    return proposal


def unload(base_url: str) -> None:
    request_json(f"{base_url}/api/generate", {"model": MODEL, "keep_alive": 0})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "rulesdb" / "patterns.db")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evidence" / "ollama-throw-triage")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    if not args.database.is_file():
        parser.error("--database must be an existing corpus database")
    base_url = endpoint(args.endpoint)
    queue = entries(args.database, args.limit)
    if args.dry_run:
        print(json.dumps({"language": "csharp", "entries": queue}, indent=2, sort_keys=True))
        return 0
    model = resolve_model(base_url)
    session_id = hashlib.sha256(
        json.dumps({"model": asdict(model), "entries": queue}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    outcomes = []
    try:
        for sequence, entry in enumerate(queue, start=1):
            request = payload(entry)
            started = time.monotonic()
            raw = request_json(f"{base_url}/api/chat", request)
            elapsed_seconds = time.monotonic() - started
            content = raw.get("message", {}).get("content") if isinstance(raw.get("message"), dict) else None
            record = {
                "status": "unvalidated-llm-proposal",
                "worker_session_id": session_id,
                "sequence": sequence,
                "model": asdict(model),
                "input": entry,
                "elapsed_seconds": elapsed_seconds,
                "raw_response": raw,
            }
            try:
                record["proposal"] = validate(entry, json.loads(content) if isinstance(content, str) else None)
                target = args.output_dir / "staged"
            except (ValueError, json.JSONDecodeError) as error:
                record["proposal"] = None
                record["rejection_reason"] = str(error)
                target = args.output_dir / "rejected"
            target.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(
                json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            path = target / f"{digest}.json"
            path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            outcomes.append({"sequence": sequence, "status": record["status"] if target.name == "staged" else "rejected"})
    finally:
        unload(base_url)
    print(json.dumps({"language": "csharp", "session_id": session_id, "outcomes": outcomes}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
