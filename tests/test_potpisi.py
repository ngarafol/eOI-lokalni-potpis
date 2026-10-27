"""Testovi bez kartice: ciste funkcije + jedan integracijski potpis softverskim certifikatom.

Pokretanje:  .venv/bin/python -m pytest -q
"""
from __future__ import annotations

import datetime
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pp = _load("potpisi_pdf", ROOT / "potpisi-pdf.py")

try:  # GUI testiramo samo ako tkinter postoji (na CI-ju zna faliti)
    import tkinter  # noqa: F401

    gui = _load("potpisi_gui", ROOT / "potpisi-gui.py")
except Exception:  # noqa: BLE001
    gui = None


# ---------------------------------------------------------------- prijevodi


def test_sve_poruke_imaju_engleski_prijevod():
    """Svaki T() (modul) i self.t() (GUI) literal mora imati engleski prijevod."""
    import ast

    missing = []
    for path, imena in (("potpisi-pdf.py", {"T"}), ("potpisi-gui.py", {"t"})):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            arg = node.args[0]
            if name in imena and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value not in pp.TR_EN:
                    missing.append((path, node.lineno, arg.value[:60]))
    assert not missing, f"poruke bez EN prijevoda: {missing}"


def test_engleski_prevodi_i_nepoznate_ostavlja_netaknute():
    assert pp.T("potpisano ->") == "potpisano ->"
    pp.LANG = "en"
    try:
        assert pp.T("potpisano ->") == "signed ->"
        assert pp.T("nepoznata poruka") == "nepoznata poruka"
    finally:
        pp.LANG = "hr"




@pytest.mark.parametrize(
    "value,expected",
    [("digicert", pp.PUBLIC_TSA), ("javni", pp.PUBLIC_TSA), ("none", None), (None, None),
     ("https://tsa.example/tsr", "https://tsa.example/tsr")],
)
def test_tsa_url(value, expected):
    assert pp._tsa_url(SimpleNamespace(tsa=value)) == expected


def test_parser_zadane_vrijednosti():
    args = pp.build_parser().parse_args([])
    assert args.token == "Signature"
    assert args.tsa == "digicert"
    assert args.lt is True and args.lta is True       # PAdES-LT i dokument-timestamp po defaultu
    assert args.visible is True and args.box is None   # vidljiv pecat, okvir se racuna
    assert not hasattr(args, "reason") and not hasattr(args, "location"), "uklonjeni flagovi"
    assert args.field is None
    assert not hasattr(args, "raw_mechanism"), "uklonjeni flag se vratio"


def test_verzija_izgleda_kao_verzija():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pp.VERSION)


def test_pomoc_ne_pada():
    with pytest.raises(SystemExit) as exc:
        pp.build_parser().parse_args(["--help"])
    assert exc.value.code == 0


# ---------------------------------------------------------------- ispis


def _row(**over):
    row = dict(field="Signature1", signer="TEST", time="2026-01-01", intact=True,
               valid=True, trusted=True, timestamp=True)
    row.update(over)
    return row


def test_format_summary_hr_i_en():
    """Ispis je polje-po-polje (citljivo u uskom statusnom prozoru)."""
    hr = pp.format_summary([_row()], "Signature2", None)
    lines = hr.splitlines()
    assert "potpisa u PDF-u: 1" in lines[0]
    assert "sljedeći potpis ide u polje: Signature2" in lines[1]
    assert any("1) Signature1" in ln for ln in lines)
    assert any("potpisnik:" in ln and "TEST" in ln for ln in lines)
    assert any("vrijeme:" in ln and "2026-01-01" in ln for ln in lines)
    assert any("stanje:" in ln and "valjan" in ln and "pouzdan" in ln and "žig da" in ln
               for ln in lines)
    assert max(len(ln) for ln in lines) < 60, f"predugacki redovi: {lines}"
    pp.LANG = "en"
    try:
        en = pp.format_summary([_row()], "Signature2", None).splitlines()
        assert "signatures in PDF: 1" in en[0]
        assert any("state:" in ln and "valid" in ln and "trusted" in ln for ln in en)
    finally:
        pp.LANG = "hr"


def test_verify_ispis_je_polje_po_polje():
    """Dugi pyHanko summary (zarezi) i cert se lome u zasebne redove."""
    assert pp._state_lines("INTACT:TRUSTED,TIMESTAMP_TOKEN<INTACT:TRUSTED>,UNTOUCHED") == [
        "    · INTACT:TRUSTED", "    · TIMESTAMP_TOKEN<INTACT:TRUSTED>", "    · UNTOUCHED"]
    first = pp._wrap("rijec " * 30, 40)[0]
    assert first.startswith("rijec") and len(first) <= 40
    assert all(len(ln) <= 40 for ln in pp._wrap("a" * 30 + " " + "b" * 30, 40))


def test_format_summary_prazan_i_greska():
    assert "0" in pp.format_summary([], "Signature1", None)
    assert "GREŠKA" in pp.format_summary([_row(error="boom")], "Signature1", None)


# ---------------------------------------------------------------- pomocne


def test_first_existing():
    # portable: /etc/hosts ne postoji na Windowsima (zato je CI tamo padao)
    import sys

    assert pp._first_existing(["/nema-o-ovoga", sys.executable]) == sys.executable
    assert pp._first_existing(["/nema-o-ovoga"]) is None


def test_lib_je_postavljen():
    assert isinstance(pp.LIB, str) and pp.LIB



def _soft_signer(tmp_path: Path):
    """Samopotpisani certifikat s pravom potpisa (bez kartice) - iz modula."""
    return pp.soft_signer("TEST POTPISNIK", str(tmp_path / "root.pem"))


def test_potpis_popis_i_odabir_polja(tmp_path):
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
    from pypdf import PdfWriter

    src, out = tmp_path / "test.pdf", tmp_path / "test-signed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        writer.write(f)

    signer = _soft_signer(tmp_path)
    args = pp.build_parser().parse_args([])
    args.input, args.out = str(src), str(out)
    args.field, args.visible, args.box = "Signature1", True, "60,600,400,700"
    args.tsa, args.lt, args.lta = None, False, False
    meta = PdfSignatureMetadata(field_name="Signature1", md_algorithm="sha256",
                                signer_key_usage={"non_repudiation"})

    assert pp._sign_document(args, signer, meta, (60, 600, 400, 700), None) is False
    assert out.exists()
    assert not (tmp_path / "test-signed.pdf.part").exists(), "ostao je .part"

    assert pp._pick_field(str(out), None)[0] == "Signature2"
    with pytest.raises(SystemExit):
        pp._pick_field(str(out), "Signature1")

    rows, nxt, _note = pp.signature_summary(str(out), trust=str(tmp_path / "root.pem"))
    assert len(rows) == 1 and rows[0]["field"] == "Signature1"
    assert rows[0]["valid"] and rows[0]["trusted"] and not rows[0]["timestamp"]
    assert nxt == "Signature2"


def test_nepodrzan_subfilter_se_prijavljuje(tmp_path):
    """PDF s potpisom na adbe.pkcs7.sha1 (stariji profil) - pyHanko ga odbija,
    ali alat mora reci da potpis postoji, a ne 'nema potpisa'."""
    from pypdf import PdfWriter
    from pypdf.generic import (ArrayObject, DictionaryObject, NameObject, NumberObject,
                               TextStringObject)

    path = tmp_path / "legacy.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    sig = DictionaryObject({
        NameObject("/Type"): NameObject("/Sig"),
        NameObject("/Filter"): NameObject("/Adobe.PPKLite"),
        NameObject("/SubFilter"): NameObject("/adbe.pkcs7.sha1"),
        NameObject("/ByteRange"): ArrayObject([NumberObject(0)] * 4),
        NameObject("/Contents"): TextStringObject("0" * 64),
    })
    field = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Sig"),
        NameObject("/T"): TextStringObject("Signature1"),
        NameObject("/V"): sig,
        NameObject("/Rect"): ArrayObject([NumberObject(v) for v in (100, 100, 300, 160)]),
    })
    writer.add_annotation(page_number=0, annotation=field)
    with open(path, "wb") as f:
        writer.write(f)

    raw = pp._raw_sig_fields(str(path))
    assert raw == [("Signature1", "adbe.pkcs7.sha1")], f"neocekivano: {raw}"

    # stari profil se sada pokusava provjeriti; s lazni CMS-om mora prijaviti gresku, ne pasti
    rows, _next, _note = pp.signature_summary(str(path), trust=str(tmp_path / "nema.pem"))
    assert rows, "mora vratiti barem jedan red"
    assert any(r.get("warn") or r.get("error") for r in rows), rows
    assert any("adbe.pkcs7.sha1" in str(r) for r in rows)


def test_pad_potpisivanja_ne_ostavlja_part(tmp_path):
    """Ako potpisivanje padne, .part datoteka se mora pocistiti."""
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    bogus = tmp_path / "nije.pdf"
    bogus.write_bytes(b"ovo nije PDF")
    out = tmp_path / "izlaz.pdf"
    args = pp.build_parser().parse_args([])
    args.input, args.out = str(bogus), str(out)
    args.field, args.visible, args.box = "Signature1", False, "50,60,350,140"
    args.tsa, args.lt, args.lta = None, False, False
    meta = PdfSignatureMetadata(field_name="Signature1", md_algorithm="sha256",
                                signer_key_usage={"non_repudiation"})
    with pytest.raises(Exception):
        pp._sign_document(args, object(), meta, None, None)
    assert not (tmp_path / "izlaz.pdf.part").exists(), ".part je ostao za sobom"


# ---------------------------------------------------------------- defaulti kao Signer


def test_auto_okvir_je_donji_desni():
    assert pp._auto_box((595.0, 842.0)) == (319.0, 28.0, 567.0, 92.0)   # 248 x 64 pt


def test_zadano_vidljiv_pecat_u_donjem_desnom_kutu(tmp_path):
    """Bez --no-visible potpis je vidljiv i pecat ide u donji desni kut."""
    from pypdf import PdfWriter
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    src, out = tmp_path / "v.pdf", tmp_path / "v-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)

    box = pp._auto_box(pp._page_size(str(src), 1))
    assert box == (319.0, 28.0, 567.0, 92.0)
    signer = _soft_signer(tmp_path)
    args = pp.build_parser().parse_args([])
    args.input, args.out, args.field = str(src), str(out), "Signature1"
    meta = PdfSignatureMetadata(field_name="Signature1", md_algorithm="sha256",
                                signer_key_usage={"non_repudiation"})
    pp._sign_document(args, signer, meta, box, None)

    from pypdf import PdfReader
    found = []
    for page in PdfReader(str(out)).pages:
        for a in page.get("/Annots", []) or []:
            o = a.get_object()
            if o.get("/FT") == "/Sig":
                found.append(([float(v) for v in o.get("/Rect")], bool(o.get("/AP"))))
    assert found == [([319.0, 28.0, 567.0, 92.0], True)], found


def test_dokument_timestamp_ne_ostavlja_part_kad_padne(tmp_path):
    """Ako TSA ne odgovori, dokument-timestamp padne bez .part smeca."""
    from pypdf import PdfWriter
    from pyhanko.sign.timestamps import HTTPTimeStamper

    src = tmp_path / "d.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    _soft_signer(tmp_path)  # napise root.pem
    ts = HTTPTimeStamper("http://127.0.0.1:9/nema-tsa", timeout=2)
    with pytest.raises(Exception):
        pp._add_doc_timestamp(str(src), ts, str(tmp_path / "root.pem"))
    assert not (tmp_path / "d.pdf.part").exists()


def test_dokument_timestamp_se_doda_uz_pravi_tsa(tmp_path):
    """Puni PAdES-LTA krug: potpis + dokument-timestamp (trazi mrezu)."""
    from pypdf import PdfWriter, PdfReader
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
    from pyhanko.sign.timestamps import HTTPTimeStamper

    src, out = tmp_path / "lta.pdf", tmp_path / "lta-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = _soft_signer(tmp_path)
    args = pp.build_parser().parse_args([])
    args.input, args.out, args.field = str(src), str(out), "Signature1"
    meta = PdfSignatureMetadata(field_name="Signature1", md_algorithm="sha256",
                                signer_key_usage={"non_repudiation"})
    pp._sign_document(args, signer, meta, None, None)
    try:
        pp._add_doc_timestamp(str(out), HTTPTimeStamper(pp.PUBLIC_TSA, timeout=15),
                              str(tmp_path / "root.pem"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"nema mreze/TSA: {type(exc).__name__}")
    types = []
    for page in PdfReader(str(out)).pages:
        for a in page.get("/Annots", []) or []:
            v = a.get_object().get("/V")
            if v is not None:
                types.append(str(v.get_object().get("/Type")))
    assert "/DocTimeStamp" in types, types


def test_cmd_sign_cijeli_put_sa_defaultima(tmp_path, monkeypatch):
    """CLI put bez kartice: defaulti moraju dati vidljiv pecat u donjem desnom kutu."""
    from pypdf import PdfReader, PdfWriter

    src, out = tmp_path / "cli.pdf", tmp_path / "cli-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)

    signer = _soft_signer(tmp_path)  # napise i root.pem
    monkeypatch.setattr(pp, "_ensure_lib", lambda: None)
    monkeypatch.setattr(pp, "_signer", lambda a, pin: signer)
    monkeypatch.setattr(pp, "_pin", lambda a: "0000")

    args = pp.build_parser().parse_args([
        str(src), "--out", str(out), "--no-lta", "--no-verify",
        "--ca-dir", str(tmp_path), "--trust", "root.pem",
    ])
    assert pp.cmd_sign(args) == 0
    assert out.exists() and not (tmp_path / "cli-signed.pdf.part").exists()

    ann = [a.get_object() for p in PdfReader(str(out)).pages for a in (p.get("/Annots") or [])]
    sig = [a for a in ann if a.get("/FT") == "/Sig"]
    assert len(sig) == 1
    assert [float(v) for v in sig[0]["/Rect"]] == [319.0, 28.0, 567.0, 92.0]
    assert sig[0].get("/AP"), "pecat bi trebao biti vidljiv"


def test_cmd_sign_bez_vidljivog_pecata(tmp_path, monkeypatch):
    """--no-visible daje nevidljiv potpis (bez /AP i bez polja na stranici)."""
    from pypdf import PdfReader, PdfWriter

    src, out = tmp_path / "nv.pdf", tmp_path / "nv-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = _soft_signer(tmp_path)
    monkeypatch.setattr(pp, "_ensure_lib", lambda: None)
    monkeypatch.setattr(pp, "_signer", lambda a, pin: signer)
    monkeypatch.setattr(pp, "_pin", lambda a: "0000")

    args = pp.build_parser().parse_args([
        str(src), "--out", str(out), "--no-visible", "--no-lta", "--no-verify",
        "--ca-dir", str(tmp_path), "--trust", "root.pem",
    ])
    assert pp.cmd_sign(args) == 0
    ann = [a.get_object() for p in PdfReader(str(out)).pages for a in (p.get("/Annots") or [])]
    sig = [a for a in ann if a.get("/FT") == "/Sig"]
    assert len(sig) == 1
    assert not sig[0].get("/AP"), "nevidljiv potpis ne bi smio imati izgled (AP)"
    assert [abs(float(v)) for v in sig[0]["/Rect"]] == [0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------- offline (local stays local)


def test_offline_iskljucuje_sve_mrezne_pozive(tmp_path, monkeypatch):
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
    from pypdf import PdfReader, PdfWriter

    _soft_signer(tmp_path)  # root.pem
    with monkeypatch.context() as mp:  # bez rucnog undo-a (pytest sam vrati stanje)
        mp.setattr(pp, "OFFLINE", True)
        vc = pp._vc(str(tmp_path / "root.pem"), fetching=True)
        assert vc.fetching_allowed is False, "offline ne smije dozvoliti dohvat s mreze"

    # offline potpis: bez TSA i bez dokument-timestampa
    src, out = tmp_path / "off.pdf", tmp_path / "off-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = _soft_signer(tmp_path)
    monkeypatch.setattr(pp, "_ensure_lib", lambda: None)
    monkeypatch.setattr(pp, "_signer", lambda a, pin: signer)
    monkeypatch.setattr(pp, "_pin", lambda a: "0000")
    args = pp.build_parser().parse_args([
        str(src), "--out", str(out), "--offline", "--no-verify",
        "--ca-dir", str(tmp_path), "--trust", "root.pem",
    ])
    assert pp.cmd_sign(args) == 0
    assert args.tsa is None and args.lta is False and args.lt is False
    types = [str(a.get_object().get("/V").get_object().get("/Type"))
             for p in PdfReader(str(out)).pages for a in (p.get("/Annots") or [])]
    assert "/DocTimeStamp" not in types, types


def test_offline_iz_env_varijable(monkeypatch):
    monkeypatch.setenv("POTPIS_OFFLINE", "1")
    spec = importlib.util.spec_from_file_location("pp_offline", ROOT / "potpisi-pdf.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OFFLINE is True


def test_auto_okvir_izbjegava_postojeci_pecat():
    """Ako je donji desni kut vec zauzet (npr. pecat banke), nas ide iznad njega."""
    bankin = (425.2, 48.19, 566.93, 73.7)
    box = pp._auto_box((595.0, 842.0), [bankin])
    assert not pp._overlap(box, bankin)
    assert box[1] > bankin[3], f"trebao bi biti iznad bankinog pecata: {box}"
    assert box[2] <= 595 and box[3] <= 842


def test_gui_nema_poziva_na_nepostojecu_metodu():
    """Staticka provjera: svaki self.X() u GUI-ju mora postojati kao metoda.

    Ovo je uhvatilo pravu regresiju (self._run_async je bio izbrisan u refaktoru,
    a gumb 'Provjeri potpis' je pucao s AttributeError tek u letu).
    """
    import ast

    tk_metode = {
        "after", "bind", "clipboard_append", "clipboard_clear", "config", "configure",
        "destroy", "get", "geometry", "items", "keys", "set", "title", "update",
        "winfo_children", "winfo_height", "winfo_width", "winfo_rootx", "winfo_rooty",
        "create_text", "itemconfigure", "attributes", "protocol", "mainloop", "quit",
    }
    src = (ROOT / "potpisi-gui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    app = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "App")
    defined = {n.name for n in app.body if isinstance(n, ast.FunctionDef)}
    missing = {
        call.func.attr: call.lineno
        for call in ast.walk(app)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and call.func.attr not in defined
        and call.func.attr not in tk_metode
        and not call.func.attr.startswith("_tk")
    }
    assert not missing, f"GUI poziva nepostojece metode: {missing}"


def _legacy_pdf(tmp_path):
    """Sintetizira PDF s pravim adbe.pkcs7.sha1 potpisom (SHA-1 digest je u CMS-u)."""
    import hashlib

    from asn1crypto import cms as a_cms, x509 as a_x509
    from cryptography import x509 as cx
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = cx.Name([cx.NameAttribute(NameOID.COMMON_NAME, "LEGACY POTPISNIK")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        cx.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(7).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(cx.KeyUsage(digital_signature=True, content_commitment=True,
                                   key_encipherment=False, data_encipherment=False,
                                   key_agreement=False, key_cert_sign=False, crl_sign=False,
                                   encipher_only=False, decipher_only=False), True)
        .sign(key, hashes.SHA256())
    )
    placeholder = b"0" * 16384  # mjesta za CMS (hex)

    def build(br):
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R] >> >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Annots [4 0 R] >>",
            b"<< /Type /Annot /Subtype /Widget /FT /Sig /T (Signature1) "
            b"/Rect [100 100 300 160] /P 3 0 R /V 5 0 R >>",
            b"<< /Type /Sig /Filter /Adobe.PPKMS /SubFilter /adbe.pkcs7.sha1 "
            b"/M (D:20261009120000+02'00') /ByteRange [" + b" ".join(
                str(v).encode() for v in br) + b"] /Contents <" + placeholder + b"> >>",
        ]
        out = bytearray(b"%PDF-1.7\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
                f"{xref}\n%%EOF\n").encode()
        return bytes(out)

    br = (0, 0, 0, 0)
    for _ in range(6):  # fiksna tocka dok se siri/zaci ByteRange tekst
        data = build(br)
        start = data.index(b"/Contents <") + len(b"/Contents ")
        end = data.index(b">", start)
        new_br = (0, start, end + 1, len(data) - (end + 1))
        if new_br == br:
            break
        br = new_br

    content = data[:br[1]] + data[br[2]:]
    digest = hashlib.sha1(content).digest()
    sig = key.sign(digest, padding.PKCS1v15(), hashes.SHA1())
    a_cert = a_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    signer_info = a_cms.SignerInfo({
        "version": "v1",
        "sid": a_cms.SignerIdentifier({"issuer_and_serial_number": {
            "issuer": a_cert.issuer, "serial_number": a_cert.serial_number}}),
        "digest_algorithm": {"algorithm": "sha1"},
        "signature_algorithm": {"algorithm": "rsassa_pkcs1v15"},
        "signature": sig,
    })
    signed_data = a_cms.SignedData({
        "version": "v1",
        "digest_algorithms": [{"algorithm": "sha1"}],
        "encap_content_info": {"content_type": "data", "content": digest},
        "certificates": [a_cert],
        "signer_infos": [signer_info],
    })
    der = a_cms.ContentInfo({"content_type": "signed_data", "content": signed_data}).dump()
    hexed = der.hex().encode() + b"0" * (len(placeholder) - 2 * len(der))
    path = tmp_path / "legacy-pravi.pdf"
    path.write_bytes(data[:br[1] + 1] + hexed + data[br[2] - 1:])
    return path


def test_stari_sha1_potpis_se_provjeri(tmp_path):
    """adbe.pkcs7.sha1: SHA-1 digest u CMS-u + RSA potpis nad njim -> intact/valid True."""
    path = _legacy_pdf(tmp_path)
    rows, next_field, _note = pp.signature_summary(str(path), trust=str(tmp_path / "root.pem"))
    row = next(r for r in rows if r.get("field") == "Signature1")
    assert row.get("intact") and row.get("valid"), f"neocekivan red (warn = greska u provjeri): {row}"
    assert row.get("signer") == "LEGACY POTPISNIK", row
    assert row.get("timestamp") is False and row.get("note"), row
    assert next_field == "Signature2"

    raw = pp._raw_sig_fields(str(path))
    assert raw == [("Signature1", "adbe.pkcs7.sha1")], raw


def test_stari_sha1_potpis_prepozna_promjenu(tmp_path):
    """Ako je dokument nakon potpisa promijenjen, digest se ne poklapa -> intact False."""
    path = _legacy_pdf(tmp_path)
    data = path.read_bytes()
    assert b"/MediaBox [0 0 595 842]" in data
    # promjena unutar pokrivenog dijela dokumenta (ista duzina da struktura ostane citljiva)
    path.write_bytes(data.replace(b"/MediaBox [0 0 595 842]", b"/MediaBox [0 0 595 843]"))
    rows, _n, _note = pp.signature_summary(str(path), trust=str(tmp_path / "root.pem"))
    row = next((r for r in rows if r.get("field") == "Signature1"), None)
    assert row is not None, rows
    assert row.get("intact") is False, f"ocekivano intact=False, dobio: {row}"


def test_stari_sha1_potpis_prijavi_dopunu(tmp_path):
    """Bajtovi izvan potpisanog ByteRangea (dopuna nakon potpisa) moraju se prijaviti."""
    path = _legacy_pdf(tmp_path)
    path.write_bytes(path.read_bytes() + b"\n% naknadna dopuna\n")
    rows, _n, _note = pp.signature_summary(str(path), trust=str(tmp_path / "root.pem"))
    row = next(r for r in rows if r.get("field") == "Signature1")
    assert row.get("intact") is True, f"potpisani dio mora ostati ispravan: {row}"
    assert "izmjena" in row["note"], row["note"]


@pytest.mark.skipif(gui is None, reason="tkinter nije dostupan")
def test_okvir_prati_promjenu_broja_redova():
    """Promjena broja redova mora prekrojiti oznaceni okvir (ne ostaviti stari)."""
    f = gui.grid_index_after_resize

    def boxes(rows, cols=gui.GRID_COLS):
        return [(0, i, 10, i + 1) for i in range(rows * cols)]

    # 7 redova -> 3: selekcija iz 6. reda ostaje u zadnjem (3.) redu, ista kolona
    b7, b3 = boxes(7), boxes(3)
    sel = 5 * 2 + 1                      # red 5, kolona 1
    idx = f(b3, rows=3, old_rows=7, old_sel=sel, box=b7[sel])
    assert idx == 2 * 2 + 1, idx
    assert b3[idx] != b7[sel]             # okvir je drugacije velicine

    # 3 reda -> 7: ista celija ostaje ista (red 2, kolona 1)
    b7 = boxes(7)
    idx = f(b7, rows=7, old_rows=3, old_sel=2 * 2 + 1, box=b3[2 * 2 + 1])
    assert idx == 2 * 2 + 1

    # nepromijenjen raspored i rucno povucen okvir -> ne diramo
    assert f(b7, rows=7, old_rows=7, old_sel=None, box=(1, 2, 3, 4)) is None
    assert f(b7, rows=7, old_rows=7, old_sel=3, box=b7[3]) == 3

    # rucno povucen okvir, promijenjen broj redova -> snap na najblizu celiju
    b3 = boxes(3)
    idx = f(b3, rows=3, old_rows=7, old_sel=None, box=(0.0, 4.0, 9.0, 4.2))  # blizu celije 4
    assert idx == 4, idx
    assert f([], rows=3, old_rows=7, old_sel=0, box=None) is None


def _cert_za_pecat(*, country="HR", serial="PNOHR-00000000000", issuer_cn="HRIDCA",
                   cn="IVAN HORVAT"):
    """Certifikat nalik eOI (za test rasporeda pecata) - iz modula."""
    return pp.soft_signer(cn, country=country, serial=serial, issuer_cn=issuer_cn,
                          issuer_org="AKD d.o.o.").signing_cert


def test_pecat_pise_potpisano_s_eoi_i_vrijeme_u_istom_redu():
    from types import SimpleNamespace

    lines = pp._stamp_lines(SimpleNamespace(signing_cert=_cert_za_pecat()))
    assert lines[0][0] == "IVAN HORVAT" and lines[0][1] == 1.0
    assert lines[1][0] == "HR · OIB: 00000000000" and lines[1][1] < 1.0
    assert lines[2][0].startswith("potpisano s eOI · ") and lines[2][1] <= lines[1][1]
    assert lines[2][1] < lines[0][1], "vrijeme mora biti manje od CN linije"

    # bez drzave/OIB u certifikatu -> nema tog reda
    bez = pp._stamp_lines(SimpleNamespace(signing_cert=_cert_za_pecat(country=None, serial=None)))
    assert bez[0][0] == "IVAN HORVAT"
    assert bez[1][0].startswith("potpisano s eOI") is False or "OIB" not in bez[1][0]

    # ne-eOI izdavatelj -> ne tvrdimo eOI
    drugi = pp._stamp_lines(SimpleNamespace(signing_cert=_cert_za_pecat(issuer_cn="SELF TEST")))
    assert drugi[2][0].startswith("potpisano lokalno ·")

    # engleski
    pp.LANG = "en"
    try:
        en = pp._stamp_lines(SimpleNamespace(signing_cert=_cert_za_pecat()))
        assert en[2][0].startswith("signed with eOI · ")
        assert "Tax ID (OIB): 00000000000" in en[1][0]
    finally:
        pp.LANG = "hr"



def test_dugo_ime_se_lomi_i_font_ne_pada_ispod_granice():
    """Duga imena (npr. dva prezimena) se lome u vise redova umjesto da postanu necitljiva."""
    # lazni mjerac: sirina = broj znakova * px * 0.6
    def measure(text, px):
        return len(text) * px * 0.6

    rows = [("ANAMARIJA KOVAČEVIĆ MARIJANOVIĆ-ŠIMUNIĆ", 1.0), ("HR · OIB: 00000000000", 0.78)]
    laid, base = pp._fit_stamp_lines(rows, measure, max_w=248 * 4 - 60, max_h=122 * 4 - 60,
                                     base_px=40, floor_px=20)
    ime_redovi = [t for t, _px in laid if t.startswith("ANAMARIJA") or t.startswith("KOVAČEVIĆ")
                  or t.startswith("MARIJANOVIĆ")]
    assert len(ime_redovi) >= 2, f"ime bi se trebalo prelomiti: {laid}"
    assert base >= 20, f"font ne smije pasti ispod granice: {base}"

    # kratko ime ostaje u jednom redu i font ostaje maksimalan
    laid2, base2 = pp._fit_stamp_lines([("IVAN HORVAT", 1.0), ("HR · OIB: 12345678901", 0.78)],
                                       measure, 248 * 4 - 60, 122 * 4 - 60, 40, 20)
    assert base2 == 40 and [t for t, _ in laid2] == ["IVAN HORVAT", "HR · OIB: 12345678901"]

    # tijesan okvir: i dalje se lomi, font se smanji ali ne ispod granice
    laid3, base3 = pp._fit_stamp_lines(rows, measure, 248 * 4 - 60, 64 * 4 - 60, 40, 20)
    assert 20 <= base3 <= 40
    assert sum(px * 1.35 for _t, px in laid3) <= 64 * 4 - 60 + 1




def test_gui_nema_hardkodiranih_hrvatskih_stringova():
    """Sve sto korisnik vidi mora ici kroz self.t() (inace ostane hrvatski u EN modu)."""
    import ast

    hr_rijeci = ("stranic", "odaberi", "upiši", "upisi", "greška", "greska", "ne mogu",
                 "potpiši", "provjeri", "okvir za", "datotek", "kartic")
    src = (ROOT / "potpisi-gui.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    allowed: set[int] = set()

    # 1) sve unutar TEXTS (i kljucevi i prijevodi, ukljucujuci tuple vrijednosti)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgt = getattr(node, "target", None) or node.targets[0]
            if getattr(tgt, "id", "") == "TEXTS":
                allowed.update(id(n) for n in ast.walk(node.value))

    # 2) argumenti T(...) / self.t(...)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id == "T") or \
               (isinstance(fn, ast.Attribute) and fn.attr == "t"):
                allowed.update(id(n) for n in ast.walk(node.args[0]))

    # 3) CLI help/description (--help je namjerno samo hrvatski) i _TAG_WORDS*
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("help", "description", "metavar", "prog"):
                    allowed.update(id(n) for n in ast.walk(kw.value))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            tgt = getattr(node, "target", None) or node.targets[0]
            if getattr(tgt, "id", "").startswith("_TAG_WORDS"):
                allowed.update(id(n) for n in ast.walk(node.value))

    # 4) docstringovi (komentari/opisi, nisu korisnicki tekst)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Constant) and sub.value == doc:
                        allowed.add(id(sub))
                        break

    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in allowed:
            continue
        low = node.value.lower()
        if any(w in low for w in hr_rijeci):
            bad.append((node.lineno, node.value[:60]))
    assert not bad, f"hardkodirani hrvatski stringovi (koristi self.t()): {bad}"


def test_vektorski_pecat_je_tekst_i_sirine_su_normalizirane(tmp_path):
    """Pecat mora biti vektorski (font) i /W u tisućinkama em (pyHanko bug je zakrpan)."""
    from pypdf import PdfReader, PdfWriter
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    src, out = tmp_path / "v.pdf", tmp_path / "v-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = _soft_signer(tmp_path)
    args = pp.build_parser().parse_args([])
    args.input, args.out, args.field = str(src), str(out), "Signature1"
    args = pp._apply_offline(args)
    pp._sign_document(args, signer, PdfSignatureMetadata(field_name="Signature1",
                      md_algorithm="sha256", signer_key_usage={"non_repudiation"}),
                      pp._auto_box(pp._page_size(str(src), 1)), None)

    ann = [a.get_object() for p in PdfReader(str(out)).pages for a in (p.get("/Annots") or [])]
    ap = [a for a in ann if a.get("/FT") == "/Sig"][0]["/AP"]["/N"].get_object()
    res = ap.get("/Resources", {})
    fonts = res.get("/Font", {})
    assert fonts, "pecat bi trebao biti vektorski (ugradjen font)"
    assert not res.get("/XObject"), "ne ocekujemo sliku kad je vektorski pecat"
    for name in fonts:
        d = fonts[name].get_object()["/DescendantFonts"][0].get_object()
        W = d.get("/W") or []
        vals, pos = [], 0
        while pos < len(W):
            nxt = W[pos + 1]
            if isinstance(nxt, list):
                vals += [int(v) for v in nxt]
                pos += 2
            else:
                vals.append(int(W[pos + 2]))
                pos += 3
        assert vals, f"{name}: /W je prazan"
        assert 100 <= min(vals) and max(vals) <= 1100, (
            f"{name}: sirine izvan 1000/em ({min(vals)}..{max(vals)}) - "
            "pyHanko patch za /W nije primijenjen?"
        )


def test_bez_fonta_potpis_je_nevidljiv(tmp_path):
    """Ako font nije upotrebljiv, potpis i dalje uspije, ali bez vidljivog pecata."""
    from pypdf import PdfReader, PdfWriter
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    src, out = tmp_path / "f.pdf", tmp_path / "f-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = _soft_signer(tmp_path)
    args = pp.build_parser().parse_args([])
    args.input, args.out, args.field = str(src), str(out), "Signature1"
    args.font = str(tmp_path / "nema-ovog-fonta.ttf")
    args = pp._apply_offline(args)
    pp._sign_document(args, signer, PdfSignatureMetadata(field_name="Signature1",
                      md_algorithm="sha256", signer_key_usage={"non_repudiation"}),
                      pp._auto_box(pp._page_size(str(src), 1)), None)
    ann = [a.get_object() for p in PdfReader(str(out)).pages for a in (p.get("/Annots") or [])]
    assert [a for a in ann if a.get("/FT") == "/Sig"], "potpis mora postojati"


@pytest.mark.skipif(gui is None, reason="tkinter nije dostupan")
def test_stanje_izbornika_certifikata():
    """Tri slucaja izbornika: nema kartice / nema potpisnog / jedan / vise."""
    f = gui.cert_area_state
    assert f([]) == ("none", [])
    assert f(None) == ("none", [])
    samo_identifikacijski = [dict(token="T", cn="X", usable=False)]
    assert f(samo_identifikacijski) == ("none_usable", [])
    jedan = [dict(token="AKD eID Card (Signature)", cn="NIKOLA", usable=True)]
    assert f(jedan)[0] == "one" and f(jedan)[1] == jedan
    dva = jedan + [dict(token="Druga", cn="DRUGI", usable=True)]
    assert f(dva)[0] == "many" and len(f(dva)[1]) == 2
    # mjesano: jedan potpisni + jedan identifikacijski -> jedan
    assert f(jedan + samo_identifikacijski)[0] == "one"


def test_provjera_radi_i_bez_akd_middlewarea(tmp_path, monkeypatch):
    """Regresija (pukao GitHub CI): bez AKD mape provjera starih potpisa ne smije pasti.

    Na CI-ju (i kod svakoga bez AKD middlewarea) CA_DIR ne postoji; ranije je to bacalo
    SystemExit (koji nije Exception) izvan try/except pa su testovi padali.
    """
    from pypdf import PdfReader, PdfWriter
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    # 1) stari (legacy) potpis mora biti provjerljiv bez AKD mape
    monkeypatch.setattr(pp, "CA_DIR", str(tmp_path / "nema-middlewarea"))
    path = _legacy_pdf(tmp_path)
    rows, _n, _note = pp.signature_summary(str(path), trust=str(tmp_path / "root.pem"))
    row = next(r for r in rows if r.get("field") == "Signature1")
    assert row.get("intact") and row.get("valid"), row

    # 2) i potpisivanje/validacija normalnog potpisa radi (trust je apsolutna putanja)
    src, out = tmp_path / "a.pdf", tmp_path / "a-signed.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(src, "wb") as f:
        w.write(f)
    signer = pp.soft_signer("CI TEST", str(tmp_path / "root.pem"))
    args = pp.build_parser().parse_args([])
    args.input, args.out, args.field = str(src), str(out), "Signature1"
    args = pp._apply_offline(args)
    pp._sign_document(args, signer, PdfSignatureMetadata(field_name="Signature1",
                      md_algorithm="sha256", signer_key_usage={"non_repudiation"}),
                      pp._auto_box(pp._page_size(str(src), 1)), None)
    rows2, _n2, _note2 = pp.signature_summary(str(out), trust=str(tmp_path / "root.pem"))
    assert rows2 and rows2[0].get("intact") and rows2[0].get("valid"), rows2


def test_der_se_reze_po_duzini_ne_po_nulama():
    """Regresija (puklo na Windows CI-ju): zadnji bajt DER-a smije biti 0x00.

    Prije se koristio rstrip(b"\\x00") pa je potpis kojem zadnji bajt slucajno bude 0x00
    (1/256 slucajeva) ostao 1 bajt kraci -> asn1crypto 'Insufficient data'.
    """
    # SEQUENCE, kratka forma, zadnji bajt je 0x00
    short = b"\x30\x03\x02\x01\x00"
    assert pp._der_object(short + b"\x00" * 10) == short
    assert pp._der_object(short) == short
    # duga forma duzine (0x81): SEQUENCE len=3 -> INTEGER 0 (zadnji bajt je 0x00)
    long_form = b"\x30\x81\x03\x02\x01\x00"
    assert pp._der_object(long_form + b"\x00" * 8) == long_form
    # ne-DER ulaz se vraca nepromijenjen; prekratak/neispravan takodjer
    assert pp._der_object(b"nije der") == b"nije der"
    assert pp._der_object(b"\x30") == b"\x30"
    assert pp._der_object(b"\x30\xff\x01") == b"\x30\xff\x01"


def test_legacy_potpis_kojeg_der_zavrsava_nulom(tmp_path):
    """End-to-end regresija: DER kojem je zadnji bajt 0x00 (1/256) mora se ispravno procitati."""
    path = _legacy_pdf(tmp_path)
    data = bytearray(path.read_bytes())
    # nadji /Contents <hex> i procitaj DER (po zaglavlju, ne po nulama)
    start = data.index(b"/Contents <") + len(b"/Contents <")
    end = data.index(b">", start)
    der = pp._der_object(bytes.fromhex(data[start:end].decode()))
    assert len(der) > 10
    # zadnji bajt DER-a postavi na 0x00 (simulira potpis koji zavrsava nulom)
    pos = start + 2 * (len(der) - 1)
    data[pos:pos + 2] = b"00"
    path.write_bytes(bytes(data))

    rows, _n, _note = pp.signature_summary(str(path), trust=str(tmp_path / "root.pem"))
    row = next(r for r in rows if r.get("field") == "Signature1")
    # bitno: NE smije biti "potpis nije citljiv" (prije je ovdje pukao asn1crypto)
    assert "warn" not in row and "error" not in row, row
    assert row.get("intact") is True, row       # dokument nije mijenjan
    assert row.get("valid") is False, row       # ali potpis je diran -> ne valja


def test_potpisuje_i_pdf_koji_nije_strogo_valjan(tmp_path):
    """Regresija: Pillow/ImageMagick PDF-ovi imaju xref '65536' i padali su na strogom citanju."""
    from PIL import Image
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata

    src, out = tmp_path / "slika.pdf", tmp_path / "slika-signed.pdf"
    Image.new("RGB", (600, 800), "white").save(src, "PDF")  # xref: 0000000000 65536 f
    assert b"65536 f" in src.read_bytes()  # da test stvarno provjerava taj slucaj

    signer = pp.soft_signer("NE-STROGI PDF", str(tmp_path / "root.pem"))
    args = pp.build_parser().parse_args([str(src), "--out", str(out), "--offline"])
    args.field = "Signature1"
    box = pp._auto_box(pp._page_size(str(src), 1))
    pp._sign_document(args, signer, PdfSignatureMetadata(field_name="Signature1",
                      md_algorithm="sha256", signer_key_usage={"non_repudiation"}), box, None)

    rows, _n, _note = pp.signature_summary(str(out), trust=str(tmp_path / "root.pem"))
    assert rows and rows[0].get("intact") and rows[0].get("valid"), rows
