# -*- coding: utf-8 -*-

"""
Generate the two service users' key pairs and render the one-time admin SQL.

    .venv/bin/python infra/snowflake/bootstrap.py

Private keys go to ~/.snowflake/citypulse/ (mode 600) and are never printed;
an existing key is reused, so running this twice does not invalidate a user.
The rendered SQL lands in tmp/ (gitignored) because it is account specific;
paste it into a Snowsight worksheet and run it as ACCOUNTADMIN.
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
KEY_DIR = Path.home() / ".snowflake" / "citypulse"
USERS = {"LOADER": "loader", "RO": "ro"}


def private_key_path(name: str) -> Path:
    return KEY_DIR / f"{name}_rsa_key.p8"


def ensure_key(name: str) -> str:
    """Create the key pair if missing; return the public key body for SQL."""
    path = private_key_path(name)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    else:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        # Created with 600 from the start, never world-readable for an instant.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    # Snowflake wants the base64 body only, without the BEGIN/END lines.
    return "".join(line for line in public.splitlines() if "-----" not in line)


def main() -> None:
    sql = (HERE / "bootstrap.sql.template").read_text(encoding="utf-8")
    for placeholder, name in USERS.items():
        sql = sql.replace(f"{{{{{placeholder}_PUBLIC_KEY}}}}", ensure_key(name))
    out = REPO / "tmp" / "snowflake_bootstrap.sql"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    print(f"Private keys: {KEY_DIR} (mode 600, not printed)")
    print(f"Admin SQL:    {out}")


if __name__ == "__main__":
    main()
