import os
import hashlib
import re
import requests
import grpc
import subprocess
import json
from django.conf import settings

# Import the correct protobuf modules
from invoices_pb2 import AddHoldInvoiceRequest, CancelInvoiceMsg, SettleInvoiceMsg
import invoices_pb2_grpc
from lightning_pb2_grpc import LightningStub

# ---------- LND SETUP ----------
def _get_lnd_credentials():
    """Load TLS cert and create secure channel"""
    with open(settings.LND_TLS_CERT, 'rb') as f:
        cert = f.read()
    return grpc.ssl_channel_credentials(cert)

def _get_macaroon():
    """Read macaroon file as hex string for metadata"""
    with open(settings.LND_MACAROON, 'rb') as f:
        return f.read().hex()

# Create gRPC channel and stubs
_creds = _get_lnd_credentials()
_channel = grpc.secure_channel(settings.LND_GRPC_HOST, _creds)
_lightning_stub = LightningStub(_channel)
_invoices_stub = invoices_pb2_grpc.InvoicesStub(_channel)

# ---------- LIGHTNING FUNCTIONS ----------

def create_hold_invoice(sats: int, memo: str) -> dict:
    """
    Create a hold invoice (HTLC) that holds funds until settled or cancelled.
    Returns preimage, payment_request, and payment_hash.
    """
    # Generate random preimage and its hash
    preimage = os.urandom(32)
    payment_hash = hashlib.sha256(preimage).digest()
    
    # Create hold invoice request
    request = AddHoldInvoiceRequest(
        memo=memo,
        value=sats,
        hash=payment_hash,
        expiry=3600  # 1 hour expiry
    )
    
    # Call LND with macaroon authentication
    metadata = [("macaroon", _get_macaroon())]
    response = _invoices_stub.AddHoldInvoice(request, metadata=metadata)
    
    return {
        "preimage": preimage.hex(),
        "payment_request": response.payment_request,
        "payment_hash": payment_hash.hex(),
    }


def settle_hold_invoice(preimage_hex: str):
    """Settle (release) a hold invoice by revealing the preimage"""
    preimage = bytes.fromhex(preimage_hex)
    request = SettleInvoiceMsg(preimage=preimage)
    metadata = [("macaroon", _get_macaroon())]
    _invoices_stub.SettleInvoice(request, metadata=metadata)


def cancel_hold_invoice(payment_hash_hex: str):
    """Cancel a hold invoice and return funds to payer"""
    payment_hash = bytes.fromhex(payment_hash_hex)
    request = CancelInvoiceMsg(payment_hash=payment_hash)
    metadata = [("macaroon", _get_macaroon())]
    _invoices_stub.CancelInvoice(request, metadata=metadata)


def keysend(pubkey: str, amount: int, memo: str = ""):
    """
    Send payment directly to a node via keysend (spontaneous payment).
    Uses lncli subprocess - works if lncli is installed.
    """
    cmd = [
        "lncli",
        "--macaroonpath", str(settings.LND_MACAROON),
        "--tlscertpath", str(settings.LND_TLS_CERT),
        "--rpcserver", settings.LND_GRPC_HOST,
        "sendpayment",
        "--dest", pubkey,
        "--amt", str(amount),
        "--keysend"
    ]
    
    if memo:
        cmd.extend(["--data", f"7629169={memo}"])  # Custom record for memo
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise Exception(f"Keysend failed: {result.stderr}")
    
    return json.loads(result.stdout) if result.stdout else {}


# ---------- GITHUB FUNCTIONS ----------

def gh_post(path: str, data: dict) -> requests.Response:
    """Make authenticated POST request to GitHub API"""
    url = f"https://api.github.com/repos/{settings.OWNER}/{settings.REPO}{path}"
    headers = {
        "Authorization": f"token {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    return requests.post(url, json=data, headers=headers)


def gh_get(path: str) -> requests.Response:
    """Make authenticated GET request to GitHub API"""
    url = f"https://api.github.com/repos/{settings.OWNER}/{settings.REPO}{path}"
    headers = {
        "Authorization": f"token {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    return requests.get(url, headers=headers)


def comment(issue: int, body: str):
    """Post a comment on a GitHub issue"""
    gh_post(f"/issues/{issue}/comments", {"body": body})


def get_bio(username: str) -> str:
    """Fetch a GitHub user's bio"""
    url = f"https://api.github.com/users/{username}"
    headers = {"Authorization": f"token {settings.GITHUB_TOKEN}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json().get("bio", "")
    return ""


def extract_lnurl(bio: str) -> str:
    """
    Extract LNURL or Lightning address from bio.
    Looks for pattern: ⚡️ <address>
    """
    if not bio:
        return None
    
    # Match lightning emoji followed by LNURL/address
    match = re.search(r'⚡️\s*([^\s]+)', bio)
    return match.group(1) if match else None


def verify_lnurl(lnurl: str) -> dict:
    """
    Verify LNURL and extract node pubkey.
    Supports both LNURL and Lightning addresses (user@domain).
    """
    # Convert Lightning address to LNURL endpoint
    if "@" in lnurl:
        username, domain = lnurl.split("@")
        url = f"https://{domain}/.well-known/lnurlp/{username}"
    else:
        # Assume it's already an LNURL or URL
        url = lnurl if lnurl.startswith("http") else f"https://{lnurl}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        metadata = response.json()
        
        # Extract pubkey (different providers use different keys)
        pubkey = metadata.get("nodePubkey") or metadata.get("pubkey")
        
        if not pubkey:
            raise ValueError("LNURL response missing node pubkey")
        
        return {
            "pubkey": pubkey,
            "metadata": metadata
        }
    
    except requests.RequestException as e:
        raise ValueError(f"Failed to verify LNURL: {str(e)}")


# ---------- WEBHOOK VERIFICATION ----------

def verify_github_signature(payload_body: bytes, signature_header: str) -> bool:
    """
    Verify GitHub webhook signature using HMAC-SHA256.
    Returns True if signature is valid.
    """
    if not signature_header:
        return False
    
    # GitHub sends signature as 'sha256=<hash>'
    expected_signature = signature_header.split('=')[1]
    
    # Calculate HMAC
    secret = settings.GITHUB_WEBHOOK_SECRET.encode()
    computed_hash = hashlib.sha256(payload_body).hexdigest()
    
    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_hash, expected_signature)