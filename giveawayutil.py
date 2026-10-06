"""Pure helpers for giveaways (no Discord imports, easy to test)."""
import random
from typing import Iterable


def pick_winners(entries: Iterable[tuple[int, int]], count: int, rng=random) -> list[int]:
    """Pick up to `count` different winners. Each entry is (user_id, weight); a weight of 3 means 3 tickets.
    Nobody can win twice."""
    pool = {}
    for user_id, weight in entries:
        pool[user_id] = max(1, int(weight))
    winners: list[int] = []
    while pool and len(winners) < count:
        users = list(pool)
        chosen = rng.choices(users, weights=[pool[u] for u in users], k=1)[0]
        winners.append(chosen)
        del pool[chosen]
    return winners
