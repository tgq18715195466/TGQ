"""Redis contains disposable rate counters only. Business state never depends on a Redis key."""
from redis import Redis

SCRIPT = '''local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n'''


def rate_limit(url, tenant, user, limit):
    with Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2) as client:
        count = client.eval(SCRIPT, 1, f'rate:{tenant}:{user}', 60)
        return count <= limit
