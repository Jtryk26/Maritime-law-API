# Layout-audit: live: https://xn--slov-gra.dk (sølov.dk)

- Base-URL: `https://xn--slov-gra.dk`
- Kørt: 2026-11-03T12:30:47+00:00
- Viewport-højde 900 px; tekst ≤ 13 px markeres; linjelængde markeres uden for 45–90 tegn ved ≥ 1024 px.
- Fuld data i `results.json`. Skærmbilleder: `<side>-<bredde>-<tema>.png` (kun viewport).

## front — `#/`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 42.8 % | 42.4 | – | 1 | 13 (0) |
| 1440 | light | 0 | 81.9 | 78.6 | 43.4 % | 34 | – | 1 | – |

<details><summary>Fund</summary>

- 390/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 1440/light linjelængde: brødtekst 34 tegn/linje
- 1440/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”

</details>

## document — `#/dokument/2423?p=p-1`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 96.2 | 9.7 % | 39.3 | 31.9 | 0 | 4 (1) |
| 1440 | light | 0 | 81.9 | 38.7 | 9.7 % | 34.7 | 40.4 | 0 | – |

<details><summary>Fund</summary>

- 1440/light linjelængde: brødtekst 34.7 tegn/linje
- 1440/light linjelængde: lovtekst 40.4 tegn/linje

</details>

## applicability — `#/anvendelighed`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 82.6 | 81.6 % | 35.1 | – | 1 | 18 (0) |
| 1440 | light | 0 | 81.9 | 48.9 | 81.6 % | 82 | – | 1 | – |

<details><summary>Fund</summary>

- 390/light kontrast 4.16:1 (kræver 4.5): `div.engine-meta > span.engine-hash > code` #66737f på #f2ede2, 12 px — “eval-ok”
- 1440/light kontrast 4.16:1 (kræver 4.5): `div.engine-meta > span.engine-hash > code` #66737f på #f2ede2, 12 px — “eval-ok”

</details>
