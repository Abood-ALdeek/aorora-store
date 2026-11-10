"""Run the store on this PC for previewing: python run_local.py"""
import os
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
env_file = os.path.join(HERE, ".env.local")
if not os.path.exists(env_file):
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(f"ADMIN_PASSWORD={secrets.token_urlsafe(12)}\nSECRET_KEY={secrets.token_hex(32)}\n")
for line in open(env_file, encoding="utf-8"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        os.environ.setdefault(k, v)
os.environ.setdefault("DATA_DIR", os.path.join(HERE, "data-local"))

from app import app  # noqa: E402

app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5055")), debug=False)
