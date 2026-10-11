#!/usr/bin/env python3
"""Regeneriraj primjere (snimke) u ovoj mapi iz TRENUTNOG koda.

    .venv/bin/python primjer/napravi-snimke.py

Potpisuje primjer-racun.pdf demo certifikatom nalik eOI (HR + OIB + izdavatelj HRIDCA) i
izvozi preglede iz GUI-ja (HR, EN) te uvecani pecаt. Pokreni ovo kad god se promijeni
izgled ili tekst pecata - tako snimke ne mogu odstupiti od koda.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POTPIS_OFFLINE", "1")  # bez mreze (demo ne treba zig)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    pp = load("potpisi_pdf", ROOT / "potpisi-pdf.py")
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    here = Path(__file__).resolve().parent
    signer = pp.soft_signer("PRIMJER POTPISNIK", str(here / "_demo-root.pem"),
                            country="HR", serial="PNOHR-00000000000",
                            issuer_cn="HRIDCA", issuer_org="AKD d.o.o.")
    src, out = here / "primjer-racun.pdf", here / "_demo-signed.pdf"
    args = pp.build_parser().parse_args([str(src), "--out", str(out), "--offline"])
    args.field = "Signature1"
    box = pp._auto_box(pp._page_size(str(src), 1), pp._existing_sig_boxes(str(src)))
    print("pecаt:")
    for text, scale in pp._stamp_lines(signer):
        print(f"   {scale:.2f}  {text}")
    pp._sign_document(args, signer, PdfSignatureMetadata(
        field_name="Signature1", md_algorithm="sha256",
        signer_key_usage={"non_repudiation"}), box, None)

    from PIL import Image
    gui = ROOT / "potpisi-gui.py"
    for lang, name in (("hr", "potpis-primjer.png"), ("en", "potpis-primjer-en.png")):
        rc = os.system(f'"{sys.executable}" "{gui}" "{out}" --lang {lang} '
                       f'--screenshot "{here / name}"')
        if rc != 0:
            print(f"GRESKA: izvoz {name} nije prosao", file=sys.stderr)
            return rc
        print("snimka:", name)

    preview = Image.open(here / "potpis-primjer.png").convert("RGB")
    k = preview.width / 595.0
    preview.crop((int(311 * k), int(744 * k), int(575 * k), int(820 * k))).save(
        here / "potpis-primjer-zoom.png")
    print("snimka: potpis-primjer-zoom.png")

    out.unlink(missing_ok=True)
    (here / "_demo-root.pem").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
