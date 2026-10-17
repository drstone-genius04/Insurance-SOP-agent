#!/usr/bin/env python3
"""Scripted live scenarios against a running Claims Desk (real model required for PASS).

Usage:
    python scripts/live_demo.py
    python scripts/live_demo.py --scenario angry_caller
    python scripts/live_demo.py --base-url http://localhost:8000 --verbose

Optional visitor key (for a Render deploy with BYOK and no server key):

    export CLAIMS_LLM_PROVIDER=groq
    export CLAIMS_LLM_MODEL=openai/gpt-oss-120b
    export CLAIMS_LLM_API_KEY=gsk_...
    python scripts/live_demo.py --base-url https://your-service.onrender.com

Dependencies: standard library + httpx (already in requirements.txt).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.eval.scenarios import NOT_CONFIGURED_MARKER, SCENARIOS, Scenario  # noqa: E402


class AgentClient:
    def __init__(self, base_url: str, timeout: float, llm: dict[str, str] | None = None):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._llm = llm or {}

    def healthz(self) -> dict[str, Any]:
        response = self._http.get("/healthz")
        response.raise_for_status()
        return response.json()

    def new_session(self, body: dict[str, Any] | None = None) -> tuple[str, str]:
        response = self._http.post("/api/session", json=body or {})
        response.raise_for_status()
        data = response.json()
        return data["session_id"], data["reply"]

    def say(self, session_id: str, message: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message, **self._llm}
        response = self._http.post(f"/api/session/{session_id}/message", json=payload)
        response.raise_for_status()
        return response.json()


def _visitor_llm() -> dict[str, str]:
    key = (os.getenv("CLAIMS_LLM_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not key:
        return {}
    body = {"llm_api_key": key}
    provider = (os.getenv("CLAIMS_LLM_PROVIDER") or os.getenv("LLM_PROVIDER") or "").strip()
    model = (os.getenv("CLAIMS_LLM_MODEL") or os.getenv("LLM_MODEL") or "").strip()
    base = (os.getenv("CLAIMS_LLM_BASE_URL") or os.getenv("LLM_BASE_URL") or "").strip()
    if provider:
        body["llm_provider"] = provider
    if model:
        body["llm_model"] = model
    if base:
        body["llm_base_url"] = base
    return body


def run_scenario(client: AgentClient, scenario: Scenario, verbose: bool) -> tuple[int, int, bool]:
    print()
    print("=" * 88)
    print(f"SCENARIO {scenario.name}: {scenario.description}")
    print("=" * 88)
    session_id, greeting = client.new_session(scenario.session_body)
    print(f"[VERIFY_ID] AGENT: {greeting}")
    passed = failed = 0
    not_configured = False

    for index, turn in enumerate(scenario.turns, start=1):
        print()
        print(f"--- turn {index}")
        print(f"USER : {turn.text}")
        data = client.say(session_id, turn.text)
        reply, state = data["reply"], data["state"]
        print(f"AGENT [{state['phase']}]: {reply}")
        if NOT_CONFIGURED_MARKER in reply.lower():
            not_configured = True
        if verbose and data.get("debug"):
            for decision in data["debug"].get("decisions", []) or data.get("decisions") or []:
                print(f"      decision: {decision}")
        for check in turn.checks:
            name, ok, detail = check(state, reply)
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({detail})")
            if ok:
                passed += 1
            else:
                failed += 1

    return passed, failed, not_configured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    llm = _visitor_llm()
    client = AgentClient(args.base_url, args.timeout, llm)
    try:
        health = client.healthz()
    except httpx.HTTPError as exc:
        print(f"Cannot reach {args.base_url}: {exc}", file=sys.stderr)
        print("Start the server first: docker compose up --build", file=sys.stderr)
        return 2
    print(
        f"Server {args.base_url}: provider={health.get('provider')} model={health.get('model')} "
        f"configured={health.get('llm_configured')} visitor_key={'yes' if llm else 'no'}"
    )

    selected = list(SCENARIOS.values()) if args.scenario == "all" else [SCENARIOS[args.scenario]]
    # Live runs need a key; the unconfigured scenario is for offline pytest only.
    if args.scenario == "all":
        selected = [s for s in selected if s.name != "unconfigured_notice"]

    totals: list[tuple[str, int, int]] = []
    any_not_configured = False
    for scenario in selected:
        try:
            passed, failed, not_configured = run_scenario(client, scenario, args.verbose)
        except httpx.HTTPError as exc:
            print(f"  ERROR request failed during scenario {scenario.name}: {exc}", file=sys.stderr)
            passed, failed, not_configured = 0, sum(len(t.checks) for t in scenario.turns), False
        totals.append((scenario.name, passed, failed))
        any_not_configured = any_not_configured or not_configured

    print()
    print("=" * 88)
    total_pass = sum(p for _, p, _ in totals)
    total_fail = sum(f for _, _, f in totals)
    for name, passed, failed in totals:
        print(f"{name}: {passed} PASS, {failed} FAIL")
    print(f"TOTAL: {total_pass} PASS, {total_fail} FAIL")
    if any_not_configured:
        print("NOTE: server answered with the not-configured notice; set a key or CLAIMS_LLM_API_KEY.")
    if total_fail:
        print("RESULT: CHECK(S) FAILED")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
