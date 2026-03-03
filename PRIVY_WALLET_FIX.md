# Privy Wallet Address Fix

## Problem
When users tried to withdraw, they got the error:
```
"No wallet address on file — please log in again"
```

Even though the frontend was correctly sending the wallet address during login.

## Root Cause
The backend `/auth/login` endpoint was:
1. ✅ Receiving `walletAddress` from the frontend
2. ❌ **NOT** reading it from the request body
3. ❌ **NOT** persisting it to the user record

The backend was only trying to extract the wallet address from JWT claims (`claims.get("wallet", {}).get("address")`), but Privy doesn't include the wallet address in the JWT by default.

## Solution

### 1. Updated LoginRequest Schema
**File:** `backend/app/schemas/user.py`

```python
class LoginRequest(BaseModel):
    token: str
    walletAddress: str | None = None  # ✅ Added this field
```

### 2. Updated Login Handler
**File:** `backend/app/api/auth.py`

**Before:**
```python
if user is None:
    user = User(
        privy_user_id=privy_user_id,
        wallet_address=claims.get("wallet", {}).get("address"),  # ❌ Only from JWT
        email=claims.get("email", {}).get("address"),
    )
    db.add(user)
    await db.commit()
```

**After:**
```python
# Wallet address from request body (preferred) or JWT claims (fallback)
wallet_address = body.walletAddress or claims.get("wallet", {}).get("address")

if user is None:
    # Create new user
    user = User(
        privy_user_id=privy_user_id,
        wallet_address=wallet_address,  # ✅ From request body
        email=claims.get("email", {}).get("address"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
else:
    # Update existing user's wallet address if provided
    if wallet_address and user.wallet_address != wallet_address:
        user.wallet_address = wallet_address
        await db.commit()
        await db.refresh(user)
```

## Key Changes

1. **Accept `walletAddress` in request body** ✅
2. **Persist wallet address to database** ✅
3. **Update wallet address on subsequent logins** ✅
4. **Fallback to JWT claims if available** ✅

## Flow

### Before Fix:
```
Frontend → POST /auth/login { token, walletAddress }
Backend  → Read only `token`, ignore `walletAddress` ❌
Backend  → Try to get wallet from JWT (doesn't exist) ❌
Backend  → Save user with wallet_address = null ❌
Frontend → POST /wallet/withdraw
Backend  → Check user.wallet_address → null ❌
Backend  → Return error: "No wallet address on file" ❌
```

### After Fix:
```
Frontend → POST /auth/login { token, walletAddress }
Backend  → Read both `token` and `walletAddress` ✅
Backend  → Prefer walletAddress from body ✅
Backend  → Save user with wallet_address = "bePc..." ✅
Frontend → POST /wallet/withdraw
Backend  → Check user.wallet_address → "bePc..." ✅
Backend  → Build & sign transaction ✅
Backend  → Return transaction signature ✅
```

## Testing

### 1. Test Login Persists Wallet
```bash
# Login with wallet address
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "token": "your-privy-jwt-token",
    "walletAddress": "bePcBHDhGDzQhUTcqFQhNXqX2Uiwf4QUAwwBRQBtLFT"
  }'

# Should return user with wallet_address populated:
# {
#   "id": "...",
#   "privy_user_id": "...",
#   "wallet_address": "bePcBHDhGDzQhUTcqFQhNXqX2Uiwf4QUAwwBRQBtLFT",  ✅
#   ...
# }
```

### 2. Test Withdraw Works
```bash
# Withdraw should now work
curl -X POST http://localhost:8000/wallet/withdraw \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-privy-jwt-token" \
  -d '{
    "token": "sol",
    "to_address": "recipient-address",
    "amount": 0.01
  }'

# Should return transaction signature:
# { "tx_signature": "abc123..." }
```

## Database Verification

```sql
-- Check that wallet addresses are being saved
SELECT privy_user_id, wallet_address, created_at
FROM users
WHERE wallet_address IS NOT NULL;
```

## Files Modified

1. `backend/app/schemas/user.py` - Added `walletAddress` to `LoginRequest`
2. `backend/app/api/auth.py` - Updated login handler to read and persist wallet address

## Status

✅ **FIXED** - Wallet addresses are now properly persisted during login
✅ **TESTED** - Backend correctly reads `walletAddress` from request body
✅ **DEPLOYED** - Backend restarted with fixes applied

## Notes

- The wallet address is updated on every login if it changes
- Frontend sends `walletAddress` in camelCase, backend accepts it
- Backend stores as `wallet_address` in snake_case (database convention)
- Fallback to JWT claims for backwards compatibility

The withdraw functionality should now work correctly! 🎉
