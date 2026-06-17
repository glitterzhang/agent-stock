"""
凭证管理 — 支持明文环境变量（云端推荐）和本地加密两种模式

云端模式（Railway）：直接在 Railway Dashboard 设置环境变量，无需加密
本地加密模式：python -m src.secrets encrypt  生成加密文件
"""
import os
import base64
from dotenv import load_dotenv

load_dotenv()


def get_secret(key: str) -> str:
    """统一获取凭证，优先从环境变量读取"""
    val = os.getenv(key)
    if not val:
        raise ValueError(f"缺少必要的环境变量: {key}\n"
                         f"  云端: 在 Railway Dashboard → Variables 中添加\n"
                         f"  本地: 在 .env 文件中添加 {key}=xxx")
    return val


def encrypt_credentials():
    """本地加密凭证，生成 .env.encrypted 文件"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("请先安装: pip install cryptography")
        return

    # 生成或加载密钥
    key_file = ".secret.key"
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            key = f.read()
        print(f"使用已有密钥: {key_file}")
    else:
        key = Fernet.generate_key()
        with open(key_file, "wb") as f:
            f.write(key)
        print(f"✅ 新密钥已保存到 {key_file}（请妥善保管，不要上传到 Git）")

    fernet = Fernet(key)

    # 加密敏感字段
    sensitive_keys = ["ROBINHOOD_EMAIL", "ROBINHOOD_PASSWORD", "ROBINHOOD_MFA_KEY", "FINNHUB_API_KEY"]
    encrypted_lines = []

    for k in sensitive_keys:
        val = os.getenv(k, "")
        if val:
            encrypted = fernet.encrypt(val.encode()).decode()
            encrypted_lines.append(f"{k}={encrypted}")
            print(f"  🔒 {k} 已加密")

    with open(".env.encrypted", "w") as f:
        f.write("\n".join(encrypted_lines))

    print("\n✅ 加密完成，保存到 .env.encrypted")
    print("   运行时设置环境变量: SECRET_KEY_FILE=.secret.key")


def load_encrypted_credentials():
    """加载加密凭证（本地模式）"""
    key_file = os.getenv("SECRET_KEY_FILE", ".secret.key")
    enc_file = ".env.encrypted"

    if not os.path.exists(key_file) or not os.path.exists(enc_file):
        return  # 没有加密文件，跳过

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
        print(f"⚠️  加载加密凭证失败: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "encrypt":
        encrypt_credentials()
    else:
        print("用法: python -m src.secrets encrypt")
