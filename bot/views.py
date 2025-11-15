import json, re, hmac, hashlib
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Bounty
from .utils import create_hold_invoice, comment, get_bio, extract_lnurl, verify_lnurl, keysend

@csrf_exempt
def github_webhook(request):
    # ---- signature verification ----
    signature = request.headers.get('X-Hub-Signature-256', '')
    digest = hmac.new(settings.WEBHOOK_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={digest}", signature):
        return JsonResponse({"error": "invalid signature"}, status=401)

    event = request.headers.get('X-GitHub-Event')
    payload = json.loads(request.body)

    # ---------- ISSUE ----------
    if event == 'issues' and payload['action'] in ('opened', 'labeled'):
        issue = payload['issue']
        if not any(l['name'].lower() == 'bounty' for l in issue['labels']):
            return JsonResponse({"status": "ignored"})

        m = re.search(r'bounty:\s*(\d+)\s*sats', issue['body'], re.I)
        if not m:
            return JsonResponse({"status": "no amount"})

        amount = int(m.group(1))
        inv = create_hold_invoice(amount, f"Bounty #{issue['number']}")

        Bounty.objects.update_or_create(
            issue_number=issue['number'],
            defaults={"amount": amount, "payment_hash": inv['payment_hash']}
        )

        comment(issue['number'], f"""
**BountyZap – {amount} sats**

Pay this **hold invoice** to lock the funds:
{inv['payment_request']}

Funds stay in an HTLC until a PR is merged.
        """)
        return JsonResponse({"status": "invoice posted"})

    # ---------- PR MERGED ----------
    if event == 'pull_request' and payload['action'] == 'closed' and payload['pull_request']['merged']:
        pr = payload['pull_request']
        m = re.search(r'fix(es)?\s*#(\d+)', pr['body'], re.I)
        if not m:
            return JsonResponse({"status": "no fixes"})

        issue_num = int(m.group(2))
        try:
            bounty = Bounty.objects.get(issue_number=issue_num, paid=False)
        except Bounty.DoesNotExist:
            return JsonResponse({"status": "no active bounty"})

        bio = get_bio(pr['user']['login'])
        lnurl = extract_lnurl(bio)
        if not lnurl:
            comment(issue_num, f"@{pr['user']['login']} Add `⚡️ your@lnurl` to your GitHub bio to receive payment.")
            return JsonResponse({"status": "missing lnurl"})

        try:
            meta = verify_lnurl(lnurl)
            pubkey = meta["pubkey"]
        except Exception as e:
            comment(issue_num, f"Invalid LNURL: {e}")
            return JsonResponse({"status": "invalid lnurl"})

        try:
            keysend(pubkey, bounty.amount)
            bounty.paid = True
            bounty.contributor = pr['user']['login']
            bounty.lnurl = lnurl
            bounty.save()
        except Exception as e:
            comment(issue_num, f"Keysend failed: {e}")
            return JsonResponse({"status": "keysend error"})

        comment(issue_num, f"""
**PAID @{pr['user']['login']} {bounty.amount} sats via Keysend!**

LNURL: `{lnurl}`
Lightning-fast, zero-escrow, zero-race.
        """)
        return JsonResponse({"status": "paid"})

    return JsonResponse({"status": "ignored"})

# ---------- BADGE ----------
def bounty_badge(request):
    total = Bounty.objects.filter(paid=False).aggregate(t=models.Sum('amount'))['t'] or 0
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="130" height="20">
  <rect width="130" height="20" fill="#555"/>
  <rect x="75" width="55" height="20" fill="#4c1"/>
  <text x="8" y="14" font-family="Verdana" font-size="12" fill="#fff">Bounties</text>
  <text x="80" y="14" font-family="Verdana" font-size="12" fill="#fff">{total} sats</text>
</svg>"""
    return HttpResponse(svg, content_type="image/svg+xml")