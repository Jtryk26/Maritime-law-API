# Layout-audit: fase 2: telefon (integration/layout-timeline, ikke committet endnu)

- Base-URL: `http://127.0.0.1:5173`
- Kørt: 2026-11-03T12:40:36+00:00
- Viewport-højde 900 px; tekst ≤ 13 px markeres; linjelængde markeres uden for 45–90 tegn ved ≥ 1024 px.
- Fuld data i `results.json`. Skærmbilleder: `<side>-<bredde>-<tema>.png` (kun viewport).

## front — `#/`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 42.3 % | 34.7 | – | 1 | 2 (0) |
| 390 | dark | 0 | 100 | 92.8 | 42.3 % | 34.7 | – | 1 | 2 (0) |
| 768 | light | 0 | 100 | 96.4 | 42.3 % | 53.4 | – | 1 | – |
| 768 | dark | 0 | 100 | 96.4 | 42.3 % | 53.4 | – | 1 | – |
| 1024 | light | 0 | 100 | 95.3 | 42.6 % | 31.5 | – | 1 | – |
| 1024 | dark | 0 | 100 | 95.3 | 42.6 % | 31.5 | – | 1 | – |
| 1440 | light | 0 | 81.9 | 78.6 | 20.7 % | 30 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 78.6 | 20.7 % | 30 | – | 1 | – |

<details><summary>Fund</summary>

- 390/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 390/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 13.5 px — “← Forrige”
- 768/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 768/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 13.5 px — “← Forrige”
- 1024/light linjelængde: brødtekst 31.5 tegn/linje
- 1024/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 1024/dark linjelængde: brødtekst 31.5 tegn/linje
- 1024/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 13.5 px — “← Forrige”
- 1440/light linjelængde: brødtekst 30 tegn/linje
- 1440/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 1440/dark linjelængde: brødtekst 30 tegn/linje
- 1440/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 13.5 px — “← Forrige”

</details>

## search — `#/?q=hviletid`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 42.3 % | 45.4 | – | 1 | 1 (0) |
| 390 | dark | 0 | 100 | 92.8 | 42.3 % | 45.4 | – | 0 | 1 (0) |
| 768 | light | 0 | 100 | 96.4 | 42.3 % | 54.5 | – | 1 | – |
| 768 | dark | 0 | 100 | 96.4 | 42.3 % | 54.5 | – | 0 | – |
| 1024 | light | 0 | 100 | 67 | 42.8 % | 42 | – | 1 | – |
| 1024 | dark | 0 | 100 | 67 | 42.8 % | 42 | – | 0 | – |
| 1440 | light | 0 | 81.9 | 58.5 | 26.1 % | 42 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 58.5 | 26.1 % | 42 | – | 0 | – |

<details><summary>Fund</summary>

- 390/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 768/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1024/light linjelængde: brødtekst 42 tegn/linje
- 1024/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1024/dark linjelængde: brødtekst 42 tegn/linje
- 1440/light linjelængde: brødtekst 42 tegn/linje
- 1440/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1440/dark linjelængde: brødtekst 42 tegn/linje

</details>

## applicability — `#/anvendelighed`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 54.5 % | 37.9 | – | 4 | 1 (0) |
| 390 | dark | 0 | 100 | 92.8 | 54.5 % | 37.9 | – | 2 | 1 (0) |
| 768 | light | 0 | 100 | 96.4 | 54.5 % | 67.2 | – | 4 | – |
| 768 | dark | 0 | 100 | 96.4 | 54.5 % | 67.2 | – | 2 | – |
| 1024 | light | 0 | 100 | 60.2 | 59.5 % | 67.2 | – | 4 | – |
| 1024 | dark | 0 | 100 | 60.2 | 59.5 % | 67.2 | – | 2 | – |
| 1440 | light | 0 | 81.9 | 46.1 | 18.2 % | 62.5 | – | 4 | – |
| 1440 | dark | 0 | 81.9 | 46.1 | 18.2 % | 62.5 | – | 2 | – |

<details><summary>Fund</summary>

- 390/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 11.5 px — “Ordret skoptekst fra Retsinformation:”
- 390/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 390/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 11.5 px — “Profilkomplethed:”
- 390/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 390/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 11.5 px — “Profilkomplethed:”
- 768/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 11.5 px — “Ordret skoptekst fra Retsinformation:”
- 768/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 768/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 11.5 px — “Profilkomplethed:”
- 768/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 768/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 11.5 px — “Profilkomplethed:”
- 1024/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 11.5 px — “Ordret skoptekst fra Retsinformation:”
- 1024/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1024/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 11.5 px — “Profilkomplethed:”
- 1024/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1024/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 11.5 px — “Profilkomplethed:”
- 1440/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 12.5 px — “Ordret skoptekst fra Retsinformation:”
- 1440/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1440/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 11.5 px — “Profilkomplethed:”
- 1440/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1440/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 11.5 px — “Profilkomplethed:”

</details>
