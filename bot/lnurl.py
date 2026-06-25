"""
LNURL-pay integration for BountyZap
Allows users to pay bounties using any Lightning wallet via LNURL-pay.
"""
import json
import hashlib
import requests
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import path
from . import views


def generate_lnurl_pay(bounty_id: int, sats: int) -> dict:
    """
    Generate LNURL-pay data for a bounty.
    Returns LNURL JSON that any Lightning wallet can scan.
    """
    base_url = getattr(settings, 'BASE_URL', 'https://bountyzap.example.com')
    
    lnurl_data = {
        "callback": f"{base_url}/lnurl/pay/{bounty_id}",
        "maxSendable": sats * 1000,  # millisatoshis
        "minSendable": sats * 1000,
        "metadata": json.dumps([
            ["text/plain", f"BountyZap: {sats} sats for Bounty #{bounty_id}"],
            ["text/plain", "Pay a bounty on GitHub via Lightning"],
        ]),
        "tag": "payRequest",
    }
    return lnurl_data


def generate_lnurl_withdraw(bounty_id: int, sats: int) -> dict:
    """
    Generate LNURL-withdraw for bounty payout.
    The bounty creator can withdraw funds to their Lightning wallet.
    """
    base_url = getattr(settings, 'BASE_URL', 'https://bountyzap.example.com')
    
    lnurl_data = {
        "callback": f"{base_url}/lnurl/withdraw/{bounty_id}",
        "k1": hashlib.sha256(f"bounty-{bounty_id}".encode()).hexdigest()[:32],
        "defaultWithdrawable": sats * 1000,  # millisatoshis
        "minWithdrawable": sats * 1000,
        "maxWithdrawable": sats * 1000,
        "metadata": json.dumps([
            ["text/plain", f"BountyZap: Withdraw {sats} sats from Bounty #{bounty_id}"],
        ]),
        "tag": "withdrawRequest",
    }
    return lnurl_data


def lnurl_pay_callback(request, bounty_id):
    """
    LNURL-pay callback - generates a Lightning invoice for the bounty.
    """
    try:
        from .models import Bounty
        bounty = Bounty.objects.get(issue_number=bounty_id)
    except Bounty.DoesNotExist:
        return JsonResponse({"status": "ERROR", "reason": "Bounty not found"}, status=404)
    
    # Get amount from query params (millisatoshis)
    amount_msat = request.GET.get("amount")
    if not amount_msat:
        return JsonResponse({"status": "ERROR", "reason": "Missing amount parameter"}, status=400)
    
    amount_msat = int(amount_msat)
    amount_sats = amount_msat // 1000
    
    if amount_sats != bounty.amount:
        return JsonResponse({
            "status": "ERROR", 
            "reason": f"Expected {bounty.amount} sats, got {amount_sats} sats"
        }, status=400)
    
    # Create Lightning invoice using existing LND integration
    try:
        from .utils import create_hold_invoice
        invoice = create_hold_invoice(
            sats=amount_sats,
            memo=f"BountyZap: {amount_sats} sats for Bounty #{bounty_id}"
        )
        
        return JsonResponse({
            "pr": invoice.get("bolt11", ""),
            "routes": [],
        })
    except Exception as e:
        return JsonResponse({
            "status": "ERROR",
            "reason": f"Failed to create invoice: {str(e)}"
        }, status=500)


def lnurl_withdraw_callback(request, bounty_id):
    """
    LNURL-withdraw callback - pays out bounty to contributor's Lightning wallet.
    """
    try:
        from .models import Bounty
        bounty = Bounty.objects.get(issue_number=bounty_id)
    except Bounty.DoesNotExist:
        return JsonResponse({"status": "ERROR", "reason": "Bounty not found"}, status=404)
    
    # Get Lightning invoice from request
    invoice = request.GET.get("pr") or request.POST.get("pr")
    k1 = request.GET.get("k1") or request.POST.get("k1")
    
    if not invoice:
        return JsonResponse({"status": "ERROR", "reason": "Missing payment request"}, status=400)
    
    # Verify k1 matches
    expected_k1 = hashlib.sha256(f"bounty-{bounty_id}".encode()).hexdigest()[:32]
    if k1 != expected_k1:
        return JsonResponse({"status": "ERROR", "reason": "Invalid k1"}, status=400)
    
    # Pay the invoice using LND
    try:
        from .utils import pay_invoice
        result = pay_invoice(invoice)
        
        if result.get("success"):
            bounty.paid = True
            bounty.save()
            return JsonResponse({"status": "OK"})
        else:
            return JsonResponse({
                "status": "ERROR",
                "reason": result.get("error", "Payment failed")
            }, status=500)
    except Exception as e:
        return JsonResponse({
            "status": "ERROR",
            "reason": f"Payment failed: {str(e)}"
        }, status=500)


def lnurl_metadata(request, bounty_id):
    """
    LNURL metadata endpoint.
    """
    try:
        from .models import Bounty
        bounty = Bounty.objects.get(issue_number=bounty_id)
    except Bounty.DoesNotExist:
        return JsonResponse({"status": "ERROR", "reason": "Bounty not found"}, status=404)
    
    lnurl_data = generate_lnurl_pay(bounty_id, bounty.amount)
    return JsonResponse(lnurl_data)


lnurl_patterns = [
    path('lnurl/pay/<int:bounty_id>', lnurl_metadata, name='lnurl_pay'),
    path('lnurl/pay/<int:bounty_id>/callback', lnurl_pay_callback, name='lnurl_pay_callback'),
    path('lnurl/withdraw/<int:bounty_id>/callback', lnurl_withdraw_callback, name='lnurl_withdraw_callback'),
]
