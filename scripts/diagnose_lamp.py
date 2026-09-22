#!/usr/bin/env python3
"""Probe one miIO lamp from the command line, outside Home Assistant.

Usage:
    .venv/bin/python scripts/diagnose_lamp.py 192.168.1.50 <32-char-token>
    .venv/bin/python scripts/diagnose_lamp.py --scan 192.168.1.0/24

The raw handshake stage needs no token and no python-miio, so it separates
"the lamp does not speak miIO here" from "the token is wrong".
"""

import argparse
import binascii
import ipaddress
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

MIIO_PORT = 54321
HANDSHAKE = b"\x21\x31\x00\x20" + b"\xff" * 28
STATUS_PROPERTIES = (
    "power",
    "bright",
    "notifystatus",
    "ambstatus",
    "ambvalue",
    "eyecare",
    "scene_num",
    "bls",
    "dvalue",
)


def hr(title: str) -> None:
    print(f"\n=== {title} ===")


def raw_handshake(host: str, timeout: float = 2.0) -> dict:
    """Send a bare miIO hello and decode the 32-byte reply header."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    started = time.monotonic()
    try:
        sock.sendto(HANDSHAKE, (host, MIIO_PORT))
        data, _ = sock.recvfrom(1024)
    except socket.timeout:
        return {"ok": False, "error": f"no UDP reply within {timeout}s"}
    except OSError as err:
        return {"ok": False, "error": f"{type(err).__name__}: {err}"}
    finally:
        sock.close()

    elapsed_ms = round((time.monotonic() - started) * 1000)
    if len(data) < 32 or data[:2] != b"\x21\x31":
        return {"ok": False, "error": f"not a miIO reply: {binascii.hexlify(data[:16])!r}"}

    magic, length, unknown, device_id, stamp = struct.unpack(">HHIII", data[:16])
    token_field = data[16:32]
    if token_field == b"\xff" * 16:
        token_note = "device is provisioned (token withheld)"
    elif token_field == b"\x00" * 16:
        token_note = "device is in setup mode (token is all zeros)"
    else:
        token_note = f"device handed out a token: {binascii.hexlify(token_field).decode()}"
    return {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "device_id": device_id,
        "uptime_s": stamp,
        "token_note": token_note,
    }


def scan(network: str) -> list[str]:
    """Find every host on a subnet that answers a miIO handshake."""
    hosts = [str(h) for h in ipaddress.ip_network(network, strict=False).hosts()]
    found = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        for host, result in zip(hosts, pool.map(lambda h: raw_handshake(h, 0.6), hosts)):
            if result.get("ok"):
                print(f"  {host}  device_id={result['device_id']}  {result['token_note']}")
                found.append(host)
    return found


def probe_with_miio(host: str, token: str) -> None:
    try:
        from miio import DeviceException, PhilipsEyecare
    except ImportError:
        print("python-miio is not installed; run: pip install python-miio==0.5.12")
        return

    device = PhilipsEyecare(host, token, timeout=3, model="philips.light.sread2")

    hr("device info")
    try:
        info = device.info()
        print(f"  model:     {info.model}")
        print(f"  firmware:  {info.firmware_version}")
        print(f"  hardware:  {info.hardware_version}")
        print(f"  mac:       {info.mac_address}")
        if info.model != "philips.light.sread2":
            print(f"  NOTE: the integration only supports philips.light.sread2, not {info.model}")
    except DeviceException as err:
        print(f"  FAILED: {type(err).__name__}: {err}")
        print("  A failure here after a successful handshake almost always means a wrong token.")
        return

    hr("all properties in one request (what the integration does)")
    try:
        values = device.get_properties(list(STATUS_PROPERTIES))
        print(f"  requested {len(STATUS_PROPERTIES)}, received {len(values)}")
        for name, value in zip(STATUS_PROPERTIES, values):
            print(f"    {name:<13} {value!r}")
        missing = STATUS_PROPERTIES[len(values) :]
        if missing:
            print(f"  MISSING: {', '.join(missing)} - this firmware answers a shorter list")
    except DeviceException as err:
        print(f"  FAILED: {type(err).__name__}: {err}")

    hr("each property on its own (firmware that rejects long requests)")
    for name in STATUS_PROPERTIES:
        try:
            print(f"    {name:<13} {device.send('get_prop', [name])!r}")
        except DeviceException as err:
            print(f"    {name:<13} FAILED: {type(err).__name__}: {err}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", help="lamp IP address")
    parser.add_argument("token", nargs="?", help="32-character miIO token")
    parser.add_argument("--scan", metavar="CIDR", help="find miIO devices on a subnet")
    args = parser.parse_args()

    if args.scan:
        hr(f"scanning {args.scan} for miIO devices on UDP {MIIO_PORT}")
        if not scan(args.scan):
            print("  nothing answered; the lamp is on another subnet or blocked")
        return 0

    if not args.host:
        parser.error("give a host, or use --scan")

    hr(f"raw miIO handshake to {args.host}:{MIIO_PORT} (no token needed)")
    handshake = raw_handshake(args.host)
    if not handshake["ok"]:
        print(f"  FAILED: {handshake['error']}")
        print("  The lamp does not answer miIO from this machine, even though ping may work:")
        print("   - Home Assistant in Docker with bridge networking cannot reach it")
        print("   - the lamp sits on a guest network / another VLAN, or client isolation is on")
        print("   - the IP now belongs to another device (pin it in DHCP)")
        return 1
    print(f"  OK in {handshake['elapsed_ms']} ms")
    print(f"  device_id: {handshake['device_id']}")
    print(f"  uptime:    {handshake['uptime_s']} s")
    print(f"  {handshake['token_note']}")

    if not args.token:
        print("\nPass the token as a second argument to continue with authenticated calls.")
        return 0

    token = args.token.strip().lower()
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        print(f"\nThe token must be 32 hex characters; got {len(token)}.")
        return 1

    probe_with_miio(args.host, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
