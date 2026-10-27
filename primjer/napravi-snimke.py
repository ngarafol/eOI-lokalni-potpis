#!/usr/bin/env python3
"""Regeneriraj primjere u ovoj mapi iz TRENUTNOG koda.

    .venv/bin/python primjer/napravi-snimke.py

1. nacrta uredan (ilustrativni) račun bez stvarnih podataka i spremi ga kao A4 PDF
2. potpiše ga demo certifikatom nalik eOI (HR + OIB + izdavatelj HRIDCA)
3. iz potpisanog PDF-a izreže STVARNI pečat (pdfium, visoka rezolucija)
4. složi snimke: dokument + uvećani pečat

Pokreni ovo kad god se promijeni izgled ili tekst pečata - tako primjeri ne mogu odstupiti od koda.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POTPIS_OFFLINE", "1")  # demo ne treba vremenski zig

HERE = Path(__file__).resolve().parent
K = 2  # crtamo u 2x pa smanjimo (ostri rubovi)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fonts(pp):
    """(regular, bold) TTF - preko alatove detekcije fonta, s rezervama."""
    from PIL import ImageFont

    regular = pp._default_font()
    if not regular:
        raise SystemExit("nema fonta za snimke (vidi --self-test)")
    bold = next((p for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ) if os.path.exists(p)), regular)
    cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def font(size: int, is_bold: bool = False):
        key = (bold if is_bold else regular, int(size))
        if key not in cache:
            cache[key] = ImageFont.truetype(key[0], key[1])
        return cache[key]

    return font


def gradient(size, top, bottom):
    from PIL import Image

    strip = Image.new("RGB", (1, size[1]))
    for y in range(size[1]):
        k = y / max(size[1] - 1, 1)
        strip.putpixel((0, y), tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)))
    return strip.resize(size)


def rounded_mask(size, radius):
    from PIL import Image, ImageDraw

    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


def card(canvas, box, radius=14):
    """Bijela zaobljena kartica sa sjenom."""
    from PIL import Image, ImageFilter

    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    sh = Image.new("RGBA", (w + 96, h + 96), (0, 0, 0, 0))
    sh.paste(Image.new("RGBA", (w, h), (70, 56, 38, 55)), (48, 48), rounded_mask((w, h), radius))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)), (x0 - 48, y0 - 40))
    solid = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    canvas.alpha_composite(Image.composite(solid, Image.new("RGBA", (w, h), (0, 0, 0, 0)),
                                           rounded_mask((w, h), radius)), (x0, y0))


def draw_invoice(size, font):
    """Uredan ilustrativni račun (svi podaci izmišljeni)."""
    from PIL import Image, ImageDraw

    W, H = size[0] * K, size[1] * K
    im = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    ink, gray, line, teal = (36, 38, 44), (126, 128, 134), (228, 227, 222), (26, 116, 108)
    pad = 56 * K

    def t(xy, text, px, bold=False, fill=ink, anchor="la"):
        d.text(xy, text, font=font(px * K, bold), fill=fill, anchor=anchor)

    d.rounded_rectangle((pad, pad, pad + 34 * K, pad + 34 * K), 9 * K, fill=teal)
    t((pad + 48 * K, pad - 6 * K), "PRIMJER d.o.o.", 23, True)
    t((pad + 48 * K, pad + 22 * K), "Primjerska ulica 1, 10000 Zagreb", 11, fill=gray)
    t((pad + 48 * K, pad + 38 * K), "OIB: 00000000000", 11, fill=gray)
    t((W - pad, pad + 4 * K), "RAČUN br. 1/2026", 19, True, anchor="ra")
    t((W - pad, pad + 32 * K), "Datum izdavanja: 10.10.2026.", 11, fill=gray, anchor="ra")
    t((W - pad, pad + 48 * K), "Dospijeće: 24.10.2026.", 11, fill=gray, anchor="ra")
    d.line((pad, pad + 76 * K, W - pad, pad + 76 * K), fill=line, width=K)

    t((pad, pad + 100 * K), "KUPAC", 9, True, fill=gray)
    t((pad, pad + 116 * K), "Primjer Kupac d.o.o.", 15, True)
    t((pad, pad + 138 * K), "Kupčeva 2, 10000 Zagreb", 11, fill=gray)

    ty = pad + 184 * K
    d.rounded_rectangle((pad, ty, W - pad, ty + 27 * K), 6 * K, fill=(243, 241, 236))
    t((pad + 14 * K, ty + 16 * K), "Opis", 10, True, fill=(70, 72, 78))
    t((W - pad - 150 * K, ty + 16 * K), "Količina", 10, True, fill=(70, 72, 78))
    t((W - pad - 14 * K, ty + 16 * K), "Iznos", 10, True, fill=(70, 72, 78), anchor="ra")

    ry = ty + 46 * K
    t((pad + 14 * K, ry), "Usluga potpisivanja dokumenata", 12)
    t((W - pad - 150 * K, ry), "1", 12)
    t((W - pad - 14 * K, ry), "100,00 EUR", 12, anchor="ra")
    d.line((pad, ry + 26 * K, W - pad, ry + 26 * K), fill=line, width=max(K // 2, 1))

    uy = ry + 58 * K
    t((W - pad - 14 * K, uy), "100,00 EUR", 11, fill=gray, anchor="ra")
    t((W - pad - 14 * K, uy + 20 * K), "PDV 25 %: 25,00 EUR", 11, fill=gray, anchor="ra")
    d.line((W - pad - 230 * K, uy + 38 * K, W - pad, uy + 38 * K), fill=line, width=K)
    t((W - pad - 14 * K, uy + 74 * K), "UKUPNO: 125,00 EUR", 16, True, anchor="ra")

    t((pad, H - pad - 28 * K), "IBAN: HR0000000000000000000", 10, fill=gray)
    t((pad, H - pad - 12 * K),
      "Ilustrativni primjer za dokumentaciju - svi podaci su izmišljeni.", 10, fill=gray)
    return im


def make_demo_pdf(font, path: Path):
    """Nacrtaj A4 račun i spremi ga kao PDF (150 dpi -> 1240x1754 px = 595x842 pt)."""
    from PIL import Image

    a4 = (1240 // K, 1754 // K)  # crtamo u pola, pa K u draw_invoice vrati punu veličinu
    img = draw_invoice(a4, font).convert("RGB")
    img.save(path, "PDF", resolution=150)
    # Pillow u xref upiše "65536" za slobodan objekt (izvan specifikacije); ispravi na 65535
    # da uzorak bude ispravan i za stroge citace.
    raw = path.read_bytes().replace(b"0000000000 65536 f", b"0000000000 65535 f")
    path.write_bytes(raw)
    return path


def stamp_crop(pdf: Path, box, scale: int = 6):
    """Stvarni pečat iz potpisanog PDF-a, visoka rezolucija (crop u PIL-u)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    doc.init_forms()  # bez ovoga pdfium ne crta izgled potpisa
    page = doc[0]
    im = page.render(scale=scale, draw_annots=True).to_pil().convert("RGB")
    W, H = page.get_width(), page.get_height()
    k = im.width / W
    m = 8
    x0, y0, x1, y1 = box
    # u PDF-u je ishodište lijevo-dolje, u slici lijevo-gore
    return im.crop((int((x0 - m) * k), int((H - y1 - m) * k),
                    int((x1 + m) * k), int((H - y0 + m) * k)))


def compose(page_img, stamp_img, font, out: Path):
    """Dokument lijevo, stvarni pečat desno, na toploj podlozi."""
    from PIL import Image, ImageDraw

    W, H = 1240 * K, 800 * K
    canvas = gradient((W, H), (246, 243, 237), (232, 225, 212)).convert("RGBA")

    page_img = page_img.convert("RGBA")
    stamp_img = stamp_img.convert("RGBA")
    doc_w, doc_h = 470 * K, 665 * K
    dx, dy = 90 * K, 42 * K
    card(canvas, (dx, dy, dx + doc_w, dy + doc_h), 14 * K)
    page = page_img.copy()
    page.thumbnail((doc_w, doc_h), Image.LANCZOS)
    canvas.alpha_composite(page, (dx + (doc_w - page.width) // 2, dy + (doc_h - page.height) // 2))

    sw = 470 * K
    sh = int(sw * stamp_img.height / stamp_img.width)
    pad = 20 * K
    sx, sy = 655 * K, 210 * K
    card(canvas, (sx, sy, sx + sw + 2 * pad, sy + sh + 2 * pad), 12 * K)
    canvas.alpha_composite(stamp_img.resize((sw, sh), Image.LANCZOS), (sx + pad, sy + pad))

    caption = font(13 * K)
    draw = ImageDraw.Draw(canvas)
    for cx, cy, text in (
        (dx + doc_w // 2, dy + doc_h + 26 * K, "dokument (ilustracija)"),
        (sx + (sw + 2 * pad) // 2, sy + sh + 2 * pad + 26 * K, "pečat (stvarni izlaz alata)"),
    ):
        draw.text((cx - caption.getlength(text) / 2, cy), text, font=caption,
                  fill=(126, 117, 104, 255))

    canvas.convert("RGB").resize((W // K, H // K), Image.LANCZOS).save(out)
    return out


def main() -> int:
    pp = load("potpisi_pdf", ROOT / "potpisi-pdf.py")
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    font = fonts(pp)
    src, out = HERE / "primjer-racun.pdf", HERE / "_demo-signed.pdf"
    if "--keep-doc" not in sys.argv:  # inače zadrži postojeći primjer-racun.pdf
        make_demo_pdf(font, src)
        print("dokument: primjer-racun.pdf")

    signer = pp.soft_signer("PRIMJER POTPISNIK", str(HERE / "_demo-root.pem"),
                            country="HR", serial="PNOHR-00000000000",
                            issuer_cn="HRIDCA", issuer_org="AKD d.o.o.")
    args = pp.build_parser().parse_args([str(src), "--out", str(out), "--offline"])
    args.field = "Signature1"
    box = pp._auto_box(pp._page_size(str(src), 1))
    print("pecаt:")
    for text, scale in pp._stamp_lines(signer):
        print(f"   {scale:.2f}  {text}")
    pp._sign_document(args, signer, PdfSignatureMetadata(
        field_name="Signature1", md_algorithm="sha256",
        signer_key_usage={"non_repudiation"}), box, None)

    from pypdf import PdfReader

    anns = [a.get_object() for a in (PdfReader(str(out)).pages[0].get("/Annots") or [])]
    sig_anns = [a for a in anns if a.get("/FT") == "/Sig"]
    if not sig_anns or not (sig_anns[0].get("/AP") or {}).get("/N"):
        raise SystemExit("potpis nije primijenjen na demo dokument (nema izgleda potpisa)")

    import pypdfium2 as pdfium
    from PIL import Image

    _doc = pdfium.PdfDocument(str(out))
    _doc.init_forms()
    page_img = _doc[0].render(scale=3, draw_annots=True).to_pil().convert("RGB")
    stamp = stamp_crop(out, box)
    compose(page_img, stamp, font, HERE / "potpis-primjer.png")
    print("snimka: potpis-primjer.png")

    pad = 14
    zoom = Image.new("RGB", (stamp.width + 2 * pad, stamp.height + 2 * pad), "white")
    zoom.paste(stamp, (pad, pad))
    zoom.save(HERE / "potpis-primjer-zoom.png")
    print("snimka: potpis-primjer-zoom.png")

    out.unlink(missing_ok=True)
    (HERE / "_demo-root.pem").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
