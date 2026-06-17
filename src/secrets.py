"""
Credential management — supports plain environment variables (recommended for cloud) and local encryption

Cloud mode (Railway): set environment variables directly in the Railway Dashboard, no encryption needed
Local encryption mode: python -m src.secrets encrypt  generates an encrypted file
"""
import os
import base64
from dotenv import load_dotenv

load_dotenv()


def get_secret(key: str) -> str:
    """Retrieve a credential, preferring environment variables"""
    val = os.getenv(key)
    if not val:
        raise ValueError(f"Missing required environment variable: {key}\n"
                         f"  Cloud: add it in Railway Dashboard → Variables\n"
                         f"  Local: add {key}=xxx to the .env file")
    return val


def encrypt_credentials():
    """Encrypt credentials locally and generate a .env.encrypted file"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("Please install first: pip install cryptography")
        return

    # Generate or load the encryption key
    key_file = ".secret.key"
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            key = f.read()
        print(f"Using existing key: {key_file}")
    else:
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        print(f"✅ New key saved to {key_file} (keep it safe, do not commit to Git)")

    fernet = Fernet(key)

    # Encrypt sensitive fields
    sensitive_keys = ["ROBINHOOD_EMAIL", "ROBINHOOD_PASSWORD", "ROBINHOOD_MFA_KEY", "FINNHUB_API_KEY"]
    encrypted_lines = []

    for k in sensitive_keys:
        val = os.getenv(k, "")
        if val:
            encrypted = fernet.encrypt(val.encode()).decode()
            encrypted_lines.append(f"{k}={encrypted}")
            print(f"  🔒 {k} encrypted")

    with open(".env.encrypted", "w") as f:
        f.write("\n".join(encrypted_lines))

    print("\n✅ Encryption complete, saved to .env.encrypted")
    print("   Set the environment variable at runtime: SECRET_KEY_FILE=.secret.key")


def load_encrypted_credentials():
    """Load encrypted credentials (local mode)"""
    key_file = os.getenv("SECRET_KEY_FILE", ".secret.key")
    enc_file = ".env.encrypted"

    if not os.path.exists(key_file) or not os.path.exists(enc_file):
        return  # no encrypted file, skip

    try:
        from cryptography.fernet import Fernet
        with open(key_file, "rb") as f:
            key = f.read()
        fernet = Fernet(key)

        with open(enc_file, "r") as f:
            for line in f:
                line = line.strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                decrypted = fernet.decrypt(v.encode()).decode()
                os.environ.setdefault(k, decrypted)
    except Exception as e:
        print(f"⚠️  Failed to load encrypted credentials: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "encrypt":
        encrypt_credentials()
    else:
        print("Usage: python -m src.secrets encrypt")
