# Layout-audit: before: integration/layout-timeline 62d3c54 (med patches)

- Base-URL: `http://127.0.0.1:5173`
- Kørt: 2026-11-03T12:28:26+00:00
- Viewport-højde 900 px; tekst ≤ 13 px markeres; linjelængde markeres uden for 45–90 tegn ved ≥ 1024 px.
- Fuld data i `results.json`. Skærmbilleder: `<side>-<bredde>-<tema>.png` (kun viewport).

## front — `#/`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 42.3 % | 34.7 | – | 1 | 12 (0) |
| 390 | dark | 0 | 100 | 92.8 | 42.3 % | 34.7 | – | 1 | 12 (0) |
| 768 | light | 0 | 100 | 96.4 | 42.3 % | 53.4 | – | 1 | – |
| 768 | dark | 0 | 100 | 96.4 | 42.3 % | 53.4 | – | 1 | – |
| 1024 | light | 0 | 100 | 95.3 | 42.6 % | 31.5 | – | 1 | – |
| 1024 | dark | 0 | 100 | 95.3 | 42.6 % | 31.5 | – | 1 | – |
| 1440 | light | 0 | 81.9 | 78.6 | 20.7 % | 30 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 78.6 | 20.7 % | 30 | – | 1 | – |
| 1920 | light | 0 | 61.5 | 59 | 20.7 % | 30 | – | 1 | – |
| 1920 | dark | 0 | 61.5 | 59 | 20.7 % | 30 | – | 1 | – |

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
- 1920/light linjelængde: brødtekst 30 tegn/linje
- 1920/light kontrast 3.03:1 (kræver 4.5): `section > nav.pagination > button` #8d9499 på #fffdf8, 13.5 px — “← Forrige”
- 1920/dark linjelængde: brødtekst 30 tegn/linje
- 1920/dark kontrast 4.2:1 (kræver 4.5): `section > nav.pagination > button` #7c8794 på #162638, 13.5 px — “← Forrige”

</details>

## search — `#/?q=hviletid`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 42.3 % | 45.4 | – | 1 | 5 (0) |
| 390 | dark | 0 | 100 | 92.8 | 42.3 % | 45.4 | – | 0 | 5 (0) |
| 768 | light | 0 | 100 | 96.4 | 42.3 % | 54.5 | – | 1 | – |
| 768 | dark | 0 | 100 | 96.4 | 42.3 % | 54.5 | – | 0 | – |
| 1024 | light | 0 | 100 | 67 | 42.8 % | 42 | – | 1 | – |
| 1024 | dark | 0 | 100 | 67 | 42.8 % | 42 | – | 0 | – |
| 1440 | light | 0 | 81.9 | 58.5 | 26.1 % | 42 | – | 1 | – |
| 1440 | dark | 0 | 81.9 | 58.5 | 26.1 % | 42 | – | 0 | – |
| 1920 | light | 0 | 61.5 | 43.9 | 26.1 % | 42 | – | 1 | – |
| 1920 | dark | 0 | 61.5 | 43.9 | 26.1 % | 42 | – | 0 | – |

<details><summary>Fund</summary>

- 390/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 768/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1024/light linjelængde: brødtekst 42 tegn/linje
- 1024/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1024/dark linjelængde: brødtekst 42 tegn/linje
- 1440/light linjelængde: brødtekst 42 tegn/linje
- 1440/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1440/dark linjelængde: brødtekst 42 tegn/linje
- 1920/light linjelængde: brødtekst 42 tegn/linje
- 1920/light kontrast 4.16:1 (kræver 4.5): `article.result > div.result-ref > span.law-class.law-support` #66737f på #f2ede2, 10.5 px — “Støttedokument”
- 1920/dark linjelængde: brødtekst 42 tegn/linje

</details>

## document — `#/dokument/1?p=p-1`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 96.2 | 31.9 % | 45 | 37.9 | 0 | 2 (1) |
| 390 | dark | 0 | 100 | 96.2 | 31.9 % | 45 | 37.9 | 0 | 2 (1) |
| 768 | light | 0 | 100 | 70.3 | 31.9 % | 78.8 | 53.8 | 0 | – |
| 768 | dark | 0 | 100 | 70.3 | 31.9 % | 78.8 | 53.8 | 0 | – |
| 1024 | light | 0 | 100 | 54.4 | 31.9 % | 78.8 | 53.8 | 0 | – |
| 1024 | dark | 0 | 100 | 54.4 | 31.9 % | 78.8 | 53.8 | 0 | – |
| 1440 | light | 0 | 81.9 | 38.7 | 16.6 % | 78.8 | 53.8 | 0 | – |
| 1440 | dark | 0 | 81.9 | 38.7 | 16.6 % | 78.8 | 53.8 | 0 | – |
| 1920 | light | 0 | 61.5 | 29 | 16.6 % | 78.8 | 53.8 | 0 | – |
| 1920 | dark | 0 | 61.5 | 29 | 16.6 % | 78.8 | 53.8 | 0 | – |

## applicability — `#/anvendelighed`

| bredde | tema | overløb px | hovedindhold % | tekstspalte % | tekst ≤13 px | brødtekst tegn/linje | lovtekst tegn/linje | kontrastfejl | små trykflader (inline) |
|---|---|---|---|---|---|---|---|---|---|
| 390 | light | 0 | 100 | 92.8 | 59.5 % | 37.9 | – | 4 | 18 (0) |
| 390 | dark | 0 | 100 | 92.8 | 59.5 % | 37.9 | – | 2 | 18 (0) |
| 768 | light | 0 | 100 | 96.4 | 59.5 % | 94 | – | 4 | – |
| 768 | dark | 0 | 100 | 96.4 | 59.5 % | 94 | – | 2 | – |
| 1024 | light | 0 | 100 | 60.2 | 59.5 % | 71.5 | – | 4 | – |
| 1024 | dark | 0 | 100 | 60.2 | 59.5 % | 71.5 | – | 2 | – |
| 1440 | light | 0 | 81.9 | 46.1 | 18.2 % | 71.5 | – | 4 | – |
| 1440 | dark | 0 | 81.9 | 46.1 | 18.2 % | 71.5 | – | 2 | – |
| 1920 | light | 0 | 61.5 | 34.6 | 18.2 % | 71.5 | – | 4 | – |
| 1920 | dark | 0 | 61.5 | 34.6 | 18.2 % | 71.5 | – | 2 | – |

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
- 1920/light kontrast 3.43:1 (kræver 4.5): `div.rule-card-body > div.statutory-citations > span.citations-header` #7c8790 på #faf7f0, 12.5 px — “Ordret skoptekst fra Retsinformation:”
- 1920/light kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1920/light kontrast 3.57:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #7d8891 på #fffdf8, 11.5 px — “Profilkomplethed:”
- 1920/dark kontrast 3.35:1 (kræver 4.5): `div.rule-card-header > div.rule-card-title-row > span.verdict-badge.verdict-not-applies` #7f8692 på #f3f4f6, 12 px — “Vurdering: Gælder ikke”
- 1920/dark kontrast 4.22:1 (kræver 4.5): `div.rule-card-header > div.rule-card-meta > span.confidence-pill` #798898 på #162638, 11.5 px — “Profilkomplethed:”

</details>
