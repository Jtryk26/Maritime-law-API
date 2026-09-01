"""Udtrækker og klassificerer samtlige FastAPI-ruter direkte fra applikationen."""

import sys
from pathlib import Path

# Sørg for at backend mappen er i sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Settings
from app.main import create_app


def classify_route(path: str, method: str) -> tuple[str, str, str]:
    if path == "/health":
        return ("SYSTEM_HEALTH", "Ingen (offentlig)", "Ja (Docker & Nginx)")
    if path.startswith("/api/applicability/evaluate"):
        return ("PUBLIC_EVALUATION", "Ingen (offentlig)", "Ja (Nginx & Cloudflare)")
    if path.startswith("/api/applicability/fields") or path.startswith("/api/applicability/rules/"):
        return ("PUBLIC_READ", "Ingen (offentlig)", "Ja (Nginx & Cloudflare)")
    if path == "/api/search/queries":
        return ("ADMIN_ONLY (Mutating/Drift)", "ADMIN_API_TOKEN (Bearer)", "Nej i prod (udeladt af app-factory)")
    if path.startswith("/api/search") or path.startswith("/api/documents") or path.startswith("/api/categories") or path.startswith("/api/facets") or path.startswith("/api/core-laws"):
        return ("PUBLIC_READ", "Ingen (offentlig)", "Ja (Nginx & Cloudflare)")
    if path.startswith("/api/import") or path.startswith("/api/embeddings") or path.startswith("/api/stats") or path.startswith("/api/admin") or "/drafts" in path or "/review" in path:
        return ("ADMIN_ONLY (Mutating/Drift)", "ADMIN_API_TOKEN (Bearer)", "Nej i prod (udeladt af app-factory)")
    return ("INTERNAL_ONLY", "Admin/System", "Nej")


def main():
    print("=========================================================================================================")
    print("FASTAPI ROUTE INVENTORY (PRODUCTION VS DEVELOPMENT)")
    print("=========================================================================================================\n")

    # 1. Produktionsapplikation (Ren offentlig læse- og evalueringsflade)
    prod_app = create_app(
        Settings(
            enable_admin_api=False,
            expose_api_docs=False,
            run_migrations_on_startup=False,
            environment="production",
        )
    )
    print("--- [1] PRODUKTIONSRUTER (ENABLE_ADMIN_API=false, EXPOSE_API_DOCS=false) ---")
    print(f"{'Metode':<8} | {'Sti':<42} | {'Klassifikation':<20} | {'Autentifikation':<22}")
    print("-" * 105)
    for route in sorted(prod_app.routes, key=lambda r: getattr(r, "path", "")):
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        methods_str = ",".join(m for m in methods if m not in {"HEAD", "OPTIONS"})
        if not methods_str:
            continue
        cls, auth, _ = classify_route(path, methods_str)
        print(f"{methods_str:<8} | {path:<42} | {cls:<20} | {auth:<22}")

    print("\n" + "=" * 105 + "\n")

    # 2. Udviklings- og administrationsapplikation
    dev_app = create_app(Settings(enable_admin_api=True, expose_api_docs=True, environment="development"))
    print("--- [2] UDVIKLINGS- OG ADMINISTRATIONS-API (ENABLE_ADMIN_API=true) ---")
    print(f"{'Metode':<8} | {'Sti':<42} | {'Klassifikation':<25} | {'Autentifikation':<25} | {'Eksponeret'}")
    print("-" * 125)
    for route in sorted(dev_app.routes, key=lambda r: getattr(r, "path", "")):
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        methods_str = ",".join(m for m in methods if m not in {"HEAD", "OPTIONS"})
        if not methods_str:
            continue
        cls, auth, exp = classify_route(path, methods_str)
        print(f"{methods_str:<8} | {path:<42} | {cls:<25} | {auth:<25} | {exp}")


if __name__ == "__main__":
    main()
