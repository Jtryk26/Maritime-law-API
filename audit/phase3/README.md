# Layout-audit: fase 3: typeskala og brede skærme

- Base-URL: `http://127.0.0.1:5173`
- Kørt: 2026-11-03T12:52:36+00:00
- Viewport-højde 900 px; tekst ≤ 13 px markeres; linjelængde markeres uden for 45–90 tegn ved ≥ 1024 px.
- Tegn/linje vises som *gennemsnit / median af fulde linjer* (hver bloks sidste linje udeladt). Markeringen bruger medianen.
- Trykflader: et felt i en `<label>` måles på etikettens flade.
- Fuld data i `results.json`. Skærmbilleder: `<side>-<bredde>-<tema>.png` (kun viewport).

## front — `#/`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 24 % | 34.7 / 45 | – | 1 | 1 (0) |
| 390 | dark | 0 | 100 | 92.8 | 24 % | 34.7 / 45 | – | 1 | 1 (0) |
| 1024 | light | 0 | 100 | 95.3 | 6.1 % | 33.2 / 35 | – | 1 | – |
| 1024 | dark | 0 | 100 | 95.3 | 6.1 % | 33.2 / 35 | – | 1 | – |
| 1440 | light | 0 | 81.9 | 78.6 | 6.1 % | 31.5 / 31 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 78.6 | 6.1 % | 31.5 / 31 | – | 1 | – |
| 1920 | light | 0 | 70.8 | 68.3 | 6.1 % | 30 / 31 | – | 1 | – |
| 1920 | dark | 0 | 70.8 | 68.3 | 6.1 % | 30 / 31 | – | 1 | – |

<details><summary>Fund</summary>

- 390/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 14 px — “← Forrige”
- 390/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 14 px — “← Forrige”
- 1024/light linjelængde: brødtekst 35 tegn/linje
- 1024/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 14.936 px — “← Forrige”
- 1024/dark linjelængde: brødtekst 35 tegn/linje
- 1024/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 14.936 px — “← Forrige”
- 1440/light linjelængde: brødtekst 31 tegn/linje
- 1440/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 15.5 px — “← Forrige”
- 1440/dark linjelængde: brødtekst 31 tegn/linje
- 1440/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 15.5 px — “← Forrige”
- 1920/light linjelængde: brødtekst 31 tegn/linje
- 1920/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 15.5 px — “← Forrige”
- 1920/dark linjelængde: brødtekst 31 tegn/linje
- 1920/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 15.5 px — “← Forrige”

</details>

## search — `#/?q=hviletid`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 25.4 % | 45.4 / 48 | – | 1 | 1 (0) |
| 390 | dark | 0 | 100 | 92.8 | 25.4 % | 45.4 / 48 | – | 0 | 1 (0) |
| 1024 | light | 0 | 100 | 67 | 7.9 % | 42 / 75 | – | 1 | – |
| 1024 | dark | 0 | 100 | 67 | 7.9 % | 42 / 75 | – | 0 | – |
| 1440 | light | 0 | 81.9 | 58.5 | 7.9 % | 42 / 75 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 58.5 | 7.9 % | 42 / 75 | – | 0 | – |
| 1920 | light | 0 | 70.8 | 53.2 | 7.9 % | 42 / 75 | – | 1 | – |
| 1920 | dark | 0 | 70.8 | 53.2 | 7.9 % | 42 / 75 | – | 0 | – |

<details><summary>Fund</summary>

- 390/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 12 px — “Støttedokument”
- 1024/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 12.5 px — “Støttedokument”
- 1440/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 12.5 px — “Støttedokument”
- 1920/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 12.5 px — “Støttedokument”

</details>

## document — `#/dokument/1?p=p-1`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 96.2 | 17 % | 45 / 50 | 36.5 / 45 | 0 | 2 (1) |
| 390 | dark | 0 | 100 | 96.2 | 17 % | 45 / 50 | 36.5 / 45 | 0 | 2 (1) |
| 1024 | light | 0 | 100 | 57.5 | 7.6 % | 78.8 / 94 | 53.8 / 71 | 0 | – |
| 1024 | dark | 0 | 100 | 57.5 | 7.6 % | 78.8 / 94 | 53.8 / 71 | 0 | – |
| 1440 | light | 0 | 81.9 | 47.5 | 7.1 % | 78.8 / 94 | 57 / 78 | 0 | – |
| 1440 | dark | 0 | 81.9 | 47.5 | 7.1 % | 78.8 / 94 | 57 / 78 | 0 | – |
| 1920 | light | 0 | 61.5 | 36.3 | 7.1 % | 78.8 / 94 | 57 / 78 | 0 | – |
| 1920 | dark | 0 | 61.5 | 36.3 | 7.1 % | 78.8 / 94 | 57 / 78 | 0 | – |

<details><summary>Fund</summary>

- 1024/light linjelængde: brødtekst 94 tegn/linje
- 1024/dark linjelængde: brødtekst 94 tegn/linje
- 1440/light linjelængde: brødtekst 94 tegn/linje
- 1440/dark linjelængde: brødtekst 94 tegn/linje
- 1920/light linjelængde: brødtekst 94 tegn/linje
- 1920/dark linjelængde: brødtekst 94 tegn/linje

</details>

## applicability — `#/anvendelighed`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 21.2 % | 36.6 / 46 | – | 4 | 1 (0) |
| 390 | dark | 0 | 100 | 92.8 | 21.2 % | 36.6 / 46 | – | 2 | 1 (0) |
| 1024 | light | 0 | 100 | 58.2 | 7.2 % | 61.5 / 80 | – | 4 | – |
| 1024 | dark | 0 | 100 | 58.2 | 7.2 % | 61.5 / 80 | – | 2 | – |
| 1440 | light | 0 | 81.9 | 46.1 | 7.2 % | 67.2 / 83 | – | 4 | – |
| 1440 | dark | 0 | 81.9 | 46.1 | 7.2 % | 67.2 / 83 | – | 2 | – |
| 1920 | light | 0 | 70.8 | 44 | 7.2 % | 67.2 / 85 | – | 4 | – |
| 1920 | dark | 0 | 70.8 | 44 | 7.2 % | 67.2 / 85 | – | 2 | – |

<details><summary>Fund</summary>

- 390/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 13 px — “Ordret skoptekst fra Retsinformation:”
- 390/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 390/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 12 px — “Profilkomplethed:”
- 390/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 390/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 12 px — “Profilkomplethed:”
- 1024/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 13.624 px — “Ordret skoptekst fra Retsinformation:”
- 1024/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1024/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 12.5 px — “Profilkomplethed:”
- 1024/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1024/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 12.5 px — “Profilkomplethed:”
- 1440/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 14 px — “Ordret skoptekst fra Retsinformation:”
- 1440/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1440/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 12.5 px — “Profilkomplethed:”
- 1440/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1440/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 12.5 px — “Profilkomplethed:”
- 1920/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 14 px — “Ordret skoptekst fra Retsinformation:”
- 1920/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1920/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 12.5 px — “Profilkomplethed:”
- 1920/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12.5 px — “Vurdering: Gælder ikke”
- 1920/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 12.5 px — “Profilkomplethed:”

</details>
