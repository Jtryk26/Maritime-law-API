# Layout-audit

`run_audit.py` måler frontendens layout på telefon og skrivebord: vandret overløb,
skærmudnyttelse, skriftstørrelser, linjelængde, kontrast og (ved 390 px) trykflader.
Hver kørsel skriver viewport-skærmbilleder, `results.json` og en `README.md` med
oversigtstabeller til den mappe, der gives med `--out`.

| Mappe | Indhold |
|---|---|
| `baseline-main/` | `origin/main` a6a93ef, uden patches |
| `before/` | `integration/layout-timeline` 62d3c54, med de to patches |
| `live/` | https://xn--slov-gra.dk, 390 og 1440 px, lyst tema |
| `phase2/` | efter fase 2 (telefon): forside, søgning og Skibsvurdering, 390–1440 px |
| `phase3/` | efter fase 3 (typeskala, brede skærme): alle fire sider, 390–1920 px, med `--open-applicability-form` |
| `phase4/` | efter fase 4 (kontrast, lovspalte 65ch): alle fire sider, 390–1920 px, med `--open-applicability-form` |

## Installation

Playwright installeres i backendens virtuelle miljø (eller et andet Python ≥ 3.11):

```sh
backend/.venv/Scripts/python -m pip install -r audit/requirements.txt   # Windows
backend/.venv/bin/python -m pip install -r audit/requirements.txt       # Linux/macOS
python -m playwright install chromium
```

## Kørsel

Kør mod en lokal backend med en frisk SQLite-database og Vite-dev-serveren
(`npm run dev` i `frontend/`, som sender `/api` videre til backend). Brug aldrig
produktionsdatabasen.

```sh
python audit/run_audit.py --out audit/after --base-url http://127.0.0.1:5173
```

Valgfrit: `--doc-id`, `--widths 390,1440`, `--themes light`, `--pages front,document`,
`--pause 3` (sekunder mellem sideindlæsninger, f.eks. mod den offentlige side) og
`--open-applicability-form` (Skibsvurdering ≤ 860 px: åbn den sammenklappede formular
før målingen; formularen gemmes også som `<side>-<bredde>-<tema>-form.png`).

Fra fase 3 måles også *medianen af fulde linjer* (hver bloks sidste linje udeladt), og
et felt i en `<label>` måles på etikettens flade. `baseline-main/`, `before/` og `phase2/`
er målt før dette og har kun gennemsnittet.
