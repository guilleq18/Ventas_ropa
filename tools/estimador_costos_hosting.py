#!/usr/bin/env python3
"""Estimador simple de costos mensuales para hosting multi-cliente.

Supuestos base (editables):
- Oracle A1 PAYG: $0.01/OCPU-h y $0.0015/GB-h
- Block Volume Oracle: $0.043/GB-mes (estimado desde pricing publico)
- Always Free Oracle: 4 OCPU, 24 GB RAM, 200 GB block por cuenta
- Horas/mes: 730

Uso:
  python tools/estimador_costos_hosting.py
"""

from __future__ import annotations

from dataclasses import dataclass


HOURS_PER_MONTH = 730

# Oracle PAYG (ajustar si cambia pricing)
ORACLE_A1_OCPU_H = 0.01
ORACLE_A1_GB_H = 0.0015
ORACLE_BLOCK_GB_MONTH = 0.043

# Beneficio Always Free (por cuenta)
ORACLE_FREE_OCPU = 4
ORACLE_FREE_RAM_GB = 24
ORACLE_FREE_BLOCK_GB = 200

# Proveedores por VM (referencia mensual)
CHEAP_VPS_PRICING = {
    "AWS Lightsail 1GB": 5.00,
    "Linode 1GB": 5.00,
    "DigitalOcean 1GB": 6.00,
    "Hetzner CAX11": 5.49,
    "Contabo VPS 10 (US ejemplo)": 5.45,
}


@dataclass(frozen=True)
class OracleProfile:
    ocpu: float
    ram_gb: float
    block_gb: float
    vms: int


def oracle_monthly_raw(profile: OracleProfile) -> float:
    ocpu_cost = profile.ocpu * ORACLE_A1_OCPU_H * HOURS_PER_MONTH
    ram_cost = profile.ram_gb * ORACLE_A1_GB_H * HOURS_PER_MONTH
    block_cost = profile.block_gb * ORACLE_BLOCK_GB_MONTH
    return (ocpu_cost + ram_cost + block_cost) * profile.vms


def oracle_monthly_net(profile: OracleProfile) -> float:
    used_ocpu = profile.ocpu * profile.vms
    used_ram = profile.ram_gb * profile.vms
    used_block = profile.block_gb * profile.vms

    billable_ocpu = max(0.0, used_ocpu - ORACLE_FREE_OCPU)
    billable_ram = max(0.0, used_ram - ORACLE_FREE_RAM_GB)
    billable_block = max(0.0, used_block - ORACLE_FREE_BLOCK_GB)

    ocpu_cost = billable_ocpu * ORACLE_A1_OCPU_H * HOURS_PER_MONTH
    ram_cost = billable_ram * ORACLE_A1_GB_H * HOURS_PER_MONTH
    block_cost = billable_block * ORACLE_BLOCK_GB_MONTH
    return ocpu_cost + ram_cost + block_cost


def oracle_isolated_profile(customers: int) -> OracleProfile:
    # 1 VM por cliente
    return OracleProfile(ocpu=1, ram_gb=2, block_gb=50, vms=customers)


def oracle_multitenant_profile(customers: int) -> OracleProfile:
    # Un perfil simple por tramo (ajustalo a tu carga real)
    if customers <= 3:
        return OracleProfile(ocpu=2, ram_gb=8, block_gb=100, vms=1)
    if customers <= 10:
        return OracleProfile(ocpu=4, ram_gb=16, block_gb=250, vms=1)
    if customers <= 20:
        return OracleProfile(ocpu=8, ram_gb=32, block_gb=500, vms=1)
    return OracleProfile(ocpu=16, ram_gb=64, block_gb=1000, vms=1)


def fmt(value: float) -> str:
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def print_block(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def run() -> None:
    customers_list = [3, 10, 20, 50]

    print_block("Oracle PAYG - Escenario A (1 VM por empresa)")
    print("Clientes | Bruto USD/mes | Neto c/Always Free USD/mes | USD/cliente")
    for customers in customers_list:
        profile = oracle_isolated_profile(customers)
        raw = oracle_monthly_raw(profile)
        net = oracle_monthly_net(profile)
        per_customer = net / customers
        print(
            f"{customers:8d} | {fmt(raw):14s} | {fmt(net):28s} | {fmt(per_customer):11s}"
        )

    print_block("Oracle PAYG - Escenario B (multitenant)")
    print("Clientes | Perfil (OCPU/RAM/Block) | Bruto USD/mes | Neto c/Always Free USD/mes")
    for customers in customers_list:
        profile = oracle_multitenant_profile(customers)
        raw = oracle_monthly_raw(profile)
        net = oracle_monthly_net(profile)
        profile_txt = f"{profile.ocpu:g}/{profile.ram_gb:g}GB/{profile.block_gb:g}GB"
        print(
            f"{customers:8d} | {profile_txt:23s} | {fmt(raw):14s} | {fmt(net):28s}"
        )

    print_block("Alternativas VPS (1 VM por empresa)")
    print("Proveedor | USD/mes por VM | 3 clientes | 10 clientes | 20 clientes | 50 clientes")
    for name, price in CHEAP_VPS_PRICING.items():
        c3 = price * 3
        c10 = price * 10
        c20 = price * 20
        c50 = price * 50
        print(
            f"{name:33s} | {fmt(price):14s} | {fmt(c3):10s} | {fmt(c10):11s} | {fmt(c20):11s} | {fmt(c50):11s}"
        )


if __name__ == "__main__":
    run()
