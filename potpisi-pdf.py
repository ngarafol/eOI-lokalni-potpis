#!/usr/bin/env python3
"""Lokalno potpisivanje PDF-a karticom preko AKD PKCS#11 modula. Nista se ne salje u cloud.

Priprema (vec napravljeno): .venv/bin/pip install "pyhanko[pkcs11]"

Primjeri:
  .venv/bin/python potpisi-pdf.py --list                    # popis tokena/certifikata (bez PIN-a)
  .venv/bin/python potpisi-pdf.py --check                   # proba PIN-a i dohvata kljuca, ne potpisuje
  .venv/bin/python potpisi-pdf.py ugovor.pdf                # potpise lokalno -> ugovor-signed.pdf
  .venv/bin/python potpisi-pdf.py --visible ugovor.pdf
  .venv/bin/python potpisi-pdf.py --verify ugovor-signed.pdf
  .venv/bin/python potpisi-pdf.py --token Identification ugovor.pdf

GUI: .venv/bin/python potpisi-gui.py   (ili dvoklik na potpisi-gui.command)

Vidljivi potpis se crta kao slika (Pillow + Arial Unicode) pa hrvatski znakovi rade,
a izgled je isti u svim preglednicima.
"""
from __future__ import annotations

import argparse
import getpass
import glob
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
import os
import re
import sys

def _first_existing(paths) -> str | None:
    return next((p for p in paths if p and os.path.exists(p)), None)


# ponytail: staze za Linux/Windows su izvucene iz sluzbenih paketa, ali kartica na tim
# OS-ima nije potvrdjena na stvarnom stroju (vidi README, "Poznata ogranicenja")
def _default_lib() -> str:
    """Putanja PKCS#11 modula: env POTPIS_LIB, pa uobicajene lokacije po OS-u."""
    env = os.environ.get("POTPIS_LIB")
    if env:
        return env
    mac = ["/Applications/CertiliaMiddleware.app/Contents/pkcs11/libCertiliaPkcs11.dylib"]
    linux = ["/opt/certiliamiddleware/pkcs11/libCertiliaPkcs11.so",       # .deb (provjereno)
             "/usr/lib/akd/certiliamiddleware/pkcs11/libCertiliaPkcs11.so",
             "/usr/lib/libCertiliaPkcs11.so", "/usr/local/lib/libCertiliaPkcs11.so"]
    win = [  # .exe instalater: 64-bit pa 32-bit, dva imena modula
        r"C:\Program Files\AKD\Certilia Middleware\pkcs11\CertiliaPkcs11_64.dll",
        r"C:\Program Files\AKD\Certilia Middleware\pkcs11\AkdEidPkcs11_64.dll",
        r"C:\Program Files\AKD\Certilia Middleware\pkcs11\CertiliaPkcs11.dll",
        r"C:\Program Files\AKD\Certilia Middleware\pkcs11\AkdEidPkcs11.dll",
        r"C:\Program Files (x86)\AKD\Certilia Middleware\pkcs11\CertiliaPkcs11.dll",
        r"C:\Program Files (x86)\AKD\Certilia Middleware\pkcs11\AkdEidPkcs11.dll",
    ]
    if sys.platform == "darwin":
        cands = mac + linux
    elif sys.platform.startswith("linux"):
        cands = linux + mac
    else:
        cands = win + mac
    return _first_existing(cands) or cands[0]


def _default_ca_dir() -> str | None:
    """Mapa s AKD korijenskim certifikatima (PEM), ako postoji."""
    env = os.environ.get("POTPIS_CA_DIR")
    if env:
        return env
    cands = [
        "/Applications/CertiliaMiddleware.app/Contents/certificates",  # macOS
        "/opt/certiliamiddleware/certificates",                         # Linux (.deb, provjereno)
        "/usr/lib/akd/certiliamiddleware/certificates",
        "/usr/share/certilia-middleware/certificates",
        r"C:\Program Files\AKD\Certilia Middleware\certificates",        # Windows (.exe)
        r"C:\Program Files (x86)\AKD\Certilia Middleware\certificates",
    ]
    return next((p for p in cands if os.path.isdir(p)), None)


LIB = _default_lib()
CA_DIR = _default_ca_dir() or ""

LANG = "hr"  # GUI ga prebacuje ("hr"/"en")

TR_EN: dict[str, str] = {
    'Greška': 'Error',
    'OK': 'OK',
    'greška (vidi log)': 'failed (see log)',
    'Lokalno potpisivanje PDF-a': 'Local PDF signing',
    'radi preko AKD middlewarea · PKCS#11 · ništa ne ide u cloud': 'uses AKD middleware · PKCS#11 · nothing leaves your computer',
    'Jezik:': 'Language:',
    ' Dokument ': ' Document ',
    ' Potpis ': ' Signature ',
    'PDF:': 'PDF:',
    'Odaberi…': 'Choose…',
    'Certifikat:': 'Certificate:',
    'vidljiv potpis': 'visible signature',
    'stranica:': 'page:',
    '{n} stranica · {name}': '{n} page(s) · {name}',
    'Odaberi PDF.': 'Pick a PDF.',
    'PDF učitan (bez preview-a)': 'PDF loaded (no preview)',
    'okvir (pt): {box}  ·  stranica {page}/{total}': 'box (pt): {box}  ·  page {page}/{total}',
    'klikni okvir na stranici da odabereš mjesto potpisa': 'click a box on the page to choose where the signature goes',
    'potpisi: greška pri čitanju ({err})': 'signatures: error while reading ({err})',
    'Odaberi PDF': 'Pick a PDF',
    'Greška': 'Error',
    'Ne mogu otvoriti PDF:\n{x}': 'Cannot open the PDF:\n{x}',
    'Odaberi postojeći PDF.': 'Pick an existing PDF.',
    'Upiši PIN kartice.': 'Enter the card PIN.',
    'Odaberi okvir za potpis na stranici (ili isključi vidljivi potpis).': 'Pick a box on the page (or turn off the visible stamp).',
    'preview nije dostupan (Pillow bez Tk podrške)': 'preview unavailable (Pillow without Tk support)',
    'Odaberi PDF koji želiš provjeriti.': 'Pick a PDF to verify.',
    'redova mreže:': 'grid rows:',
    'PIN kartice:': 'Card PIN:',
    'Potpiši PDF': 'Sign PDF',
    'Provjeri potpis': 'Verify signature',
    'Popis potpisa': 'List signatures',
    'otvori nakon potpisa': 'open after signing',
    'vremenski žig (javni TSA)': 'timestamp (public TSA)',
    'PAdES-LT (ugradi OCSP/CRL dokaze)': 'PAdES-LT (embed OCSP/CRL evidence)',
    'Odaberi PDF pa klikni „Potpiši PDF”.': 'Pick a PDF, then click “Sign PDF”.',
    'Učitavam PDF…': 'Loading PDF…',
    'Radi…': 'Working…',
    'potpis': 'signing',
    'provjera': 'verification',
    'POTPIS': 'SIGNATURE',
    'potpisujem: {pdf}\n  -> {out}\n  okvir: {box} (stranica {page})': 'signing: {pdf}\n  -> {out}\n  box: {box} (page {page})',
    'PDF koji potpisuješ.\nZa jedan čist potpis odaberi original, ne već potpisani PDF.': 'PDF you want to sign.\nFor a single clean signature pick the original, not an already signed PDF.',
    'Odaberi PDF datoteku s diska.': 'Pick a PDF file from disk.',
    'Broj stranica i naziv učitanog dokumenta.': 'Page count and file name of the loaded document.',
    'Nudi se samo certifikat koji smije potpisivati dokumente (non-repudiation).\nIdentifikacijski certifikat (client_auth / e-Građani) se ne prikazuje jer ne služi za potpis.': 'Only certificates allowed to sign documents (non-repudiation) are offered.\nThe identification certificate (client_auth / web login) is hidden because it cannot sign.',
    'Jedini certifikat na kartici koji može potpisivati dokumente.': 'The only certificate on the card that can sign documents.',
    'Kartica nije pronadena ili nema potpisnog certifikata. Ubaci karticu pa klikni ↻.': 'No card found, or it has no signing certificate. Insert the card and click ↻.',
    'Ponovno procitaj certifikate s kartice.': 'Re-read certificates from the card.',
    'nema certifikata za potpisivanje (ubaci karticu, pa ↻)': 'no signing certificate (insert the card, then ↻)',
    'citam certifikate s kartice…': 'reading certificates from the card…',
    'kartica nema potpisni certifikat (samo identifikacijski)': 'the card has no signing certificate (identification only)',
    'certifikati: {n} potpisni': 'certificates: {n} signing',
    'Tamno': 'Dark',
    'Svijetlo': 'Light',
    'Prebaci svijetlu/tamnu temu (sve boje su podesene za citljivost).': 'Switch light/dark theme (all colours are tuned for readability).',
    'Iscrtava vidljivi pečat potpisa na stranicu.\nAko je isključeno, potpis je nevidljiv (ali jednako valjan).': 'Draws a visible signature stamp on the page.\nIf off, the signature is invisible (but just as valid).',
    'Stranica na koju ide vidljivi potpis.\n◀ ▶ listaju stranice, upisani broj se odmah prikaže.': 'Page where the visible signature goes.\n◀ ▶ page through; a typed number is shown immediately.',
    'Prethodna stranica.': 'Previous page.',
    'Sljedeća stranica.': 'Next page.',
    'Koliko redova okvira se iscrta preko stranice (2 stupca × N redova).\nKlikni okvir da odabereš mjesto potpisa, povuci mišem da ga premjestiš.': 'How many rows of boxes are drawn over the page (2 columns × N rows).\nClick a box to pick the signing spot, drag it with the mouse to move it.',
    'PIN kartice (ostaje samo na ovom računalu).\nPazi: kartica dopušta ograničen broj pokušaja (nakon 3 promašaja treba PUK).': 'Card PIN (stays on this computer only).\nCareful: the card allows limited attempts (after 3 failures you need the PUK).',
    'Potpisuje odabrani PDF karticom i sprema <ime>-signed.pdf.\nSve ostaje lokalno - dokument i PIN se nigdje ne šalju.': 'Signs the selected PDF with the card and saves <name>-signed.pdf.\nEverything stays local - document and PIN are never sent anywhere.',
    'Temeljita provjera postojećih potpisa: lanac, vremenski žig i opoziv.\nMože ići na mrežu (dohvat CRL/OCSP).': 'Thorough check of existing signatures: chain, timestamp, revocation.\nMay use the network (CRL/OCSP fetching).',
    'Brzi lokalni popis potpisa u PDF-u: polje, potpisnik, vrijeme, valjan/pouzdan,\ni u koje polje ide sljedeći potpis.': 'Quick local list of signatures: field, signer, time, valid/trusted,\nand which field the next signature will use.',
    'Nakon potpisa automatski otvori potpisani PDF.': 'Open the signed PDF automatically after signing.',
    'RFC3161 vremenski žig od javnog TSA (DigiCert): neovisni dokaz KADA je potpis nastao.\nTraži internet; ako ne prođe, potpis ipak nastaje (bez žiga).': 'RFC3161 timestamp from a public TSA (DigiCert): independent proof of WHEN the signature was made.\nNeeds internet; if it fails, the signature is still created (without a timestamp).',
    'Ugrađuje dokaze o opozivu (OCSP/CRL) u PDF za dugoročnu provjerljivost.\nAKD certifikati ne objavljuju CRL/OCSP adrese, pa se obično preskoči.': 'Embeds revocation evidence (OCSP/CRL) into the PDF for long-term validation.\nAKD certificates publish no CRL/OCSP pointers, so it is usually skipped.',
    'Klikni okvir da odabereš mjesto potpisa, povuci mišem da ga premjestiš.\nCrveni okvir = gdje će biti vidljivi potpis.': 'Click a box to pick the signing spot, drag it with the mouse to move it.\nRed frame = where the visible signature will be.',
    'Tijek rada: potpis, provjera, upozorenja i greške.': 'Activity log: signing, verification, warnings, errors.',
    'neslužbeni alat · nije povezan s AKD d.o.o.': 'unofficial tool · not affiliated with AKD d.o.o.',
    "\nOK: PIN prihvaćen, certifikat i privatni ključ dohvaćeni. Ništa nije potpisano.":
        "\nOK: PIN accepted, certificate and private key loaded. Nothing was signed.",
    "stari profil": "legacy profile", "cijeli dokument": "entire document",
    "stari SHA-1 profil (bez ziga)": "legacy SHA-1 profile (no timestamp)",
    "dokument ima izmjena nakon potpisa": "the document was changed after signing",
    "ocekujem datoteku, ne mapu": "expected a file, not a directory",
    "serijski:": "serial:", "stanje:": "state:", "potpisano s eOI": "signed with eOI",
    "potpisano lokalno": "signed locally",
    "nema fonta za pecаt - potpis je nevidljiv":
        "no font for the stamp - the signature will be invisible",
    "OIB:": "Tax ID (OIB):",
    "dio dokumenta": "part of the document",
    "vrijeme potpisa nije kriptografski zasticeno":
        "the signing time is not cryptographically protected",
    "potpis nije citljiv": "signature cannot be read",
    "dokument-timestamp": "document timestamp", "TSA": "TSA", "nema datoteke": "file not found",
    "offline: bez vremenskog ziga, LT/LTA i dohvata s mreze":
        "offline: no timestamp, no LT/LTA and no network fetching",
    "preskacem dokument-timestamp": "skipping document timestamp",
    "potpis postoji, ali ga ne mogu procitati":
        "a signature exists, but it cannot be read",
    "certifikat nema CRL/OCSP adrese - PAdES-LT preskocen (nema sto ugraditi)":
        "certificate has no CRL/OCSP pointers - PAdES-LT skipped (nothing to embed)",
    "   ! ne mogu otvoriti sesiju:": "   ! cannot open session:",
    "  -> potpisano varijantom: ": "  -> signed with variant: ",
    "dokument-timestamp dodan": "document timestamp added",
    "dokument-timestamp nije prosao": "document timestamp failed",
    " + vremenski žig": " + timestamp",
    "--lta traži vremenski žig (--tsa)": "--lta requires a timestamp (--tsa)",
    "--box treba biti u obliku \"x1,y1,x2,y2\"": '--box must look like "x1,y1,x2,y2"',
    "(potpis je bez vremenskog žiga)": "(signature has no timestamp)",
    "(potpis je nevidljiv na stranici; za vidljivi okvir dodaj --visible)":
        "(signature is invisible on the page; add --visible for a visible box)",
    "AKD certifikati": "AKD certificates",
    "algoritam:": "algorithm:", "bez ziga": "without timestamp", "da": "yes",
    "dostupni:": "available:",
    "DSS nije ugrađen (nema dostupnih OCSP/CRL podataka)":
        "DSS not embedded (no OCSP/CRL data available)",
    "DSS s dokazima o opozivu ugrađen": "DSS with revocation evidence embedded",
    "font za pecate": "stamp font", "GREŠKA": "ERROR",
    "žig da": "timestamp yes", "žig ne": "timestamp no",
    "izdao:": "issuer:", "je u ovom PDF-u već potpisano": "is already signed in this PDF",
    "Mjesto:": "Location:", "ne": "no", "ne postoji": "does not exist",
    "ne mogu naci AKD certifikate (CA_DIR)": "cannot find AKD certificates (CA_DIR)",
    "ne mogu naci korijenski certifikat": "cannot find the trust root certificate",
    "NE RADI": "DOES NOT WORK", "nedostupno:": "unavailable:",
    "nema potpisa u": "no signatures in", "nema tokena koji sadrži": "no token matching",
    "nepouzdan": "untrusted", "NEVALJAN": "INVALID", "nije prošao:": "failed:",
    "novi potpis ide u polje": "the new signature goes to field",
    "osnovni potpis": "basic signature",
    "PDF već sadrži potpisna polja": "PDF already contains signature fields",
    "PKCS#11 modul": "PKCS#11 module", "platforma": "platform", "pokriva:": "covers:",
    "polje": "field", "postojeća polja:": "existing fields:",
    "potpisa u PDF-u:": "signatures in PDF:", "potpisano ->": "signed ->",
    "Potpisi originalni PDF ili pusti alat da sam odabere slobodno polje.":
        "Sign the original PDF, or let the tool pick a free field.",
    "potpisivanje": "signing", "potpisnik:": "signer:", "pouzdan": "trusted",
    "preview (pypdfium2)": "preview (pypdfium2)", "privremeni PDF": "temporary PDF",
    "problem:": "problem:", "radi": "WORKS", "Razlog:": "Reason:", "rezultat": "result",
    "sa zigom": "with timestamp", "self-test (bez kartice)": "self-test (no card needed)",
    "sistemski korijeni (TSA)": "system roots (TSA)",
    "sljedeći potpis ide u polje:": "next signature goes to field:",
    "softverski certifikat": "software certificate", "UPOZORENJE": "WARNING",
    "validacija": "validation", "valjan": "valid", "vremenski zig": "timestamp",
    "vrijedi do:": "valid until:", "vrijeme:": "time:",
    "nema sistemskog fonta - pecate crta ugradjeni bitmap font":
        "no system font found - the stamp uses the built-in bitmap font",
    "PKCS#11 modul nije nadjen": "PKCS#11 module not found",
    "instaliraj AKD middleware ili zadaj --lib / POTPIS_LIB":
        "install the AKD middleware, or pass --lib / POTPIS_LIB",
    "za vidljivi potpis treba Pillow: .venv/bin/pip install pillow":
        "a visible signature needs Pillow: .venv/bin/pip install pillow",
}


def T(text: str) -> str:
    """Prijevod poruka prema LANG (hr = original)."""
    return text if LANG == "hr" else TR_EN.get(text, text)


VERSION = "1.0.0"
# Ako je ukljuceno, alat ne smije napraviti nijedan mrezni poziv (ni TSA, ni dohvat OCSP/AIA).
OFFLINE = os.environ.get("POTPIS_OFFLINE") == "1"
PUBLIC_TSA = "http://timestamp.digicert.com"
FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
)


def _default_font() -> str | None:
    return next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)


def _patch_pyhanko_font_widths() -> None:
    """pyHanko 0.37 pise /W (sirine CID glifova) u FONT UNITS, a PDF trazi tisućinke em.

    Za ArialUnicodeMS (unitsPerEm=2048) to znaci da preglednici svako slovo pomaknu ~2x
    preveliko (P R I M J E R). Popravak: sirine se skaliraju s 1000/unitsPerEm.
    Ako neki buduci pyHanko to sam normalizira, ovaj patch treba ukloniti (test to hvata).
    """
    import pyhanko.pdf_utils.font.opentype as _ot

    orig = _ot._build_type0_font_from_cidfont

    def fixed(writer, cidfont_obj, widths_by_cid_iter, vertical, obj_stream=None):
        scale = 1000.0 / cidfont_obj._tt["head"].unitsPerEm
        scaled = ((cid, round(width * scale)) for cid, width in widths_by_cid_iter)
        return orig(writer=writer, cidfont_obj=cidfont_obj, widths_by_cid_iter=scaled,
                    vertical=vertical, obj_stream=obj_stream)

    _ot._build_type0_font_from_cidfont = fixed


def _patch_pyhanko_bbox() -> None:
    """pyHanko 0.37 pise /BBox kao (0, visina, sirina, 0) - PDF spec trazi (0, 0, sirina, visina).

    Strogi preglednici (Preview, pdf.js) zbog toga odrezu/ne prikazu potpis.
    """
    from pyhanko.pdf_utils import generic, writer as _writer

    orig = _writer.init_xobject_dictionary

    def _bbox(obj):
        return [float(v) for v in obj[generic.pdf_name("/BBox")]]

    probe = orig(b"", 10.0, 20.0)  # feature-detect: pise li pyHanko normalizirani BBox?
    if _bbox(probe) == [0.0, 0.0, 10.0, 20.0]:
        return  # noviji pyHanko je to popravio, ne diramo

    def fixed(command_stream, box_width, box_height, resources=None):
        obj = orig(command_stream, box_width, box_height, resources)
        obj[generic.pdf_name("/BBox")] = generic.ArrayObject(
            [generic.FloatObject(float(v)) for v in (0, 0, box_width, box_height)]
        )
        return obj

    _writer.init_xobject_dictionary = fixed


_patch_pyhanko_bbox()
_patch_pyhanko_font_widths()


def _fit_stamp_lines(rows, measure, max_w: float, max_h: float, base_px: int, floor_px: int):
    """Slozi redove pecata u zadani prostor: lomi duga imena i smanjuje font do granice.

    rows = [(tekst, relativna velicina)], measure(tekst, px) -> sirina u pikselima.
    Vraca ([(tekst, px)], base_px).
    """

    def wrap(text: str, px: int) -> list[str]:
        out, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cur and measure(trial, px) > max_w:
                out.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            out.append(cur)
        return out or [""]

    base = base_px
    while True:
        laid: list[tuple[str, int]] = []
        for text, scale in rows:
            px = max(int(base * scale), 8)
            laid.extend((seg, px) for seg in wrap(str(text), px))
        if sum(px * 1.35 for _t, px in laid) <= max_h or base <= floor_px:
            return laid, base
        base -= 1


def _stamp_lines(signer) -> list[tuple[str, float]]:
    """Redovi pecata: (tekst, relativna velicina). CN je najveci, ostalo manje."""
    import re
    from datetime import datetime

    import tzlocal

    cert = signer.signing_cert
    subj = cert.subject.native
    name = subj.get("common_name") or cert.subject.human_friendly
    lines: list[tuple[str, float]] = [(str(name), 1.0)]

    # drugi red: drzava i OIB (iz serijskog broja certifikata, npr. PNOHR-00000000000)
    country = subj.get("country_name")
    serial = str(subj.get("serial_number") or "")
    oib = re.search(r"(\d{11})", serial)
    id_bits = [b for b in (country, (f"{T('OIB:')} {oib.group(1)}" if oib else serial)) if b]
    if id_bits:
        lines.append((" · ".join(id_bits), 0.78))

    device = "eOI" if (cert.issuer.native.get("common_name") or "").upper() in ("HRIDCA", "KIDCA") else None
    when = datetime.now(tzlocal.get_localzone()).strftime("%Y-%m-%d %H:%M %Z")
    label = T("potpisano s eOI") if device else T("potpisano lokalno")
    lines.append((f"{label} · {when}", 0.72))
    return lines


def _visible_stamp_style(writer, box, lines, font_path=None, max_font_size=10):
    """Vidljivi potpis kao PRAVI PDF tekst (vektor): ugradjen podskup fonta.

    Ostar na svakom zoomu i mali (~2 KB). Redovi se grupiraju po velicini (ime vece,
    detalji manje) i slazu odozdo prema gore; duga imena se lome. Ako nema upotrebljivog
    fonta, vraca None (potpis je tada nevidljiv) - potpisivanje nikad ne pada zbog pecata.
    """
    from PIL import Image, ImageDraw, ImageFont

    from pyhanko.pdf_utils import layout
    from pyhanko.pdf_utils.content import PdfResources, RawContent
    from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
    from pyhanko.pdf_utils.text import TextBox, TextBoxStyle
    from pyhanko.stamp import StaticStampStyle

    font_path = font_path or _default_font()
    if not font_path or not os.path.exists(font_path):
        print("  " + T("nema fonta za pecаt - potpis je nevidljiv"))
        return None

    w, h = box[2] - box[0], box[3] - box[1]
    pad = max(h * 0.06, 3.0)
    measurer = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def measure(text: str, px: int) -> float:
        return measurer.textlength(text, font=ImageFont.truetype(font_path, px))

    laid, _base = _fit_stamp_lines(lines, measure, w - 2 * pad, h - 2 * pad,
                                   base_px=int(max_font_size), floor_px=5)

    groups: dict[int, list[str]] = {}
    for text, px in laid:
        groups.setdefault(px, []).append(text)

    resources = PdfResources()
    parts: list[bytes] = []
    total = sum(len(v) * k * 1.35 for k, v in groups.items())
    y = pad + max((h - 2 * pad - total) / 2, 0)
    for gi, px in enumerate(sorted(groups)):  # manji redovi (detalji) dolje, ime gore
        lines_ = groups[px]
        gh = len(lines_) * px * 1.35
        tb = TextBox(
            TextBoxStyle(
                font=GlyphAccumulatorFactory(font_file=font_path, font_size=px),
                font_size=px,
                leading=int(round(px * 1.35)),
                box_layout_rule=layout.SimpleBoxLayoutRule(
                    x_align=layout.AxisAlignment.ALIGN_MIN,
                    y_align=layout.AxisAlignment.ALIGN_MIN,
                    margins=layout.Margins(0, 0, 0, 0),
                ),
            ),
            writer=writer,
            resources=resources,
            box=layout.BoxConstraints(width=w - 2 * pad, height=gh),
            font_name=f"F{gi + 1}",  # svaki blok svoj font resurs (inace se /W pregazi)
        )
        tb.content = "\n".join(lines_)
        parts.append(b"q 1 0 0 1 %g %g cm\n" % (pad, y) + tb.render() + b"\nQ")
        y += gh

    content = RawContent(data=b"\n".join(parts), resources=resources,
                         box=layout.BoxConstraints(width=w, height=h))
    return StaticStampStyle(
        background=content,
        background_opacity=1.0,
        border_width=1,
        border_color=(0, 0, 0),
        background_layout=layout.SimpleBoxLayoutRule(
            x_align=layout.AxisAlignment.ALIGN_MID,
            y_align=layout.AxisAlignment.ALIGN_MID,
            margins=layout.Margins(0, 0, 0, 0),
            inner_content_scaling=layout.InnerScaling.STRETCH_FILL,
        ),
    )


def _pin(args) -> str:
    return os.environ.get("POTPIS_PIN") or getpass.getpass("PIN kartice: ")


def _session(match: str, user_pin):
    """Otvori PKCS#11 sesiju na tokenu ciji naziv sadrzi `match`."""
    import pkcs11

    _ensure_lib()

    labels = []
    for slot in pkcs11.lib(LIB).get_slots(token_present=True):
        try:
            token = slot.get_token()
        except Exception:  # noqa: BLE001
            continue
        labels.append(token.label)
        if match.lower() in token.label.lower():
            return token.open(user_pin=user_pin, rw=False)
    raise SystemExit(T("nema tokena koji sadrži") + f" {match!r}; " + T("dostupni:") + f" {labels}")


def list_certs() -> list[dict]:
    """Certifikati na kartici: token, CN, key usage, extended key usage.

    Vraca samo one koji mogu POTPISIVATI dokumente (non-repudiation/content_commitment);
    certifikati za identifikaciju (client_auth) se preskacu.
    """
    import pkcs11
    from asn1crypto import x509
    from pkcs11 import Attribute, ObjectClass

    _ensure_lib()
    out: list[dict] = []
    for slot in pkcs11.lib(LIB).get_slots(token_present=True):
        try:
            token = slot.get_token()
            session = token.open(user_pin=None, rw=False)
            certs = list(session.get_objects({Attribute.CLASS: ObjectClass.CERTIFICATE}))
        except Exception:  # noqa: BLE001
            continue
        for obj in certs:
            try:
                cert = x509.Certificate.load(obj[Attribute.VALUE])
            except Exception:  # noqa: BLE001
                continue
            ku = set(cert.key_usage_value.native) if cert.key_usage_value else set()
            eku = set(cert.extended_key_usage_value.native) if cert.extended_key_usage_value else set()
            out.append(dict(
                token=token.label,
                cn=cert.subject.native.get("common_name") or cert.subject.human_friendly,
                ku=sorted(ku),
                eku=sorted(eku),
                usable=bool({"non_repudiation", "content_commitment"} & ku),
            ))
    return out


def _hexorNone(value: str | None) -> bytes | None:
    return bytes.fromhex(value) if value else None


def _tsa_url(args) -> str | None:
    """URL vremenskog ziga: "digicert"/"javni" -> javni TSA, "none" -> bez ziga."""
    value = (args.tsa or "").lower()
    if value in ("", "none", "ne", "off", "bez"):
        return None
    if value in ("digicert", "public", "javni"):
        return PUBLIC_TSA
    return args.tsa


def _timestamper(args):
    """RFC3161 timestamp (default: javni DigiCert TSA); podrzava HTTP basic auth (AKD QTSA)."""
    url = _tsa_url(args)
    if url is None:
        return None
    from pyhanko.sign.timestamps import HTTPTimeStamper

    auth = None
    raw = args.tsa_auth or os.environ.get("TSA_AUTH")
    if raw:
        user, _, pwd = raw.partition(":")
        auth = (user, pwd)
    return HTTPTimeStamper(url, https=url.startswith("https"), auth=auth)


def soft_signer(cn: str = "SOFT TEST", root_pem: str | None = None):
    """Samopotpisani certifikat + kljuc (bez kartice): za --self-test i testove.

    Ako je zadan `root_pem`, tamo se spremi i PEM certifikata (za --trust u testovima).
    """
    from asn1crypto import keys as a_keys, x509 as a_x509
    from cryptography import x509 as cx
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.sign import signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    import tzlocal

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = cx.Name([cx.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(tzlocal.get_localzone())
    cert = (
        cx.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(cx.KeyUsage(digital_signature=True, content_commitment=True,
                                   key_encipherment=False, data_encipherment=False,
                                   key_agreement=False, key_cert_sign=False, crl_sign=False,
                                   encipher_only=False, decipher_only=False), True)
        .sign(key, hashes.SHA256())
    )
    if root_pem:
        with open(root_pem, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    a_cert = a_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    registry = SimpleCertificateStore()
    registry.register(a_cert)
    return signers.SimpleSigner(
        signing_cert=a_cert,
        signing_key=a_keys.PrivateKeyInfo.load(
            key.private_bytes(serialization.Encoding.DER,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())),
        cert_registry=registry,
    )


def self_test(args) -> int:
    """Provjera platforme bez kartice: potpise PDF softverskim certifikatom i validira ga."""
    import tempfile
    from types import SimpleNamespace

    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    state = {"ok": True}

    def line(label: str, value: str, level: str = "ok"):
        """level: ok (obavezno), warn (radi, ali fali nesto), info (samo za karticu)."""
        if level == "fail":
            state["ok"] = False
        mark = {"ok": "OK ", "warn": "WARN", "info": "-- ", "fail": "FAIL"}[level]
        print(f"  [{mark}] {label:30s} {value}")

    print(T("self-test (bez kartice)"))
    line(T("platforma"), f"{sys.platform} · python {sys.version.split()[0]}")
    # modul i AKD certifikati trebaju samo za KARTICU, ne za ovaj test
    line(T("PKCS#11 modul"), LIB, "ok" if os.path.exists(LIB) else "info")
    line(T("AKD certifikati"), CA_DIR or "-", "ok" if CA_DIR else "info")
    font = _default_font()
    line(T("font za pecate"), str(font), "ok" if font else "warn")
    if not font:
        print("        " + T("nema sistemskog fonta - pecate crta ugradjeni bitmap font"))
    roots = _system_roots()
    line(T("sistemski korijeni (TSA)"), str(len(roots)), "ok" if roots else "warn")
    try:
        import pypdfium2  # noqa: F401

        preview = True
    except Exception:  # noqa: BLE001
        preview = False
    line(T("preview (pypdfium2)"), T("da") if preview else T("ne"), "ok")

    tmp = tempfile.mkdtemp(prefix="potpis-selftest-")
    signer = soft_signer("SELF TEST")
    a_cert = signer.signing_cert

    src = os.path.join(tmp, "self-test.pdf")
    out = os.path.join(tmp, "self-test-signed.pdf")
    try:
        from pypdf import PdfWriter

        w = PdfWriter()
        w.add_blank_page(width=595, height=842)
        with open(src, "wb") as f:
            w.write(f)
        line(T("privremeni PDF"), src, "ok")
    except Exception as exc:  # noqa: BLE001
        line(T("privremeni PDF"), f"{type(exc).__name__}: {exc}", "fail")
        return 1

    box = (60, 600, 400, 700)
    sargs = SimpleNamespace(
        input=src, out=out, visible=True, page=1, box=",".join(str(v) for v in box),
        field="Signature1", ku="non_repudiation",
        font=None, font_size=10, tsa=args.tsa, tsa_auth=getattr(args, "tsa_auth", None),
        trust=args.trust, lt=False, lta=False, no_tsa=not args.tsa,
    )
    meta = PdfSignatureMetadata(field_name="Signature1",
                                md_algorithm="sha256", signer_key_usage={"non_repudiation"})
    try:
        with_ts = _sign_document(sargs, signer, meta, box, _timestamper(sargs))
        line(T("potpisivanje"), T("da") + " (" + (T("sa zigom") if with_ts else T("bez ziga")) + ")")
    except Exception as exc:  # noqa: BLE001
        line(T("potpisivanje"), f"{type(exc).__name__}: {exc}", "fail")
        return 1

    try:
        with open(out, "rb") as f:  # validacija mora unutar 'with' (cita byte range)
            sig = list(PdfFileReader(f).embedded_signatures)[0]
            st = validate_pdf_signature(sig, ValidationContext(trust_roots=[a_cert],
                                                               allow_fetching=False))
            line(T("validacija"),
                 f"intact={st.intact} valid={st.valid} trusted={st.trusted}",
                 "ok" if (st.intact and st.valid and st.trusted) else "fail")
            line(T("vremenski zig"), T("da") if st.timestamp_validity else T("ne"), "ok")
    except Exception as exc:  # noqa: BLE001
        line(T("validacija"), f"{type(exc).__name__}: {exc}", "fail")

    print(T("rezultat") + ": " + (T("radi") if state["ok"] else T("NE RADI")))
    return 0 if state["ok"] else 1


def cmd_list(args) -> int:
    _ensure_lib()
    import pkcs11
    from pkcs11 import Attribute, ObjectClass

    pin = _pin(args) if args.login else None
    for token in pkcs11.lib(LIB).get_tokens():
        print(f"\ntoken: {token.label!r}  model={token.model!r}  serial={token.serial!r}")
        try:
            session = token.open(user_pin=pin, rw=False)
        except Exception as exc:  # noqa: BLE001
            print(T("   ! ne mogu otvoriti sesiju:"), exc)
            continue
        for cls, kind in ((ObjectClass.CERTIFICATE, "CERT"), (ObjectClass.PRIVATE_KEY, "KEY ")):
            try:
                for o in session.get_objects({Attribute.CLASS: cls}):
                    oid = bytes(o[Attribute.ID]).hex() if o[Attribute.ID] else "-"
                    print(f"   {kind} id={oid}  label={o[Attribute.LABEL]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"   {kind} " + T("nedostupno:") + f" {type(exc).__name__}: {exc}")
    return 0


def _ensure_lib() -> None:
    """Jasna poruka ako PKCS#11 modul nije na ocekivanom mjestu."""
    if not os.path.exists(LIB):
        raise SystemExit(
            T("PKCS#11 modul nije nadjen") + f": {LIB}\n  "
            + T("instaliraj AKD middleware ili zadaj --lib / POTPIS_LIB")
        )


def _signer(args, user_pin):
    from pyhanko.sign.pkcs11 import PKCS11Signer

    return PKCS11Signer(
        _session(args.token, user_pin),
        cert_id=_hexorNone(args.cert_id),
        key_id=_hexorNone(args.key_id),
        key_label=args.key_label or None,
        use_raw_mechanism=True,  # AKD modul nudi samo CKM_RSA_PKCS
    )


def cmd_check(args) -> int:
    _ensure_lib()
    signer = _signer(args, _pin(args))
    cert = signer.signing_cert
    print("token:       ", args.token)
    print("cert id:     ", args.cert_id or "(auto)")
    print("key id:      ", args.key_id or "(auto)")
    print("subject:     ", cert.subject.human_friendly)
    print("issuer:      ", cert.issuer.human_friendly)
    print(T("vrijedi do:") + "  ", cert["tbs_certificate"]["validity"]["not_after"].native)
    print(T("\nOK: PIN prihvaćen, certifikat i privatni ključ dohvaćeni. Ništa nije potpisano."))
    return 0


def _existing_sig_boxes(path: str) -> list[tuple[float, float, float, float]]:
    """Pravokutnici vec popunjenih potpisnih polja (da novi pecat ne prekrije stari)."""
    out: list[tuple[float, float, float, float]] = []
    try:
        from pypdf import PdfReader

        for page in PdfReader(path).pages:
            for annot in page.get("/Annots", []) or []:
                obj = annot.get_object()
                if obj.get("/FT") == "/Sig" and obj.get("/V") is not None and obj.get("/Rect"):
                    r = tuple(float(v) for v in obj["/Rect"])
                    if abs(r[2] - r[0]) > 1 and abs(r[3] - r[1]) > 1:
                        out.append(r)
    except Exception:  # noqa: BLE001
        pass
    return out


def _overlap(a, b, gap: float = 6.0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0]
                or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _auto_box(page_size: tuple[float, float], taken=()) -> tuple[float, float, float, float]:
    """Pecat u donji desni kut: 248x64 pt, 28 pt od ruba.

    Ako je taj prostor vec zauzet (npr. pecat banke), pomice se prema gore, pa ulijevo.
    """
    w, h = page_size
    bw, bh = 248.0, 64.0   # kompaktno: 3 reda teksta bez praznine
    x1, y = w - 28.0 - bw, 28.0
    for _ in range(24):
        cand = (x1, y, x1 + bw, y + bh)
        if not any(_overlap(cand, t) for t in taken):
            return cand
        y += bh + 8.0
        if y + bh > h - 28.0:
            x1, y = x1 - bw - 8.0, 28.0
            if x1 < 28.0:
                break
    return (w - 276.0, 28.0, w - 28.0, 92.0)


def _page_size(path: str, page: int = 1) -> tuple[float, float]:
    try:
        from pypdf import PdfReader

        box = PdfReader(path).pages[max(page - 1, 0)].mediabox
        return float(box.width), float(box.height)
    except Exception:  # noqa: BLE001
        return 595.0, 842.0  # A4


def _add_doc_timestamp(path: str, timestamper, trust: str) -> bool:
    """PAdES-LTA: dodaj dokument-timestamp (zaseban objekt, kao Certilia Signer)."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.signers.pdf_signer import PdfTimeStamper

    part = path + ".part"
    try:
        with open(path, "rb") as inf, open(part, "wb") as outf:
            writer = IncrementalPdfFileWriter(inf)
            PdfTimeStamper(timestamper, field_name="Timestamp").timestamp_pdf(
                writer, md_algorithm="sha256",
                validation_context=_vc(trust, fetching=True), output=outf,
            )
        os.replace(part, path)
        return True
    except BaseException:  # noqa: BLE001
        if os.path.exists(part):
            try:
                os.unlink(part)
            except OSError:
                pass
        raise


def _run_sign(args, signer, meta, box, ts):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.fields import SigFieldSpec
    from pyhanko.sign.signers.pdf_signer import PdfSigner

    part = args.out + ".part"
    try:
        with open(args.input, "rb") as inf, open(part, "wb") as outf:
            writer = IncrementalPdfFileWriter(inf)
            stamp_style = None
            field_kwargs: dict = {}
            if box:
                stamp_style = _visible_stamp_style(
                    writer, box, _stamp_lines(signer), args.font, args.font_size
                )
                if stamp_style is not None:
                    field_kwargs = dict(on_page=args.page - 1, box=box)
            PdfSigner(
                meta,
                signer,
                timestamper=ts,
                stamp_style=stamp_style,
                new_field_spec=SigFieldSpec(
                    sig_field_name=(args.field or meta.field_name), **field_kwargs
                ),
            ).sign_pdf(writer, output=outf)
        os.replace(part, args.out)  # nikad ne ostavljamo polovicni PDF
    except BaseException:
        if os.path.exists(part):  # ne ostavljaj ni prazan .part za sobom
            try:
                os.unlink(part)
            except OSError:
                pass
        raise


def _sig_fields(path: str) -> tuple[list[str], list[str]]:
    """(sva potpisna polja, popunjena potpisna polja) u PDF-u."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.fields import enumerate_sig_fields

    with open(path, "rb") as f:
        reader = PdfFileReader(f)
        all_names = [name for name, _ref, _fld in enumerate_sig_fields(reader)]
        filled = [name for name, _ref, _fld in enumerate_sig_fields(reader, filled_status=True)]
    return all_names, filled


def _pick_field(path: str, requested: str | None) -> tuple[str, str | None]:
    """Odaberi naziv potpisnog polja; ako je zadano polje vec potpisano, jasna greska."""
    try:
        names, filled = _sig_fields(path)
    except Exception:  # noqa: BLE001
        names, filled = [], []
    if requested:
        if requested in filled:
            raise SystemExit(
                f"{T('polje')} '{requested}' {T('je u ovom PDF-u već potpisano')} "
                f"({T('postojeća polja:')} {', '.join(names) or '-'}).\n"
                + T("Potpisi originalni PDF ili pusti alat da sam odabere slobodno polje.")
            )
        return requested, None
    if "Signature1" not in names:
        return "Signature1", None
    i = 2
    while f"Signature{i}" in names:
        i += 1
    return f"Signature{i}", (
        f"{T('PDF već sadrži potpisna polja')} ({', '.join(names)}); "
        f"{T('novi potpis ide u polje')} 'Signature{i}'"
    )


def _cert_has_revocation_info(signer) -> bool:
    """Ima li certifikat CRL/OCSP adrese? AKD ih ne objavljuje, pa je PAdES-LT bez predmeta."""
    try:
        cert = signer.signing_cert
        return bool(cert.crl_distribution_points or cert.ocsp_urls)
    except Exception:  # noqa: BLE001
        return True  # ne diramo ako ne mozemo provjeriti


def _sign_document(args, signer, meta, box, timestamper) -> bool:
    """Potpisi dokument. Ako PAdES-LT ili vremenski zig ne prodju, probaj skromniju varijantu.

    Vraca True ako je potpis nastao s vremenskim zigom, inace False.
    """
    from dataclasses import replace

    plain = replace(meta, embed_validation_info=False, validation_context=None, use_pades_lta=False)
    cands: list[tuple[str, object, object]] = []
    metas = [(meta, "PAdES-LT")] if meta.embed_validation_info else []
    metas.append((plain, T("osnovni potpis")))
    for m, label in metas:
        cands.append((label + (T(" + vremenski žig") if timestamper is not None else ""), m, timestamper))
        cands.append((label, m, None))

    seen = set()
    unique: list[tuple[str, object, object]] = []
    for label, m, ts in cands:
        key = (m is plain, ts is not None)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, m, ts))

    last: Exception | None = None
    # pyHanko/certvalidator znaju u log ispisati cijeli traceback za neuspjeli TSA lanac,
    # a to je ovdje ocekivano (soft-fail) - nas vlastiti ispis je dovoljan.
    with _quiet_pyhanko():
        for i, (label, m, ts) in enumerate(unique):
            try:
                _run_sign(args, signer, m, box, ts)
                if i:
                    print(T("  -> potpisano varijantom: ") + label)
                return ts is not None
            except Exception as exc:  # noqa: BLE001
                last = exc
                if i < len(unique) - 1:
                    print(f"{T('UPOZORENJE')} [{label}] {T('nije prošao:')} "
                          f"{type(exc).__name__}: {str(exc)[:140]}")
    assert last is not None
    raise last



def cmd_sign(args) -> int:
    _ensure_lib()
    args = _apply_offline(args)
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign.fields import SigFieldSpec
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
    from pyhanko.sign.timestamps import HTTPTimeStamper

    args.field, field_note = _pick_field(args.input, args.field)
    if field_note:
        print(field_note)
    signer = _signer(args, _pin(args))
    if args.lt and not _cert_has_revocation_info(signer):
        print(T("certifikat nema CRL/OCSP adrese - PAdES-LT preskocen (nema sto ugraditi)"))
        args.lt = False
    want_lt = bool(args.lt)
    vc = _vc(args.trust, fetching=True) if want_lt else None
    meta = PdfSignatureMetadata(
        field_name=args.field,
        md_algorithm="sha256",
        signer_key_usage={x.strip() for x in args.ku.split(",") if x.strip()},
        embed_validation_info=bool(vc),
        validation_context=vc,
        use_pades_lta=False,  # dokument-timestamp dodajemo zasebno (vidi _add_doc_timestamp)
    )
    box = None
    if args.visible:
        if args.box:
            box = tuple(float(v) for v in args.box.split(","))
            if len(box) != 4:
                raise SystemExit(T('--box treba biti u obliku "x1,y1,x2,y2"'))
        else:
            box = _auto_box(_page_size(args.input, args.page),
                            _existing_sig_boxes(args.input))

    timestamper = _timestamper(args)
    if args.lta and timestamper is None:
        print(T("--lta traži vremenski žig (--tsa)") + " -> " + T("preskacem dokument-timestamp"))
        args.lta = False
    with_ts = _sign_document(args, signer, meta, box, timestamper)
    print(T("potpisano ->"), args.out)
    if args.lta and timestamper is not None:
        try:
            if _add_doc_timestamp(args.out, timestamper, args.trust):
                print("  " + T("dokument-timestamp dodan") + " (PAdES-LTA)")
        except Exception as exc:  # noqa: BLE001
            print(f"  {T('UPOZORENJE')} {T('dokument-timestamp nije prosao')}: "
                  f"{type(exc).__name__}: {str(exc)[:120]}")
    if want_lt:
        try:
            from pyhanko.pdf_utils.reader import PdfFileReader

            with open(args.out, "rb") as f:
                has_dss = bool(PdfFileReader(f).root.get("/DSS"))
            print("  PAdES-LT:", T("DSS s dokazima o opozivu ugrađen") if has_dss
                  else T("DSS nije ugrađen (nema dostupnih OCSP/CRL podataka)"))
        except Exception:  # noqa: BLE001
            pass
    if not with_ts and timestamper is not None:
        print(T("(potpis je bez vremenskog žiga)"))
    if not args.visible:
        print(T("(potpis je nevidljiv na stranici; za vidljivi okvir dodaj --visible)"))
    if args.no_verify:
        return 0
    return cmd_verify(args)


@lru_cache(maxsize=1)
def _system_roots() -> list:
    """Trust anchori za validaciju TSA lanca: macOS keychain + certifi/SSL bundle (sve platforme)."""
    import asn1crypto.pem
    from asn1crypto import x509

    roots: list = []
    pem_blobs: list[bytes] = []

    if sys.platform == "darwin":
        import subprocess

        try:
            out = subprocess.run(
                ["security", "find-certificate", "-a", "-p",
                 "/System/Library/Keychains/SystemRootCertificates.keychain"],
                capture_output=True, text=True, check=False, timeout=20,
            )
            pem_blobs.append(out.stdout.encode())
        except Exception:  # noqa: BLE001
            pass

    try:  # certifi radi na svim platformama
        import certifi

        with open(certifi.where(), "rb") as f:
            pem_blobs.append(f.read())
    except Exception:  # noqa: BLE001
        pass

    if sys.platform.startswith("linux"):  # ako certifi nema, sistemski bundle
        for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            try:
                with open(path, "rb") as f:
                    pem_blobs.append(f.read())
            except Exception:  # noqa: BLE001
                continue

    for blob in pem_blobs:
        for block in re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", blob, re.S):
            try:
                roots.append(x509.Certificate.load(asn1crypto.pem.unarmor(block)[2]))
            except Exception:  # noqa: BLE001
                continue
    return roots


def _apply_offline(args):
    """U offline nacinu nema nijednog mreznog poziva: bez ziga, bez LT/LTA i bez dohvata."""
    global OFFLINE
    if not (getattr(args, "offline", False) or OFFLINE):
        return args
    OFFLINE = True
    if getattr(args, "tsa", None) or getattr(args, "lta", False) or getattr(args, "lt", False):
        print(T("offline: bez vremenskog ziga, LT/LTA i dohvata s mreze"))
    args.tsa = None
    args.no_tsa = True
    args.lta = False
    args.lt = False
    return args


def _vc(trust: str, fetching: bool = False, local_roots: bool | None = None):
    import asn1crypto.pem
    from asn1crypto import x509
    from pyhanko_certvalidator import ValidationContext

    def load(path: str):
        with open(path, "rb") as f:
            data = f.read()
        try:
            return x509.Certificate.load(asn1crypto.pem.unarmor(data)[2])  # PEM
        except Exception:  # noqa: BLE001
            return x509.Certificate.load(data)  # DER (.cer na Windowsima)

    trust_path = trust if os.path.isabs(trust) else os.path.join(CA_DIR, trust)
    if not CA_DIR and not os.path.isabs(trust):
        raise SystemExit(
            T("ne mogu naci AKD certifikate (CA_DIR)") +
            ": --ca-dir ILI POTPIS_CA_DIR, odnosno --trust /putanja/AKDCARoot.pem"
        )
    if os.path.isdir(trust_path):
        raise SystemExit(T("ne mogu naci korijenski certifikat")
                         + f": {trust_path} ({T('ocekujem datoteku, ne mapu')})")
    if not os.path.exists(trust_path):
        raise SystemExit(T("ne mogu naci korijenski certifikat") + f": {trust_path}")
    others = [
        load(p)
        for p in sorted(
            glob.glob(os.path.join(CA_DIR, "*.pem"))
            + glob.glob(os.path.join(CA_DIR, "*.cer"))
            + glob.glob(os.path.join(CA_DIR, "*.crt"))
        )
        if "TEST" not in os.path.basename(p) and p != trust_path
    ]
    anchors = [load(trust_path)]
    # local_roots=True: koristi sistemske korijene, ali BEZ dohvata s mreze
    use_sys = fetching if local_roots is None else local_roots
    if use_sys and not OFFLINE:
        anchors += _system_roots()
    return ValidationContext(
        trust_roots=anchors,
        other_certs=others,
        allow_fetching=fetching and not OFFLINE,
        revocation_mode="soft-fail",
    )


@contextmanager
def _quiet_pyhanko():
    """pyHanko/certvalidator znaju u log ispisati cijeli traceback za ocekivane slucajeve."""
    names = ("pyhanko", "pyhanko.sign", "pyhanko.sign.validation", "pyhanko_certvalidator")
    loggers = [logging.getLogger(n) for n in names]
    prev = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        for lg, lvl in zip(loggers, prev):
            lg.setLevel(lvl)


def _raw_sig_objects(path: str) -> list[tuple[str, object]]:
    """(naziv, /V objekt) za sva popunjena potpisna polja - i ona koja pyHanko preskace."""
    from pyhanko.pdf_utils.reader import PdfFileReader

    found: list[tuple[str, object]] = []
    seen: set[int] = set()

    def take(obj):
        if obj.get("/FT") != "/Sig" or obj.get("/V") is None:
            return
        found.append((str(obj.get("/T") or "?"), obj["/V"].get_object()))

    def walk(fields, depth: int = 0):
        for ref in fields or []:
            obj = ref.get_object()
            if id(obj) in seen or depth > 8:
                continue
            seen.add(id(obj))
            take(obj)
            if obj.get("/Kids"):
                walk(obj["/Kids"], depth + 1)

    with open(path, "rb") as f:
        reader = PdfFileReader(f)
        for page in reader.root["/Pages"].get("/Kids", []):
            for annot in page.get_object().get("/Annots", []) or []:
                take(annot.get_object())
        acro = reader.root.get("/AcroForm")
        if acro is not None:  # zna biti i IndirectObject
            walk(acro.get_object().get("/Fields"))
    out: list[tuple[str, object]] = []
    seen_objs: set[int] = set()
    for name, obj in found:  # bez duplikata (isto polje je i annot i /Fields)
        if id(obj) in seen_objs:
            continue
        seen_objs.add(id(obj))
        out.append((name, obj))
    return out


def _legacy_sha1_verify(path: str, sig_obj) -> dict:
    """Provjeri stari profil adbe.pkcs7.sha1 (SHA-1, bez signed attributes).

    U tom profilu CMS NIJE detached: njegov ugradjeni sadrzaj je SHA-1 digest
    ByteRange dijela PDF-a, a potpis je nad tim digestom. Zato se dokument moze
    provjeriti iako pyHanko odbija sam SubFilter.
    """
    import asyncio
    import hashlib
    from asn1crypto import cms as a_cms, x509 as a_x509
    from cryptography import x509 as cx
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    from pyhanko_certvalidator import CertificateValidator

    out: dict = {}
    byte_range = [int(v) for v in sig_obj["/ByteRange"]]
    contents = getattr(sig_obj["/Contents"], "original_bytes", None)
    if contents is None:
        raise ValueError("ne mogu procitati /Contents")
    with open(path, "rb") as f:
        data = f.read()
    content = data[byte_range[0]:byte_range[0] + byte_range[1]] + \
        data[byte_range[2]:byte_range[2] + byte_range[3]]
    cms = a_cms.ContentInfo.load(bytes(contents).rstrip(b"\x00"))
    sd = cms["content"]
    signer_info = sd["signer_infos"][0]
    encap = sd["encap_content_info"]["content"].native

    out["intact"] = bytes(encap) == hashlib.sha1(content).digest()
    out["covers_whole"] = byte_range[2] + byte_range[3] == len(data) and byte_range[0] == 0

    certs = [a_x509.Certificate.load(c.chosen.dump()) for c in (sd["certificates"] or [])]
    serial = signer_info["sid"].chosen["serial_number"].native
    signer_cert = next((c for c in certs if c.serial_number == serial), None)
    if signer_cert is None:
        raise ValueError("u potpisu nema certifikata potpisnika")
    out["signer_cert"] = signer_cert

    pub = cx.load_der_x509_certificate(signer_cert.dump()).public_key()
    signature = signer_info["signature"].native
    out["valid"] = False
    for pad, msg, h in ((padding.PKCS1v15(), bytes(encap), hashes.SHA1()),
                        (ec.ECDSA(hashes.SHA1()), bytes(encap), None)):
        try:
            if h is None:
                pub.verify(signature, msg, pad)
            else:
                pub.verify(signature, msg, pad, h)
            out["valid"] = True
            break
        except InvalidSignature:
            continue
        except Exception:  # noqa: BLE001  (pogresan tip kljuca za tu varijantu)
            continue

    others = [c for c in certs if c is not signer_cert]
    try:
        # lanac gradimo iz certifikata u PDF-u + LOKALNIH korijena (bez mreze)
        vc = _vc("AKDCARoot.pem", fetching=False, local_roots=True)
        asyncio.run(CertificateValidator(signer_cert, others, vc).async_validate_path())
        out["trusted"], out["chain_error"] = True, None
    except Exception as exc:  # noqa: BLE001
        out["trusted"], out["chain_error"] = False, f"{type(exc).__name__}: {str(exc)[:90]}"
    return out


def _sig_type(sig) -> str:
    """'/Sig' ili '/DocTimeStamp' (PAdES-LTA dokument-timestamp)."""
    try:
        return str(sig.sig_object.get("/Type") or "/Sig")
    except Exception:  # noqa: BLE001
        return "/Sig"


def _raw_sig_fields(path: str) -> list[tuple[str, str]]:
    """(naziv, SubFilter) popunjenih potpisnih polja - i onih koje pyHanko preskace."""
    return [(name, str(obj.get("/SubFilter") or obj.get("/Filter") or "?").lstrip("/"))
            for name, obj in _raw_sig_objects(path)]


def _unreadable_note(path: str, parsed_names: set[str]) -> str | None:
    """Ako ima popunjenih polja koja pyHanko nije procitao, objasni zasto."""
    extra = [(n, sf) for n, sf in _raw_sig_fields(path) if n not in parsed_names]
    if not extra:
        return None
    parts = ", ".join(f"{n} ({sf})" for n, sf in extra)
    return T("potpis postoji, ali ga ne mogu procitati") + f": {parts}"


def signature_summary(path: str, trust: str = "AKDCARoot.pem") -> tuple[list[dict], str, str | None]:
    """Brzi lokalni pregled potpisa u PDF-u (bez mreze). Vraca (redovi, sljedece polje)."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp

    vc = None  # gradi se tek kad stvarno ima sto validirati
    rows: list[dict] = []
    with _quiet_pyhanko():
        with open(path, "rb") as f:
            try:
                sigs = list(PdfFileReader(f).embedded_signatures)
            except Exception as exc:  # noqa: BLE001
                # pyHanko ne podrzava npr. adbe.pkcs7.sha1 i onda pukne vec pri citanju
                raw = str(exc)
                hint = ("adbe.pkcs7.sha1" if "pkcs7.sha1" in raw or "SubFilter" in raw else
                        f"{type(exc).__name__}: {raw[:90]}")
                return [dict(field="-", warn=T("potpis nije citljiv") + f": {hint}")], *_pick_field(path, None)
            for fld, obj in _raw_sig_objects(path):
                if str(obj.get("/SubFilter") or "") != "/adbe.pkcs7.sha1":
                    continue
                try:
                    res = _legacy_sha1_verify(path, obj)
                    cert = res["signer_cert"]
                    rows.append(dict(
                        field=fld, signer=cert.subject.native.get("common_name"),
                        time=None, intact=res["intact"], valid=res["valid"],
                        trusted=res["trusted"], timestamp=False,
                        note=T("stari SHA-1 profil (bez ziga)")
                        + ("" if res["covers_whole"] else
                           " + " + T("dokument ima izmjena nakon potpisa")),
                    ))
                except Exception as exc:  # noqa: BLE001
                    rows.append(dict(field=fld, warn=(
                        f"{T('potpis nije citljiv')} "
                        f"({str(obj.get('/SubFilter') or '?').lstrip('/')}): "
                        f"{type(exc).__name__}: {str(exc)[:60]}")))
            parsed_names = {getattr(x, "field_name", "?") for x in sigs} | {
                f for f, _ in _raw_sig_objects(path) if str(_.get("/SubFilter") or "") == "/adbe.pkcs7.sha1"
            }
            note_raw = _unreadable_note(path, parsed_names)
            if note_raw:
                rows.append(dict(field="-", warn=note_raw))
            for sig in sigs:
                try:
                    if _sig_type(sig) == "/DocTimeStamp":  # PAdES-LTA dokument-timestamp
                        ts_st = validate_pdf_timestamp(sig, _vc(trust, fetching=True))
                        rows.append(dict(field=T("dokument-timestamp"), signer=T("TSA"),
                                         time=getattr(ts_st, "timestamp", None), intact=True,
                                         valid=True, trusted=ts_st.trusted, timestamp=True))
                        continue
                    if vc is None:
                        vc = _vc(trust)
                    st = validate_pdf_signature(sig, vc)
                    cert = st.signing_cert
                    rows.append(dict(
                        field=sig.field_name,
                        signer=(cert.subject.native.get("common_name") if cert else None),
                        time=st.signer_reported_dt,
                        intact=st.intact,
                        valid=st.valid,
                        trusted=st.trusted,
                        timestamp=st.timestamp_validity is not None,
                    ))
                except Exception as exc:  # noqa: BLE001
                    rows.append(dict(field=getattr(sig, "field_name", "?"),
                                     error=f"{type(exc).__name__}: {str(exc)[:90]}"))
    next_field, note = _pick_field(path, None)
    return rows, next_field, note


def _wrap(text: str, width: int) -> list[str]:
    """Podijeli tekst na redove do ~width znakova (za uski statusni prozor)."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _cert_lines(cert, indent: str = "            ") -> list[str]:
    """Citljiv, kratak ispis potpisnika i izdavatelja (za uski statusni prozor)."""
    def lab(name: str) -> str:
        return f"{name:<12}"

    subj = cert.subject.native
    out = [f"{lab(T('potpisnik:'))} {subj.get('common_name') or '?'}"]
    if subj.get("serial_number"):
        out.append(f"{indent}{lab(T('serijski:'))} {subj['serial_number']}")
    ou = subj.get("organizational_unit_name") or subj.get("organizational_unit")
    if ou:
        out.append(f"{indent}{lab('OU:')} {ou}")
    iss = cert.issuer.native
    org = iss.get("organization_name") or iss.get("organization")
    out.append(f"{lab(T('izdao:'))} {iss.get('common_name') or '?'}"
               + (f" ({org})" if org else ""))
    return out


def _state_lines(summary: str, indent: str = "    ") -> list[str]:
    """pyHanko summary() je jedan dugacak red s zarezima -> svako stanje u svoj red."""
    return [f"{indent}· {part.strip()}" for part in summary.split(",") if part.strip()]


def format_summary(rows: list[dict], next_field: str, note: str | None = None) -> str:
    pravi = [r for r in rows if "error" not in r and "warn" not in r]
    out = [f"{T('potpisa u PDF-u:')} {len(pravi)}",
           f"{T('sljedeći potpis ide u polje:')} {next_field}"]
    if note:
        out.append(f"  ({note})")
    for i, r in enumerate(rows, 1):
        out.append("")
        if "warn" in r:
            out.append(f" {i}) {T('UPOZORENJE')}")
            out.extend(f"    {line}" for line in _wrap(r["warn"], 46))
            continue
        if "error" in r:
            out.append(f" {i}) {r['field']} · {T('GREŠKA')}")
            out.extend(f"    {line}" for line in _wrap(r["error"], 46))
            continue
        def lab(name: str) -> str:
            return f"{name:<12}"

        out.append(f" {i}) {r['field']}")
        out.append(f"    {lab(T('potpisnik:'))} {r['signer'] or '-'}")
        out.append(f"    {lab(T('vrijeme:'))} {r['time']}")
        st = T("valjan") if r["valid"] else T("NEVALJAN")
        tr = T("pouzdan") if r["trusted"] else T("nepouzdan")
        ts = T("žig da") if r["timestamp"] else T("žig ne")
        out.append(f"    {lab(T('stanje:'))} {st} · {tr} · {ts}")
        if r.get("note"):
            out.extend(f"    {line}" for line in _wrap(r["note"], 46))
    return "\n".join(out) + "\n"


def cmd_verify(args) -> int:
    args = _apply_offline(args)
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp

    path = args.input if args.cmd == "verify" else args.out
    with _quiet_pyhanko():
        with open(path, "rb") as f:
            sigs = list(PdfFileReader(f).embedded_signatures)
    legacy = []
    with _quiet_pyhanko():
        for fld, obj in _raw_sig_objects(path):
            if str(obj.get("/SubFilter") or "") == "/adbe.pkcs7.sha1":
                try:
                    legacy.append((fld, _legacy_sha1_verify(path, obj)))
                except Exception as exc:  # noqa: BLE001
                    legacy.append((fld, {"error": f"{type(exc).__name__}: {str(exc)[:90]}"}))
    if not sigs and not legacy:
        note = _unreadable_note(path, set())
        print(f"{T('UPOZORENJE')} {note}" if note else f"{T('nema potpisa u')} {path}")
        return 1
    with _quiet_pyhanko(), open(path, "rb") as f:
        sigs = list(PdfFileReader(f).embedded_signatures)
        vc = _vc(args.trust)  # potpisnik: samo AKD korijeni
        ts_vc = _vc(args.trust, fetching=True)  # TSA lanac: + sistemski korijeni
        for sig in sigs:
            if _sig_type(sig) == "/DocTimeStamp":
                ts_st = validate_pdf_timestamp(sig, ts_vc)
                print(f"  {T('dokument-timestamp')}: {getattr(ts_st, 'timestamp', '?')}  "
                      f"trusted={ts_st.trusted}  "
                      f"{T('pokriva:')} {getattr(ts_st.coverage, 'name', ts_st.coverage)}")
                print(f"  {T('stanje:')}")
                for ln in _state_lines(ts_st.summary()):
                    print("  " + ln)
                continue
            st = validate_pdf_signature(sig, vc, ts_validation_context=ts_vc)
            cert = st.signing_cert
            for ln in (_cert_lines(cert) if cert else [f"  {T('potpisnik:')}  -"]):
                print("  " + ln)
            problem = f"  {T('problem:')} {st.trust_problem_indic.name}" if st.trust_problem_indic else ""
            print(f"  intact={st.intact} valid={st.valid} trusted={st.trusted}{problem}")
            mech = getattr(st.pkcs7_signature_mechanism, "native", st.pkcs7_signature_mechanism)
            print(f"  {T('algoritam:')}  {st.md_algorithm} / {mech}")
            print(f"  {T('pokriva:')}    {getattr(st.coverage, 'name', st.coverage)}")
            print(f"  {T('vrijeme:')}    {st.signer_reported_dt}")
            print(f"  timestamp:  {T('da') if st.timestamp_validity else T('ne')}")
            print(f"  {T('stanje:')}")
            for ln in _state_lines(st.summary()):
                print("  " + ln)
        for fld, res in legacy:
            if "error" in res:
                print(f"  {fld} ({T('stari profil')} adbe.pkcs7.sha1): "
                      f"{T('UPOZORENJE')} {res['error']}")
                continue
            cert = res["signer_cert"]
            for ln in _cert_lines(cert):
                print("  " + ln)
            print(f"  [{T('stari profil')}: adbe.pkcs7.sha1]")
            print(f"  intact={res['intact']} valid={res['valid']} trusted={res['trusted']}"
                  + (f"  {T('problem:')} {res['chain_error']}" if res.get("chain_error") else ""))
            print(f"  {T('algoritam:')}  sha1 / rsassa_pkcs1v15")
            print(f"  {T('pokriva:')}    " + (T("cijeli dokument") if res["covers_whole"]
                                              else T("dio dokumenta")))
            if not res["covers_whole"]:
                print(f"  {T('UPOZORENJE')}: {T('dokument ima izmjena nakon potpisa')}")
            print(f"  {T('UPOZORENJE')}: SHA-1, bez signed attributes -> "
                  f"{T('vrijeme potpisa nije kriptografski zasticeno')}")
    return 0


# ponytail: --help je samo hrvatski (GUI je dvojezican); prevesti ako zatreba
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", nargs="?", help="ulazni PDF (ili potpisani PDF za --verify)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    ap.add_argument("--self-test", action="store_true", dest="self_test",
                    help="provjeri radi li alat na ovoj platformi (softverski certifikat, bez kartice)")
    ap.add_argument("--lib", default=None, help="putanja do PKCS#11 modula (default: autodetekcija)")
    ap.add_argument("--ca-dir", default=None, dest="ca_dir", help="mapa s AKD PEM certifikatima")
    ap.add_argument("--list", action="store_true", help="popisi tokene/certifikate/kljuceve")
    ap.add_argument("--login", action="store_true", help="s --list: prijavi se PIN-om")
    ap.add_argument("--check", action="store_true", help="proba PIN i dohvat kljuca (bez potpisa)")
    ap.add_argument("--verify", action="store_true", help="samo validiraj potpise u PDF-u")
    ap.add_argument("--token", default="Signature", help="dio naziva tokena (default: Signature)")
    ap.add_argument("--cert-id", default="", help="hex ID certifikata (default: auto)")
    ap.add_argument("--key-id", default="", help="hex ID privatnog kljuca (default: auto)")
    ap.add_argument("--key-label", default="", help="naziv privatnog kljuca (default: auto)")
    ap.add_argument("--field", default=None,
                    help="naziv potpisnog polja (default: prvo slobodno: Signature1, Signature2, ...)")
    ap.add_argument("--ku", default="non_repudiation", help="key usage potpisnika")
    ap.add_argument("--visible", action=argparse.BooleanOptionalAction, default=True,
                    help="vidljiv potpisni pecat na stranici (default: da; --no-visible za nevidljiv)")
    ap.add_argument("--page", type=int, default=1, help="stranica za vidljivi potpis (1 = prva)")
    ap.add_argument("--box", default=None,
                    help='okvir pecata "x1,y1,x2,y2" u tockama '
                         '(default: donji desni kut, 248x64 pt)')
    ap.add_argument("--font", default=None, help="TrueType font za vidljivi potpis (default: Arial Unicode)")
    ap.add_argument("--font-size", type=int, default=10, dest="font_size",
                    help="najveca velicina teksta u vidljivom potpisu (pt)")
    ap.add_argument("--tsa", default="digicert",
                    help='RFC3161 vremenski zig: URL, "digicert"/"javni" (default) ili "none"')
    ap.add_argument("--no-tsa", action="store_true", help="potpis bez vremenskog ziga")
    ap.add_argument("--offline", action="store_true",
                    help="nijedan mrezni poziv: bez TSA/ziga, bez LT/LTA i bez dohvata OCSP/AIA "
                         "(local stays local; env POTPIS_OFFLINE=1)")
    ap.add_argument("--lt", action=argparse.BooleanOptionalAction, default=True,
                    help="PAdES-LT: ugradi dokaze o opozivu (OCSP/CRL) u PDF (default: da)")
    ap.add_argument("--lta", action=argparse.BooleanOptionalAction, default=True,
                    help="PAdES-LTA: nakon potpisa dodaj dokument-timestamp (default: da; "
                         "--no-lta za ne; trazi vremenski zig)")
    ap.add_argument("--tsa-auth", default=None, dest="tsa_auth",
                    help='HTTP basic auth za TSA kao "korisnik:lozinka" (ili env TSA_AUTH)')
    ap.add_argument("--trust", default="AKDCARoot.pem", help="korijenski certifikat za provjeru")
    ap.add_argument("--out", default=None, help="izlazni PDF (default: <ulaz>-signed.pdf)")
    ap.add_argument("--no-verify", action="store_true", help="ne validiraj odmah nakon potpisa")
    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()

    global LIB, CA_DIR
    if args.lib:
        LIB = args.lib
    if args.ca_dir:
        CA_DIR = args.ca_dir
    if args.no_tsa:
        args.tsa = None
    args = _apply_offline(args)
    if args.self_test:
        return self_test(args)
    args.cmd = "list" if args.list else "check" if args.check else "verify" if args.verify else "sign"
    if args.cmd in ("sign", "verify") and not args.input:
        ap.error("treba ulazni PDF")
    if args.cmd in ("sign", "verify") and not os.path.exists(args.input):
        ap.error(T("nema datoteke") + f": {args.input}")
    if args.cmd == "sign":
        base = args.input[:-4] if args.input.lower().endswith(".pdf") else args.input
        args.out = args.out or base + "-signed.pdf"
    if args.cmd == "self-test":
        return 0  # obradeno ranije
    return {"list": cmd_list, "check": cmd_check, "verify": cmd_verify, "sign": cmd_sign}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
