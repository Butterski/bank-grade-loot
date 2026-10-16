"""Backend of a small online RPG: shop, player-to-player trades, gem purchases, and the world server.

PostgreSQL through psycopg2. Tables (see migrations/):
  players(id, name, coins)            -- coins: the soft currency
  items(id, kind, owner_id)           -- one row per item instance
  purchases(provider, purchase_id, player_id, gems, verified_at)
  grants(event_id UNIQUE, provider, purchase_id, player_id, gems)
  wallet_gems(player_id, gems)        -- lives in the wallet service, same database for now
"""

import json
import logging
import threading
import time

import psycopg2
import requests

log = logging.getLogger(__name__)
conn = psycopg2.connect(DSN := "postgresql://game@db/game")
conn.autocommit = True  # keeps the request handlers simple; every statement is its own transaction

WALLET_URL = "http://wallet.internal/grant"


def retry(times):
    """Network blips happen; run the handler again rather than failing the request."""

    def wrap(fn):
        def run(*args, **kwargs):
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as error:  # noqa: BLE001
                    log.warning("%s failed (%s), attempt %d", fn.__name__, error, attempt + 1)
            raise RuntimeError(f"{fn.__name__} failed {times} times")

        return run

    return wrap


# ----------------------------------------------------------------------------- shop: buy an item from a player


@retry(3)
def buy_item(buyer_id, seller_id, item_id, price):
    """Buyer pays the seller `price` coins for the item. Returns a dict for the client."""
    with conn.cursor() as cur:
        cur.execute("SELECT owner_id FROM items WHERE id = %s", (item_id,))
        owner = cur.fetchone()
        if owner is None or owner[0] != seller_id:
            return {"ok": False, "reason": "item unavailable"}
        cur.execute("SELECT coins FROM players WHERE id = %s", (buyer_id,))
        coins = cur.fetchone()[0]
        if coins < price:
            return {"ok": False, "reason": "not enough coins"}
        cur.execute("UPDATE items SET owner_id = %s WHERE id = %s", (buyer_id, item_id))
        cur.execute("UPDATE players SET coins = %s WHERE id = %s", (coins - price, buyer_id))
        cur.execute("SELECT coins FROM players WHERE id = %s", (seller_id,))
        seller_coins = cur.fetchone()[0]
        cur.execute("UPDATE players SET coins = %s WHERE id = %s", (seller_coins + price, seller_id))
    return {"ok": True, "item": item_id}


# ----------------------------------------------------------------------------- gems: paid purchases


def complete_purchase(player_id, provider, purchase_id, gems):
    """Called after the store SDK confirmed the payment on the client."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO purchases (provider, purchase_id, player_id, gems, verified_at) VALUES (%s, %s, %s, %s, now())",
            (provider, purchase_id, player_id, gems),
        )
    event = {"event_id": f"E-{provider}-{purchase_id}", "provider": provider, "purchase_id": purchase_id,
             "player_id": player_id, "gems": gems}
    requests.post(WALLET_URL, json=event, timeout=5)  # the wallet grants the gems
    return {"ok": True}


def grant_gems(event):
    """Wallet endpoint. The queue may deliver an event more than once, so we check first."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM grants WHERE event_id = %s", (event["event_id"],))
        if cur.fetchone():
            return {"ok": True, "already": True}
        cur.execute(
            "INSERT INTO grants (event_id, provider, purchase_id, player_id, gems) VALUES (%s, %s, %s, %s, %s)",
            (event["event_id"], event["provider"], event["purchase_id"], event["player_id"], event["gems"]),
        )
        cur.execute("UPDATE wallet_gems SET gems = gems + %s WHERE player_id = %s", (event["gems"], event["player_id"]))
    return {"ok": True}


def resend_unacknowledged(events):
    """Support tool: re-sends events the wallet never acknowledged, under a fresh event id so the queue accepts them."""
    for event in events:
        event = dict(event, event_id=f"{event['event_id']}-resend-{int(time.time())}")
        requests.post(WALLET_URL, json=event, timeout=5)


# ----------------------------------------------------------------------------- recovery


def refund(player_id, amount, reason):
    with conn.cursor() as cur:
        cur.execute("UPDATE players SET coins = coins + %s WHERE id = %s", (amount, player_id))
    log.info("refunded %s to %s: %s", amount, player_id, reason)


def request_trade(buyer_id, seller_id, item_id, price):
    """HTTP handler. Trading can be switched off by ops during incidents."""
    if not settings.trading_enabled:
        refund(buyer_id, price, "trading disabled")  # the client already shows the coins as spent
        return {"ok": False, "reason": "trading is disabled"}
    return buy_item(buyer_id, seller_id, item_id, price)


# ----------------------------------------------------------------------------- the world server


class WorldServer:
    """Keeps the live characters in memory. Each character is saved to its own file."""

    SAVE_EVERY = 120  # seconds

    def __init__(self, save_dir):
        self.save_dir = save_dir
        self.players = {}  # player_id -> {"coins": int, "items": [item ids], "pos": (x, y), "hp": int, "cooldowns": {}}
        threading.Thread(target=self._autosave_loop, daemon=True).start()

    def trade(self, from_id, to_id, item_id, price):
        """In-world trade window. Both players are online, so this is instant."""
        a, b = self.players[from_id], self.players[to_id]
        if item_id not in a["items"] or b["coins"] < price:
            return False
        a["items"].remove(item_id)
        b["items"].append(item_id)
        b["coins"] -= price
        a["coins"] += price
        return True

    def claim_daily(self, player_id):
        p = self.players[player_id]
        if time.time() - p["cooldowns"].get("daily", 0) < 86400:
            return False
        p["cooldowns"]["daily"] = time.time()
        p["coins"] += 50
        return True

    def on_logout(self, player_id):
        self._save(player_id)
        del self.players[player_id]

    def _autosave_loop(self):
        while True:
            time.sleep(self.SAVE_EVERY)
            for player_id in list(self.players):
                self._save(player_id)

    def _save(self, player_id):
        with open(f"{self.save_dir}/{player_id}.json", "w") as f:
            json.dump(self.players[player_id], f)
