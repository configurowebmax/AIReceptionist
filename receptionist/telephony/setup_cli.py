from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from receptionist.telephony.netelip import (
    DEFAULT_NETELIP_LATAM_ADDRESSES,
    NetelipConfigError,
    NetelipPlan,
    provision_livekit,
)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="python -m receptionist.telephony",
        description="SIP telephony provisioning utilities for AIReceptionist.",
    )
    providers = parser.add_subparsers(dest="provider", required=True)
    netelip = providers.add_parser(
        "netelip",
        help="Plan or apply a Netelip inbound SIP integration.",
    )
    actions = netelip.add_subparsers(dest="action", required=True)
    for action in ("plan", "apply"):
        command = actions.add_parser(action)
        _add_netelip_arguments(command)
        if action == "apply":
            command.add_argument(
                "--yes",
                action="store_true",
                help="Create resources in the configured LiveKit Cloud project.",
            )

    args = parser.parse_args(argv)
    try:
        plan = _plan_from_args(args)
        output_dir = Path(args.output_dir)
        paths = plan.write(output_dir)
        _print_plan(plan, paths)
        if args.action == "plan":
            return 0
        if not args.yes:
            print(
                "\nDry run only. Re-run with --yes after reviewing the generated files."
            )
            return 0
        result = asyncio.run(provision_livekit(plan))
    except NetelipConfigError as exc:
        print(f"Netelip setup error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"LiveKit provisioning failed: {exc}", file=sys.stderr)
        return 1

    print("\n[OK] LiveKit provisioning complete")
    print(
        f"  Inbound trunk: {result.trunk_id} "
        f"({'created' if result.trunk_created else 'reused'})"
    )
    print(
        f"  Dispatch rule: {result.dispatch_rule_id} "
        f"({'created' if result.dispatch_created else 'reused'})"
    )
    print(f"  Netelip destination: {plan.sip_endpoint}")
    return 0


def _add_netelip_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--number",
        default=os.getenv("NETELIP_DID"),
        help="Netelip DID in E.164 format; defaults to NETELIP_DID.",
    )
    parser.add_argument(
        "--sip-endpoint",
        default=os.getenv("LIVEKIT_SIP_ENDPOINT"),
        help="LiveKit SIP hostname; defaults to LIVEKIT_SIP_ENDPOINT.",
    )
    parser.add_argument(
        "--business",
        default="example-dental",
        help="Business YAML slug passed to the receptionist agent.",
    )
    parser.add_argument(
        "--agent-name",
        default=os.getenv("RECEPTIONIST_AGENT_NAME") or "receptionist",
    )
    parser.add_argument(
        "--allowed-address",
        action="append",
        dest="allowed_addresses",
        help=(
            "Netelip source IP or CIDR. Repeat for multiple ranges. "
            "Defaults to the published Latin America SIP address."
        ),
    )
    parser.add_argument(
        "--security",
        choices=("auto", "auth", "ip"),
        default=os.getenv("NETELIP_SECURITY_MODE") or "auto",
        help=(
            "Inbound authentication mode. auto uses NETELIP_SIP_USERNAME and "
            "NETELIP_SIP_PASSWORD when both exist, otherwise the source IP."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="secrets/netelip",
        help="Private directory for generated JSON plans.",
    )


def _plan_from_args(args: argparse.Namespace) -> NetelipPlan:
    if not args.number:
        raise NetelipConfigError("Provide --number or set NETELIP_DID.")
    if not args.sip_endpoint:
        raise NetelipConfigError(
            "Provide --sip-endpoint or set LIVEKIT_SIP_ENDPOINT."
        )
    username = (os.getenv("NETELIP_SIP_USERNAME") or "").strip()
    password = (os.getenv("NETELIP_SIP_PASSWORD") or "").strip()
    if args.security in {"auto", "auth"} and bool(username) != bool(password):
        raise NetelipConfigError(
            "NETELIP_SIP_USERNAME and NETELIP_SIP_PASSWORD must be set together."
        )
    if args.security == "auth" and (not username or not password):
        raise NetelipConfigError(
            "security=auth requires NETELIP_SIP_USERNAME and NETELIP_SIP_PASSWORD."
        )
    use_auth = args.security == "auth" or (
        args.security == "auto" and bool(username and password)
    )

    addresses = args.allowed_addresses
    if use_auth and addresses is None:
        addresses = []
    elif addresses is None:
        configured = (os.getenv("NETELIP_ALLOWED_ADDRESSES") or "").strip()
        addresses = (
            [part.strip() for part in configured.split(",") if part.strip()]
            if configured
            else list(DEFAULT_NETELIP_LATAM_ADDRESSES)
        )
    return NetelipPlan.create(
        number=args.number,
        sip_endpoint=args.sip_endpoint,
        business=args.business,
        agent_name=args.agent_name,
        allowed_addresses=tuple(addresses),
        auth_username=username if use_auth else None,
        auth_password=password if use_auth else None,
    )


def _print_plan(plan: NetelipPlan, paths: tuple[Path, Path, Path]) -> None:
    print("Netelip -> LiveKit inbound plan")
    print(f"  DID: {plan.number}")
    print(f"  LiveKit SIP endpoint: {plan.sip_endpoint}")
    security = (
        f"SIP digest auth ({plan.auth_username})"
        if plan.auth_username
        else f"source IP ({', '.join(plan.allowed_addresses)})"
    )
    print(f"  Inbound security: {security}")
    print(f"  Agent: {plan.agent_name}")
    print(f"  Business config: {plan.business}")
    print("  Generated files:")
    for path in paths:
        print(f"    - {path}")
    if plan.allowed_addresses:
        print(
            "\nVerify the Netelip source IPs with support before production. "
            "The default is its currently published Latin America SIP address."
        )
