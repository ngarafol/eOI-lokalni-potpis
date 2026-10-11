# Third-party components

Ovaj alat **ne redistribuira** AKD-ov middleware ni kartične drajvere - korisnik ih
instalira sam i za njih vrijede AKD-ovi uvjeti.

Python ovisnosti (sve permisivne licence):

| paket | licenca | koristi se za |
|---|---|---|
| [pyHanko](https://github.com/MatthiasValvekens/pyHanko) | MIT | PDF potpisivanje, PAdES, validacija |
| [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) | Apache-2.0 | oblikovanje teksta (vektorski pečat; pyHanko `[opentype]`) |
| [pyhanko-certvalidator](https://github.com/MatthiasValvekens/pyhanko-certvalidator) | MIT | izgradnja i validacija lanca |
| [python-pkcs11](https://github.com/pyauth/python-pkcs11) | MIT | pristup kartici preko PKCS#11 |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3 | kriptografija (softverski self-test) |
| [asn1crypto](https://github.com/wbond/asn1crypto) | MIT | ASN.1/X.509 |
| [Pillow](https://python-pillow.org/) | MIT-CMU | crtanje vidljivog potpisa |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | Apache-2.0 / BSD-3 (PDFium) | preview PDF-a u GUI-ju |
| [pypdf](https://github.com/py-pdf/pypdf) | BSD-3 | citanje Rect-ova potpisa, testovi |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | CA bundle za TSA lanac |
| [tzlocal](https://github.com/regebro/tzlocal) | MIT | lokalno vrijeme u pecatu |

Ikona (`ikona.png`) je generirana u ovom projektu (Pillow) i dijeli licencu projekta.
