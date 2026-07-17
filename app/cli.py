"""``foundrysim`` — a thin HTTP client over a running simulator.

No business logic lives here, by design (the same discipline as the sibling
APIM simulator's ``apimsim``): the HTTP surfaces stay the single source of
truth and the CLI only makes requests and pretty-prints JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8020"
DEFAULT_API_VERSION = "2024-10-21"
CONTENT_SAFETY_API_VERSION = "2024-09-01"


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _client(args: argparse.Namespace) -> httpx.Client:
    return httpx.Client(base_url=args.base_url, timeout=30.0)


def _api_headers(args: argparse.Namespace) -> dict[str, str]:
    return {"api-key": args.api_key, "content-type": "application/json"}


def _admin_headers(args: argparse.Namespace) -> dict[str, str]:
    return {"X-Foundry-Admin-Key": args.admin_key}


def _show(response: httpx.Response, *, headers: tuple[str, ...] = ()) -> int:
    shown = {name: response.headers[name] for name in headers if name in response.headers}
    if shown:
        _print({"headers": shown})
    try:
        _print(response.json())
    except json.JSONDecodeError:
        print(response.text)
    return 0 if response.is_success else 1


def cmd_status(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/foundry/management/status", headers=_admin_headers(args)))


def cmd_deployments(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/foundry/management/deployments", headers=_admin_headers(args)))


def cmd_models(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/openai/v1/models", headers=_api_headers(args)))


def cmd_chat(args: argparse.Namespace) -> int:
    body = {"messages": [{"role": "user", "content": args.prompt}]}
    path = f"/openai/deployments/{args.deployment}/chat/completions"
    with _client(args) as client:
        response = client.post(
            path,
            params={"api-version": args.api_version},
            headers=_api_headers(args),
            json=body,
        )
    return _show(response, headers=("x-semantic-cache", "x-semantic-cache-score", "age", "x-request-id"))


def cmd_embed(args: argparse.Namespace) -> int:
    path = f"/openai/deployments/{args.deployment}/embeddings"
    with _client(args) as client:
        response = client.post(
            path,
            params={"api-version": args.api_version},
            headers=_api_headers(args),
            json={"input": args.text},
        )
    if response.is_success:
        payload = response.json()
        vector = payload["data"][0]["embedding"]
        _print(
            {
                "model": payload["model"],
                "dimensions": len(vector),
                "usage": payload["usage"],
                "vector_preview": [round(component, 4) for component in vector[:8]],
            }
        )
        return 0
    return _show(response)


def cmd_analyze(args: argparse.Namespace) -> int:
    body: dict[str, Any] = {"text": args.text, "outputType": args.output_type}
    if args.blocklist:
        body["blocklistNames"] = args.blocklist
    with _client(args) as client:
        response = client.post(
            "/contentsafety/text:analyze",
            params={"api-version": CONTENT_SAFETY_API_VERSION},
            headers=_api_headers(args),
            json=body,
        )
    return _show(response)


def cmd_shield(args: argparse.Namespace) -> int:
    with _client(args) as client:
        response = client.post(
            "/contentsafety/text:shieldPrompt",
            params={"api-version": CONTENT_SAFETY_API_VERSION},
            headers=_api_headers(args),
            json={"userPrompt": args.text, "documents": args.document},
        )
    return _show(response)


def cmd_cache_stats(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/foundry/management/semantic-cache", headers=_admin_headers(args)))


def cmd_cache_events(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/foundry/management/semantic-cache/events", headers=_admin_headers(args)))


def cmd_cache_flush(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.delete("/foundry/management/semantic-cache", headers=_admin_headers(args)))


def cmd_safety_stats(args: argparse.Namespace) -> int:
    with _client(args) as client:
        return _show(client.get("/foundry/management/content-safety", headers=_admin_headers(args)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foundrysim", description="Client for a running aifoundry-simulator")
    parser.add_argument(
        "--base-url",
        default=os.getenv("FOUNDRY_BASE_URL", DEFAULT_BASE_URL),
        help=f"Simulator base URL (env FOUNDRY_BASE_URL, default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("FOUNDRY_API_KEY", "local-foundry-key-alpha"),
        help="Inference/content-safety api-key (env FOUNDRY_API_KEY, defaults to the shipped demo key)",
    )
    parser.add_argument(
        "--admin-key",
        default=os.getenv("FOUNDRY_ADMIN_KEY", "local-foundry-admin-key"),
        help="Management admin key (env FOUNDRY_ADMIN_KEY, defaults to the shipped demo key)",
    )
    parser.add_argument(
        "--api-version",
        default=os.getenv("FOUNDRY_API_VERSION", DEFAULT_API_VERSION),
        help=f"api-version for deployment-scoped calls (default {DEFAULT_API_VERSION})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Management status summary").set_defaults(func=cmd_status)
    subparsers.add_parser("deployments", help="List configured deployments").set_defaults(func=cmd_deployments)
    subparsers.add_parser("models", help="List models via /openai/v1/models").set_defaults(func=cmd_models)

    chat = subparsers.add_parser("chat", help="Send one chat completion")
    chat.add_argument("prompt")
    chat.add_argument("--deployment", default="gpt-demo")
    chat.set_defaults(func=cmd_chat)

    embed = subparsers.add_parser("embed", help="Embed one text and preview the vector")
    embed.add_argument("text")
    embed.add_argument("--deployment", default="text-embedding-demo")
    embed.set_defaults(func=cmd_embed)

    analyze = subparsers.add_parser("analyze", help="Content safety text:analyze")
    analyze.add_argument("text")
    analyze.add_argument("--blocklist", action="append", default=[], help="Blocklist name (repeatable)")
    analyze.add_argument(
        "--output-type", default="FourSeverityLevels", choices=["FourSeverityLevels", "EightSeverityLevels"]
    )
    analyze.set_defaults(func=cmd_analyze)

    shield = subparsers.add_parser("shield", help="Content safety text:shieldPrompt")
    shield.add_argument("text")
    shield.add_argument("--document", action="append", default=[], help="Document text (repeatable)")
    shield.set_defaults(func=cmd_shield)

    subparsers.add_parser("cache-stats", help="Semantic cache counters").set_defaults(func=cmd_cache_stats)
    subparsers.add_parser("cache-events", help="Recent semantic cache events").set_defaults(func=cmd_cache_events)
    subparsers.add_parser("cache-flush", help="Flush the semantic cache").set_defaults(func=cmd_cache_flush)
    subparsers.add_parser("safety-stats", help="Content safety counters").set_defaults(func=cmd_safety_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except httpx.HTTPError as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
