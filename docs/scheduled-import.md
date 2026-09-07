# Daglig produktionsimport (P0-2)

Jobbet tager en verificeret databasebackup, importerer fra Retsinformation,
vektoriserer og kontrollerer dækningen. Det bruger `maintenance` med skriveadgang
via `ops`-profilen og begge Compose-filer. Det starter ingen af de fire offentlige
tjenester, åbner ingen porte og udfører ingen migrationer.

## Forberedelse efter PR-review

Installation og første kørsel skal godkendes af ejeren: første kørsel skriver i
produktionsdatabasen og aktivering af timeren tillader efterfølgende daglige job.
Der er ikke udført produktionskørsel som del af udviklingen af denne PR.

1. Kontrollér at den godkendte commit er hentet til `/home/tryk/Maritime-law-API`.
   Systemd-filen er lavet til brugeren `tryk` og denne projektsti; tilpas `User`,
   `WorkingDirectory`, `ExecStart` og `ExecStopPost` ved en anden installation.
2. Værten skal have Bash, Python 3 (kun standardbibliotek), curl, `flock`, `timeout`,
   `find` og Docker Compose med understøttelse af tunnel-overlayens `!reset`.
   Brugeren `tryk` skal kunne køre Docker uden en interaktiv sudo-prompt.
3. Opret en Healthchecks-kontrol: cron `15 4 * * *`, tidszone **UTC**, grace **20 timer**,
   og aktivér en notifikationsintegration. Grace skal rumme den første indhentning;
   efter målte køretider kan både grace og systemd-timeout sænkes.
   Skriv kontrollens HTTPS **ping-URL** i `.env` som `IMPORT_HEALTHCHECK_URL`.
   Brug basis-URL'en, uden `/start`, `/fail`, query-parametre eller fragment.
   En Healthchecks-kompatibel egen installation kan også bruges; en ntfy-topic-URL
   bruger en anden protokol og kan ikke indsættes her.
4. `.env` skal have mode 600. De valgfrie `IMPORT_STATE_DIR` og `IMPORT_BACKUP_DIR`
   skal være absolutte stier. Tomme værdier bruger henholdsvis
   `/home/tryk/.local/state/maritim-import` og `/home/tryk/maritim-backups`.
   Citér værdier med mellemrum; disse tre felter understøtter ikke `$VAR`/`${VAR}`
   eller `~`-ekspansion. Resten af `.env` læses af Docker Compose som hidtil.

Byg vedligeholdelsesimaget fra den godkendte commit. Dette genstarter ikke backenden:

```bash
cd /home/tryk/Maritime-law-API
chmod 600 .env
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml --profile ops build maintenance
```

Vedligeholdelsescontaineren bruger de samme modelvalg som tunnel-overlayen
(`local`, `intfloat/multilingual-e5-small`, 384 dimensioner). Kildens URL, timeout,
retries, rate limit og importgrænser læses nu også fra Compose-konfigurationen.
Kildens rate limit må ikke sænkes for at gøre det første job hurtigere.

## Installation og første kørsel

Kør først følgende efter godkendelse. Hold timeren slukket indtil første kørsel er kontrolleret:

```bash
sudo install -m 644 deploy/maritim-import.service deploy/maritim-import.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start --no-block maritim-import.service
sudo systemctl status maritim-import.service
tail -f /home/tryk/.local/state/maritim-import/import-$(date -u +%F).log
```

`--no-block` giver terminalen tilbage med det samme; kommandoens exit-kode er **ikke**
bevis for en vellykket import. Når jobbet er færdigt, kontrollér:

```bash
sudo systemctl show maritim-import.service -p ActiveState -p Result -p ExecMainStatus
```

Accept: `Result=success`, `ExecMainStatus=0`, log med `COMPLETED`, nul fejl fra
`embed run`, nul manglende dokumenter i `embed status`, og
`event=scheduled_import_completed`. Kontrollér også at den nye backup kan listes
med `pg_restore --list` (det gør scriptet automatisk), og at Healthchecks viser
både start og succes. En læsbar indholdsfortegnelse er en basal backupkontrol;
en fuld prøvegendannelse og off-box backup hører til P0-4.

Aktivér derefter timeren:

```bash
sudo systemctl enable --now maritim-import.timer
systemctl list-timers maritim-import.timer
```

Timeren kører kl. **04:15 UTC** og indhenter en oversprunget timeraktivering med
`Persistent=true`. Aktivering kan derfor starte jobbet straks. En indhentning,
der rammer kildens lukkede vindue, fejler synligt og forsøges næste planlagte dag.
Der er ingen automatisk hurtig genstartssløjfe mod den rate-begrænsede kilde.

## Fejl, overlap og genoptagelse

- `import --require-complete` returnerer fejl ved alle statusser bortset fra
  `COMPLETED`, inklusive `COMPLETED_WITH_ERRORS`. Eksisterende interaktiv CLI-adfærd
  uden flaget er bevaret. Kontrollen gælder denne kommandos egen importkørsel;
  den læser ikke bare den nyeste række, som kunne tilhøre et andet job.
- `embed run` skal lykkes. `embed status --require-complete` kræver derefter nul
  manglende/forældede dokumenter, ingen chunks fra en anden model og en passende
  pgvector-kolonnedimension. Modellen erstattes aldrig af testdata eller en anden udbyder.
- En lås i tilstandsmappen afviser en ekstra scriptkørsel med exit 75. Systemd
  starter heller ikke samme oneshot-service to gange samtidigt. Brug altid dette
  job til planlagt import; låsen omfatter **ikke** vilkårlige manuelle CLI-kommandoer,
  backfill eller den lokale, umerged `content reimport`-branch.
- Ved SIGTERM ryddes kun jobbets egne containere op, og jobbet fejler. Systemd
  kalder desuden `ExecStopPost` ved timeout/SIGKILL; det fjerner containere med samme
  `INVOCATION_ID`-etiket og sender fejlvarsling. En ukendt container fjernes aldrig.
- Ved strømsvigt eller en utilgængelig Docker-daemon kan oprydning ikke garanteres.
  Det faste containernavn afviser da en ny jobcontainer. Inspicér en eventuel
  `maritim-scheduled-import`, og stop/fjern den først, når det er bekræftet, at den
  tilhører den afbrudte kørsel. Start derefter servicen igen.
- Jobbet sender `/start`, `/fail` ved fejl og basis-URL'en ved succes. URL'en sendes
  via curls stdin og kommer ikke i log eller procesargumenter. Ingen logtekst eller
  lovdata sendes til Healthchecks. Fejlede netværkskald logges uden URL.
  Et fejlet slutping giver en fejlet service, også hvis dataarbejdet lykkedes.
  En konfigurationsfejl før URL'en kan læses eller en slukket vært opdages gennem
  den eksterne kontrols udeblevne ping; opsæt derfor selve notifikationen først.

Healthchecks beskriver [fejlsignalerne](https://healthchecks.io/docs/signaling_failures/)
og [startsignaler/grace](https://healthchecks.io/docs/measuring_script_run_time/).

En afbrydelsestest skriver muligvis en del af importen før stoppet og skal derfor
foretages i et aftalt driftsvindue. Scriptets backup sker før alle disse writes:

```bash
sudo systemctl start --no-block maritim-import.service
# Når loggen viser, at importtrinnet kører:
sudo systemctl kill --kill-whom=main --signal=TERM maritim-import.service
sudo systemctl show maritim-import.service -p Result -p ExecMainStatus
```

Forvent fejlet service, fejlnotifikation og ingen container med denne kørsels etiket.
Gentag normalt `systemctl start` for at genoptage. Det skal slutte med succes og
rydde alarmen. En hard-kill-test kan bruge `--signal=KILL`; den afprøver specifikt
`ExecStopPost` og skal også aftales før kørsel i produktion.

## Feedvindue og lagring

Første kørsel henter det tilgængelige vindue med `--since` ti kalenderdage tilbage.
Efter fuldført import og indekskontrol gemmes kørselsstartens UTC-dato atomisk i
`last-success-date`. Næste kørsel starter én dag før denne dato og begrænses til
kildens ti-dages vindue. Afbrudte eller delvist fejlede datakørsler flytter ikke datoen.
Et fejlet slutping kan forekomme efter datoen er gemt; dataarbejdet er da afsluttet.

Første kørsel og pauser længere end feedvinduet logges med
`event=backfill_review_required`. Det er ikke bevis på historisk fuldstændighed,
selv om dagens import bliver `COMPLETED`. Ændringer uden for vinduet kræver
stadig en eksplicit accessionsliste i P0-3.

Backups hedder `pre-scheduled-import-<UTC>-<tilfældig>.dump`; en `.partial` bliver
først omdøbt efter `pg_restore --list`. Nye filer er private (umask 077).
Jobbet beholder egne færdige backups og dagslogs i 14 dage. Rotation sker først
efter en ny verificeret backup og rører ikke andre dumpfiler. Gamle `.partial`
fra et hårdt stop kan undersøges og fjernes manuelt. Dette er lokale backups;
en kopi uden for værten og en selvstændig backuptimer er stadig P0-4.

Stop automatisk import med `sudo systemctl disable --now maritim-import.timer`.
Det stopper ikke en aktiv import; brug om nødvendigt `sudo systemctl stop
maritim-import.service`. Deaktivering ændrer ingen data eller offentlige tjenester.
