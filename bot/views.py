import json, re, hmac, hashlib
import logging
import traceback
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.db import models
from .models import Bounty

logger = logging.getLogger(__name__)

# Import utils with error handling
try:
    from .utils import create_hold_invoice, comment, get_bio, extract_lnurl, verify_lnurl, keysend
except Exception as e:
    logger.error(f"Failed to import utils: {e}")
    logger.error(traceback.format_exc())
    # Create dummy functions to prevent import errors
    def create_hold_invoice(*args, **kwargs):
        raise Exception(f"Utils import failed: {e}")
    def comment(*args, **kwargs):
        pass
    def get_bio(*args, **kwargs):
        return ""
    def extract_lnurl(*args, **kwargs):
        return None
    def verify_lnurl(*args, **kwargs):
        raise Exception(f"Utils import failed: {e}")
    def keysend(*args, **kwargs):
        raise Exception(f"Utils import failed: {e}")

@csrf_exempt
def github_webhook(request):
    try:
        # Handle form-encoded payload (GitHub sends this way sometimes)
        if request.content_type == 'application/x-www-form-urlencoded':
            payload_str = request.POST.get('payload', '')
            if not payload_str:
                return JsonResponse({"error": "missing payload"}, status=400)
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError as e:
                return JsonResponse({"error": f"invalid json: {str(e)}"}, status=400)
            # For form-encoded, signature is on the entire body
            body_for_signature = request.body
        else:
            # Handle JSON payload
            if not request.body:
                return JsonResponse({"error": "empty body"}, status=400)
            try:
                payload = json.loads(request.body)
            except json.JSONDecodeError as e:
                return JsonResponse({"error": f"invalid json: {str(e)}"}, status=400)
            body_for_signature = request.body
        
        # ---- signature verification ----
        signature = request.headers.get('X-Hub-Signature-256', '')
        webhook_secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', None)
        
        # Only verify signature if secret is properly configured
        if signature and webhook_secret and webhook_secret != 'your-webhook-secret':
            try:
                digest = hmac.new(webhook_secret.encode(), body_for_signature, hashlib.sha256).hexdigest()
                expected_sig = f"sha256={digest}"
                if not hmac.compare_digest(expected_sig, signature):
                    logger.error(f"Signature verification failed. Expected: {expected_sig[:20]}..., Got: {signature[:20]}...")
                    logger.error("Make sure GITHUB_WEBHOOK_SECRET in .env matches the secret configured in GitHub webhook settings.")
                    return JsonResponse({"error": "invalid signature"}, status=401)
                logger.debug("Signature verification passed")
            except Exception as e:
                logger.warning(f"Signature verification error: {e}. Proceeding without verification.")
        elif signature:
            logger.warning("GITHUB_WEBHOOK_SECRET is not set or using default value. Signature verification skipped.")
        else:
            logger.debug("No signature header found. Skipping signature verification.")

        event = request.headers.get('X-GitHub-Event')
        action = payload.get('action', '')

        logger.info(f"Received webhook: event={event}, action={action}")

        # ---------- ISSUE ----------
        if event == 'issues' and action in ('opened', 'labeled'):
            issue = payload['issue']
            if not any(l['name'].lower() == 'bounty' for l in issue['labels']):
                return JsonResponse({"status": "ignored - no bounty label"})

            # More flexible regex to match "bounty: 10 sats" or "Bounty: 10 sats" etc.
            m = re.search(r'bounty\s*:\s*(\d+)\s*sats?', issue['body'] or '', re.I)
            if not m:
                logger.warning(f"Issue #{issue['number']} has bounty label but no amount found in body: {issue.get('body', '')[:100]}")
                return JsonResponse({"status": "no amount found in body. Format: 'bounty: 10 sats'"})

            amount = int(m.group(1))
            logger.info(f"Creating hold invoice for issue #{issue['number']}: {amount} sats")
            
            try:
                inv = create_hold_invoice(amount, f"Bounty #{issue['number']}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to create invoice: {error_msg}")
                logger.error(traceback.format_exc())
                
                # Check if it's an LND connection error
                if "Connection refused" in error_msg or "UNAVAILABLE" in error_msg:
                    error_msg = "LND is not running or not accessible. Please ensure LND is running and accessible at the configured address."
                
                # Post error comment to GitHub issue
                try:
                    comment(issue['number'], f"""
**❌ BountyZap Error**

Failed to create invoice: {error_msg}

Please check:
- LND is running
- LND_GRPC_HOST is correct
- LND_TLS_CERT and LND_MACAROON paths are valid
                    """)
                except:
                    pass  # Don't fail if we can't post comment
                
                return JsonResponse({"error": f"failed to create invoice: {error_msg}"}, status=500)

            Bounty.objects.update_or_create(
                issue_number=issue['number'],
                defaults={"amount": amount, "payment_hash": inv['payment_hash']}
            )

            comment(issue['number'], f"""
**BountyZap – {amount} sats ⚡**

Pay this **hold invoice** to lock the funds:
```
{inv['payment_request']}
```

Funds stay in an HTLC until a PR is merged.
            """)
            return JsonResponse({"status": "invoice posted", "issue": issue['number'], "amount": amount})

        # ---------- PR MERGED ----------
        if event == 'pull_request' and action == 'closed' and payload.get('pull_request', {}).get('merged'):
            pr = payload['pull_request']
            m = re.search(r'fix(es)?\s*#(\d+)', pr['body'] or '', re.I)
            if not m:
                return JsonResponse({"status": "no fixes reference"})

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
                keysend(pubkey, bounty.amount, memo=f"BountyZap #{issue_num}")
                bounty.paid = True
                bounty.contributor = pr['user']['login']
                bounty.lnurl = lnurl
                bounty.save()
            except Exception as e:
                comment(issue_num, f"Keysend failed: {e}")
                return JsonResponse({"status": "keysend error"})

            comment(issue_num, f"""
**PAID ⚡ @{pr['user']['login']} {bounty.amount} sats via Keysend!**

LNURL: `{lnurl}`
Lightning-fast, zero-escrow, zero-race.
            """)
            return JsonResponse({"status": "paid"})

        # Log ignored events for debugging
        logger.info(f"Ignored webhook: event={event}, action={action}")
        if event == 'issue_comment':
            logger.warning("Received issue_comment event. The webhook should trigger on 'issues' events when an issue is created or labeled.")
            logger.warning("Make sure your GitHub webhook is configured to send 'issues' events, not just 'issue_comment' events.")
        
        return JsonResponse({
            "status": "ignored", 
            "event": event, 
            "action": action,
            "message": f"This webhook only processes 'issues' events with actions 'opened' or 'labeled', and 'pull_request' events with action 'closed' and merged=true"
        })
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        logger.error(traceback.format_exc())
        return JsonResponse({"error": f"Internal server error: {str(e)}", "traceback": traceback.format_exc()}, status=500)


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