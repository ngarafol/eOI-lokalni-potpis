# Changelog

## 1.0.0 — prva javna verzija

- **Lokalno potpisivanje PDF-a** karticom preko PKCS#11 modula (eOI / AKD middleware).
  Dokument i PIN ne izlaze s računala; alat **ne podržava udaljeno (cloud) potpisivanje**.
- **Vektorski vidljivi pečat** (pravi PDF tekst s ugrađenim podskupom fonta): ime, `država · OIB`,
  `potpisano s eOI · <vrijeme>` u istom redu; duga imena se lome. Bez sistemskog fonta pečat se
  ne crta (potpis je tada nevidljiv).
- **RFC 3161 vremenski žig** (javni TSA po defaultu, AKD QTSA uz HTTP basic auth) i opcija
  **PAdES-LT**; nakon potpisa dodaje se **dokument-timestamp** (PAdES-LTA). `--offline` isključuje
  svaki mrežni poziv.
- **Validacija**: lanac pouzdanja, žig, dokument-timestamp, DSS/opoziv te **stari `adbe.pkcs7.sha1`
  potpisi** (SHA-1 profil, tipičan za bankovne dokumente) uz jasno upozorenje o slabostima.
- **GUI (Tk)**: pregled s mrežom potpisnih okvira (izbor prati promjenu broja redova), povlačenje
  mišem, popis potpisa, status i log, svijetla/tamna tema, HR/EN iz jedne tablice prijevoda.
- **CLI**: `--list`, `--check`, `--verify`, `--self-test`, `--lib`, `--ca-dir`, `--lang`, `--tsa`,
  `--box`, `--offline` i dr. (`--help` je zasad na hrvatskom).
- **Autodetekcija** PKCS#11 modula, AKD CA certifikata i fonta (macOS, Linux, Windows).
- Lokalni popravci pyHanko 0.37: `/BBox` i `/W` (širine CID glifova) — bez njih preglednici
  crtaju pečat razlomljeno (`P R I M J E R`).
- **38 testova** bez kartice + CI matrica (ubuntu / windows / macos × py3.11–3.13).
