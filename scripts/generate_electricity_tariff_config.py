#!/usr/bin/env python3
"""Interactively generate validated ELECTRICITY_TARIFF_CONFIG_JSON."""
from __future__ import annotations

import json
from datetime import datetime


def ask_text(prompt: str, *, required: bool = True) -> str:
    while True:
        value = input(prompt).strip()
        if value or not required:
            return value
        print("A value is required.")


def ask_date(prompt: str) -> str:
    while True:
        value = ask_text(prompt)
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value
        except ValueError:
            print("Use YYYY-MM-DD.")


def ask_number(prompt: str, *, minimum: float = 0.0, maximum: float | None = None, optional: bool = False) -> float:
    while True:
        raw = input(prompt).strip()
        if optional and not raw:
            return 0.0
        try:
            value = float(raw)
        except ValueError:
            print("Enter a numeric value.")
            continue
        if value < minimum or (maximum is not None and value > maximum):
            range_text = f" between {minimum} and {maximum}" if maximum is not None else f" of at least {minimum}"
            print(f"Enter a value{range_text}.")
            continue
        return value


def ask_tiers() -> list[dict[str, float | None]]:
    print("\nProgressive tiers")
    print("Enter cumulative upper limits. Leave the final limit blank for the unlimited tier.")
    tiers: list[dict[str, float | None]] = []
    previous = 0.0
    while True:
        label = len(tiers) + 1
        raw_limit = input(f"Tier {label} upper limit kWh (blank = unlimited final tier): ").strip()
        if not raw_limit:
            limit = None
        else:
            try:
                limit = float(raw_limit)
            except ValueError:
                print("Enter a numeric cumulative upper limit.")
                continue
            if limit <= previous:
                print(f"Upper limit must be greater than {previous:g}.")
                continue
        rate = ask_number(f"Tier {label} rate THB/kWh: ")
        tiers.append({"up_to_kwh": limit, "rate": rate})
        if limit is None:
            break
        previous = limit
    return tiers


def main() -> int:
    print("Smart Condo Dashboard — Electricity Tariff Configuration")
    print("Use tariff values from your chosen published source. This helper does not supply official rates.\n")
    config = {
        "tariff_name": ask_text("Tariff name: "),
        "effective_date": ask_date("Effective date (YYYY-MM-DD): "),
        "tiers": ask_tiers(),
        "ft_rate": ask_number("Ft rate per kWh: "),
        "service_charge": ask_number("Service charge THB: "),
        "vat_percent": ask_number("VAT percent: ", maximum=100.0),
        "minimum_charge": ask_number("Optional minimum charge THB (blank = 0): ", optional=True),
    }
    compact = json.dumps(config, separators=(",", ":"), ensure_ascii=False)
    print("\nAdd the following line to the service environment, then restart the service:\n")
    print(f"ELECTRICITY_TARIFF_CONFIG_JSON='{compact}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
