from tradingagents.llm_clients.rate_limit import (
    SlidingWindowRateLimiter,
    estimate_chat_tokens,
)


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_limiter_waits_for_token_window_instead_of_failing():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(
        requests_per_minute=30,
        tokens_per_minute=8000,
        window_seconds=60,
        clock=clock,
        sleeper=clock.sleep,
    )
    limiter.acquire(5000)
    limiter.acquire(4000)
    assert clock.sleeps == [60.0]


def test_limiter_reconciles_estimate_with_actual_usage():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(
        requests_per_minute=30,
        tokens_per_minute=8000,
        window_seconds=60,
        clock=clock,
        sleeper=clock.sleep,
    )
    reservation = limiter.acquire(3000)
    limiter.reconcile(reservation, 7000)
    limiter.acquire(2000)
    assert clock.sleeps == [60.0]


def test_estimator_counts_prompt_tools_and_output_reserve():
    estimate = estimate_chat_tokens(
        [{"role": "user", "content": "x" * 400}],
        {"tools": [{"name": "calculator", "description": "y" * 200}]},
        output_reserve=512,
    )
    assert estimate > 650
