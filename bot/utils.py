import os, hashlib, re, requests, grpc, subprocess, json
from django.conf import settings
from lightning_pb2 import AddHoldInvoiceResp
from lightning_pb2_grpc import LightningStub

# ---------- LND ----------
with open(settings.LND_TLS_CERT, 'rb') as f:
    _cert = f.read()
_creds = grpc.ssl_channel_credentials(_cert)
_channel = grpc.secure_channel(settings.LND_GRPC_HOST, _creds)
_stub = LightningStub(_channel)

def create_hold_invoice(sats: int, memo: str) -> dict:
    preimage = os.urandom(32)
    h = hashlib.sha256(preimage).digest()
    resp: AddHoldInvoiceResp = _stub.AddHoldInvoice(
        {
            "memo": memo,
            "value": sats,
            "hash": h,
            "expiry": 3600,
        },
        metadata=[("macaroon", settings.LND_MACAROON)],
    )
    return {
        "preimage": preimage.hex(),
        "payment_request": resp.payment_request,
        "payment_hash": h.hex(),
    }

def keysend(pubkey: str, amount: int):
    """Uses lncli under the hood – works on Render if you install lncli."""
    cmd = [
        "lncli", "--macaroonpath", "/app/lnd/admin.macaroon",
        "--tlscertpath", "/app/lnd/tls.cert",
        "--rpcserver", settings.LND_GRPC_HOST,
        "keysend", "--dest", pubkey, "--amt", str(amount)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(result.stderr)

# ---------- GitHub ----------
def gh_post(path: str, data: dict):
    url = f"https://api.github.com/repos/{settings.OWNER}/{settings.REPO}{path}"
    return requests.post(
        url, json=data,
        headers={"Authorization": f"token {settings.GITHUB_TOKEN}",
                 "Accept": "application/vnd.github.v3+json"}
    )

def comment(issue: int, body: str):
    gh_post(f"/issues/{issue}/comments", {"body": body})

def get_bio(username: str) -> str:
    r = requests.get(
        f"https://api.github.com/users/{username}",
        headers={"Authorization": f"token {settings.GITHUB_TOKEN}"}
    )
    return r.json().get("bio", "")

def extract_lnurl(bio: str):
    m = re.search(r'⚡️\s*([^\s]+)', bio or "")
    return m.group(1) if m else None

def verify_lnurl(lnurl: str) -> dict:
    url = f"https://{lnurl}" if "@" in lnurl else lnurl
    meta = requests.get(url).json()
    pubkey = meta.get("nodePubkey") or meta.get("pubkey")
    if not pubkey:
        raise ValueError("LNURL missing pubkey")
    return {"pubkey": pubkey}