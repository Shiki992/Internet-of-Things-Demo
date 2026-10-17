"""Generate a self-signed TLS certificate for serving the app on the LAN.

Mobile browsers only expose motion sensors on HTTPS pages, so the phone must
open an https:// URL. This writes backend/certs/cert.pem + key.pem with the
machine's LAN IP (auto-detected) and localhost in the SAN list. backend/run.py
picks the files up automatically.

    python scripts/make_cert.py            # auto-detect LAN IP
    python scripts/make_cert.py 10.0.0.5   # add extra IPs / hostnames

Phones will show a "connection not private" warning once; choose Advanced ->
Proceed. Android Chrome and iOS Safari then treat the page as secure.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = Path(__file__).resolve().parent.parent / "backend" / "certs"


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def main(extra: list[str]) -> None:
    names = ["localhost", *extra]
    ip = lan_ip()
    if ip:
        names.append(ip)

    san: list[x509.GeneralName] = []
    for n in names:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            san.append(x509.DNSName(n))
    san.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip or "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    (CERT_DIR / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (CERT_DIR / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {CERT_DIR}/cert.pem and key.pem for: {', '.join(names)}")


if __name__ == "__main__":
    main(sys.argv[1:])
