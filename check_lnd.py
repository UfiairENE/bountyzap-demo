#!/usr/bin/env python
"""Quick script to check LND connection status"""
import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bountyzap.settings')
django.setup()

from django.conf import settings
import grpc
from bot.utils import _get_lnd_credentials, _get_macaroon

print("Checking LND connection...")
print(f"LND_GRPC_HOST: {settings.LND_GRPC_HOST}")
print(f"LND_TLS_CERT: {settings.LND_TLS_CERT}")
print(f"LND_MACAROON: {settings.LND_MACAROON}")
print()

try:
    # Check if files exist
    if not os.path.exists(settings.LND_TLS_CERT):
        print(f"❌ TLS cert not found: {settings.LND_TLS_CERT}")
        sys.exit(1)
    else:
        print(f"✅ TLS cert found: {settings.LND_TLS_CERT}")
    
    if not os.path.exists(settings.LND_MACAROON):
        print(f"❌ Macaroon not found: {settings.LND_MACAROON}")
        sys.exit(1)
    else:
        print(f"✅ Macaroon found: {settings.LND_MACAROON}")
    
    # Try to connect and make a test RPC call
    print("\nAttempting to connect to LND...")
    try:
        from bot.utils import _get_channel
        from lightning_pb2_grpc import LightningStub
        from lightning_pb2 import GetInfoRequest
        
        # Get the channel (this will try to connect)
        channel, lightning_stub, _ = _get_channel()
        
        # Make a test RPC call to verify connection
        print("Making test RPC call (GetInfo)...")
        request = GetInfoRequest()
        macaroon = _get_macaroon()
        metadata = [("macaroon", macaroon)]
        
        response = lightning_stub.GetInfo(request, metadata=metadata, timeout=5)
        print("✅ Successfully connected to LND!")
        print(f"   Node alias: {response.alias}")
        print(f"   Node pubkey: {response.identity_pubkey}")
        print(f"   Network: {response.chains[0].network if response.chains else 'unknown'}")
        
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.UNAVAILABLE:
            print(f"❌ Cannot connect to LND at {settings.LND_GRPC_HOST}")
            print("   Make sure LND is running and listening on the configured address")
        else:
            print(f"❌ LND RPC error: {e.code()} - {e.details()}")
        sys.exit(1)
        
except FileNotFoundError as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Connection error: {e}")
    print("\nTroubleshooting:")
    print("1. Make sure LND is running: lnd --lnddir=~/.lnd")
    print("2. Check if LND is listening on the correct port")
    print("3. Verify your .env file has correct paths")
    sys.exit(1)
