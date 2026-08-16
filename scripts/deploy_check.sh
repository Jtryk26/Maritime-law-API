#!/usr/bin/env bash
# =============================================================================
# Kontrol før offentlig udgivelse
# =============================================================================
# Kører igennem de fejl, der er nemme at begå og dyre at opdage bagefter:
# et manglende administratortoken, databasens standardkodeord, en glemt
# /docs-flade. Kaldes automatisk af `make tunnel-up`.
#
#   scripts/deploy_check.sh
#
# Afslutter med 1, hvis noget er galt. Advarsler alene stopper ikke.
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

ENV_FILE="${ENV_FILE:-.env}"
errors=0
warnings=0

red()   { printf '\033[31m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }
amber() { printf '\033[33m%s\033[0m\n' "$1"; }

fail() { red   "  FEJL     $1"; errors=$((errors + 1)); }
warn() { amber "  ADVARSEL $1"; warnings=$((warnings + 1)); }
ok()   { green "  OK       $1"; }

# Læser en nøgle fra .env uden at eksekvere filen.
value_of() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 | sed 's/^"//; s/"$//'
}

echo "Kontrollerer $ENV_FILE før offentlig udgivelse"
echo

if [ ! -f "$ENV_FILE" ]; then
  fail "$ENV_FILE findes ikke. Kør: cp .env.example .env"
  echo
  red "Kontrollen fejlede."
  exit 1
fi

# --- Administratortoken ------------------------------------------------------
token="$(value_of ADMIN_API_TOKEN)"
if [ -z "$token" ]; then
  fail "ADMIN_API_TOKEN er tom. Import, vektorisering og driftsdata kan ikke beskyttes."
  echo "           Generér et: make admin-token"
elif [ "${#token}" -lt 24 ]; then
  fail "ADMIN_API_TOKEN er kun ${#token} tegn. Brug mindst 24 (make admin-token)."
else
  ok "ADMIN_API_TOKEN er sat (${#token} tegn)."
fi

# --- Databasekodeord ---------------------------------------------------------
password="$(value_of POSTGRES_PASSWORD)"
if [ -z "$password" ]; then
  fail "POSTGRES_PASSWORD er tom."
elif [ "$password" = "maritim" ] || [ "$password" = "postgres" ] || [ "$password" = "password" ]; then
  fail "POSTGRES_PASSWORD er stadig standardværdien '$password'."
elif [ "${#password}" -lt 12 ]; then
  warn "POSTGRES_PASSWORD er kortere end 12 tegn."
else
  ok "POSTGRES_PASSWORD er skiftet."
fi

# --- Tunneltoken -------------------------------------------------------------
if [ -z "$(value_of TUNNEL_TOKEN)" ]; then
  fail "TUNNEL_TOKEN er tom. Hentes i Cloudflare Zero Trust > Networks > Tunnels."
else
  ok "TUNNEL_TOKEN er sat."
fi

# --- Miljø -------------------------------------------------------------------
environment="$(value_of ENVIRONMENT)"
if [ "$environment" != "production" ]; then
  warn "ENVIRONMENT=${environment:-<tom>}. Tunnel-opsætningen sætter selv production, men .env bør følge med."
else
  ok "ENVIRONMENT=production."
fi

docs="$(value_of EXPOSE_API_DOCS)"
if [ "$docs" = "true" ]; then
  warn "EXPOSE_API_DOCS=true i .env. Tunnel-opsætningen tvinger den til false."
else
  ok "API-dokumentationen udstilles ikke."
fi

cors="$(value_of CORS_ORIGINS)"
if [ "$cors" = "*" ]; then
  fail "CORS_ORIGINS=* tillader ethvert websted at kalde API'et fra en browser."
else
  ok "CORS_ORIGINS er afgrænset."
fi

# --- .env må aldrig committes ------------------------------------------------
if git ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  fail "$ENV_FILE er sporet af git. Fjern den: git rm --cached $ENV_FILE"
else
  ok "$ENV_FILE er ikke sporet af git."
fi

echo
if [ "$errors" -gt 0 ]; then
  red "$errors fejl og $warnings advarsler. Udgiv ikke før fejlene er rettet."
  exit 1
fi

if [ "$warnings" -gt 0 ]; then
  amber "Ingen fejl, $warnings advarsler."
else
  green "Alt i orden."
fi
exit 0
