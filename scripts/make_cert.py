#!/usr/bin/env python3
"""
Buat sertifikat HTTPS self-signed untuk server lokal - Nuansa Wangi.

Self-signed cocok untuk demo/latihan. Browser akan menampilkan peringatan
"Not secure" sampai sertifikat dipercaya; untuk produksi gunakan sertifikat
dari Let's Encrypt / reverse proxy (lihat README).

Butuh paket `cryptography`:
    pip install cryptography

Contoh:
    python scripts/make_cert.py
    python scripts/make_cert.py --host 192.168.1.10 --days 365 --force
"""
import argparse
import datetime as dt
import ipaddress
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    print(
        "[ERROR] Paket 'cryptography' belum terpasang.\n"
        "        Install dulu: pip install cryptography\n"
        "        atau: pip install -r server/requirements-https.txt",
        file=sys.stderr,
    )
    sys.exit(2)


def build_san(hosts: list[str]) -> x509.SubjectAlternativeName:
    entries = []
    for host in hosts:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            entries.append(x509.DNSName(host))
    return x509.SubjectAlternativeName(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description="Buat sertifikat HTTPS self-signed")
    parser.add_argument("--out-dir", default="certs", help="folder output (default: certs/)")
    parser.add_argument("--days", type=int, default=825, help="masa berlaku hari (default 825)")
    parser.add_argument("--host", action="append", default=None,
                        help="tambahan host/IP untuk SAN (boleh diulang)")
    parser.add_argument("--force", action="store_true", help="timpa sertifikat yang sudah ada")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    cert_path = out_dir / "server.crt"
    key_path = out_dir / "server.key"

    if (cert_path.exists() or key_path.exists()) and not args.force:
        print(f"[!] Sertifikat sudah ada di {out_dir}")
        print("    Pakai --force untuk membuat ulang.")
        return 0

    hosts = ["localhost", "127.0.0.1", "::1"] + list(args.host or [])

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ID"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nuansa Wangi"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=max(1, args.days)))
        .add_extension(build_san(hosts), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("[OK] Sertifikat dibuat:")
    print(f"     cert: {cert_path}")
    print(f"     key : {key_path}")
    print(f"     host: {', '.join(hosts)}")
    print(f"     berlaku sampai: {(now + dt.timedelta(days=args.days)).date()}")
    print()
    print("Jalankan server HTTPS dengan:  .\\scripts\\run_https.ps1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
