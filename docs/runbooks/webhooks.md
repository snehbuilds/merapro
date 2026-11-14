# Runbook: Outbound webhooks

Mera can POST its events to your systems: a post published or failed, an approval
requested or granted, an account that needs reconnecting, and so on. Set them up under
**Settings → Webhooks** (Owners and Admins).

## 1. Add an endpoint

1. Enter an **https** URL. Private and local addresses are refused, and redirects are not
   followed.
2. Tick the events you want.
3. Copy the **signing secret** (`whsec_…`). It is shown once. **New secret** replaces it,
   and the old one stops working at once.
4. Press **Send a test**: a `ping` event arrives within a minute. A test is not retried.

## 2. Verify each delivery

Every request carries:

```
Mera-Signature: t=1763049600,v1=5f2c…
Content-Type: application/json
```

`v1` is the hex HMAC-SHA256 of `"<t>.<raw body>"` with your secret. To check it:

1. Read the **raw** body bytes (before any JSON parsing).
2. Compute `HMAC_SHA256(secret, t + "." + body)` and compare it with `v1` in constant
   time.
3. Reject `t` more than five minutes from your clock, to stop replays.

In Python:

```python
import hmac, hashlib, time

def verify(secret: str, header: str, body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    t, sig = parts["t"], parts["v1"]
    if abs(time.time() - int(t)) > 300:
        return False
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, sig)
```

Answer with any 2xx within ten seconds. Do the real work after you reply.

## 3. When deliveries fail

- A non-2xx answer or a timeout is retried after 1 minute, 5 minutes, 30 minutes, 2 hours
  and 6 hours. After that the delivery is marked failed.
- After **20 failed attempts in a row** the endpoint turns off and shows why. Fix the receiver,
  then press **Turn on**.
- The same event can arrive twice after a retry. Use the event `id` to ignore repeats.
