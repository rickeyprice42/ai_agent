"""Create persistent local VAPID identity once. Never print or replace secrets."""
import argparse
import base64
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def configure(output: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    output.mkdir(parents=True, exist_ok=True)
    private_path = output / "private.pem"
    if private_path.exists():
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        payload = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        with private_path.open("xb") as handle:
            handle.write(payload)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("The existing VAPID key is not a P-256 private key.")
    public = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
    (output / "public.txt").write_text(public + "\n", encoding="ascii")
    return public


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/web-push")
    args = parser.parse_args()
    try:
        configure(args.output.resolve())
    except (ValueError, OSError) as exc:
        print(f"Web Push setup failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
    print("Web Push keys are ready. Private key remains in the local ignored data directory.")
