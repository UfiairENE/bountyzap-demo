
## Lightning Wallet Integration

BountyZap now supports paying bounties with any Lightning wallet via LNURL-pay.

### How to pay a bounty:
1. Visit `/lnurl/pay/<bounty_id>` - returns LNURL JSON
2. Scan with any Lightning wallet (Wallet of Satoshi, Alby, Zeus, etc.)
3. Or use the callback URL directly in your wallet

### LNURL Endpoints:
- `GET /lnurl/pay/<bounty_id>` - Get LNURL-pay data
- `GET /lnurl/pay/<bounty_id>/callback?amount=<msat>` - Generate invoice
- `POST /lnurl/withdraw/<bounty_id>/callback` - Withdraw bounty payout

### Supported wallets:
Any wallet that supports LNURL-pay:
- Wallet of Satoshi
- Alby
- Zeus
- BlueWallet
- Phoenix
- Breez
- Muun
