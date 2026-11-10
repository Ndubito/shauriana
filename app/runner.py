"""Runs restocks and owner approvals in a background thread, one at a time.

Web requests return immediately; the page polls /runs/latest to watch progress.
Each job opens its own SQLite connection (connections are per thread).
"""
from __future__ import annotations

import logging
import math
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from app import approvals, db, payments, seed
from app.restock import RestockError, run_restock

log = logging.getLogger("runner")


class Busy(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


class RateLimiter:
    """At most `max_runs` restocks in any rolling `window` seconds, across the whole site.

    Protects the LLM key and the sandbox on a public demo URL with no login. In memory,
    so a restart clears it (fine: a restart also wipes the demo database).
    """

    def __init__(self, max_runs: int, window: float = 3600.0, clock=time.monotonic):
        self.max_runs, self.window, self.clock = max_runs, window, clock
        self._starts: deque[float] = deque()
        self._mutex = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._starts and now - self._starts[0] >= self.window:
            self._starts.popleft()

    def check(self) -> None:
        if self.max_runs <= 0:
            return
        with self._mutex:
            now = self.clock()
            self._prune(now)
            if len(self._starts) >= self.max_runs:
                wait = math.ceil((self.window - (now - self._starts[0])) / 60)
                raise RateLimited(f"Demo limit reached ({self.max_runs} restocks per hour). "
                                  f"Try again in about {wait} minute{'s' if wait != 1 else ''}.")

    def record(self) -> None:
        if self.max_runs <= 0:
            return
        with self._mutex:
            self._starts.append(self.clock())


@dataclass
class RunnerState:
    busy: bool = False
    job: str | None = None
    last_error: str | None = None
    log_lines: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, *, db_path: str, rules, seed_data: dict, gateway_factory: Callable, shop_email: str,
                 llm=None, max_rounds: int = 4, synchronous: bool = False, limiter: RateLimiter | None = None):
        self.limiter = limiter or RateLimiter(0)  # 0 = no limit
        self.db_path = db_path
        self.rules = rules
        self.seed_data = seed_data
        self.gateway_factory = gateway_factory
        self.shop_email = shop_email
        self.llm = llm
        self.max_rounds = max_rounds
        self.synchronous = synchronous  # tests run jobs inline
        self.state = RunnerState()
        self._lock = threading.Lock()

    # ---- job plumbing ----
    def _log(self, tag: str, msg: str) -> None:
        line = f"[{tag}] {msg}"
        self.state.log_lines = (self.state.log_lines + [line])[-200:]
        log.info(line)

    def _start(self, name: str, fn: Callable, counts_as_run: bool = False) -> None:
        if counts_as_run:
            self.limiter.check()  # before taking the lock: a refused run never blocks others
        if not self._lock.acquire(blocking=False):
            raise Busy(f"A job is already running ({self.state.job})")
        if counts_as_run:
            self.limiter.record()  # only runs that actually start count
        self.state.busy, self.state.job, self.state.last_error = True, name, None

        def work():
            conn = db.connect(self.db_path)
            gateway = None
            try:
                gateway = self.gateway_factory()
                fn(conn, gateway)
            except Exception as e:  # surfaced on the page; the DB already holds the run's state
                self.state.last_error = f"{type(e).__name__}: {e}"
                self._log("error", self.state.last_error)
                log.debug(traceback.format_exc())
            finally:
                if gateway is not None and hasattr(gateway, "close"):
                    gateway.close()
                conn.close()
                self.state.busy, self.state.job = False, None
                self._lock.release()

        if self.synchronous:
            work()
        else:
            threading.Thread(target=work, name=f"job-{name}", daemon=True).start()

    def _restock(self, scenario: dict) -> Callable:
        def fn(conn, gateway):
            run_restock(conn, scenario, rules=self.rules, gateway=gateway, shop_email=self.shop_email,
                        llm=self.llm, max_rounds=self.max_rounds, log=self._log)
        return fn

    # ---- jobs the API starts ----
    def start_scenario(self, name: str) -> None:
        if name not in self.seed_data.get("scenarios", {}):
            raise KeyError(f"Unknown scenario {name!r}")

        def fn(conn, gateway):
            scenario = seed.apply_scenario(conn, self.seed_data, name)  # reset + overrides, under the lock
            self._restock(scenario)(conn, gateway)

        self.limiter.check()  # don't clear the page's log for a run that will be refused
        self.state.log_lines = []
        self._start(f"scenario {name.upper()}", fn, counts_as_run=True)

    def start_item_restock(self, item: str, supplier_id: str = "sup_1") -> None:
        # The fixed deal is only for scenarios; a manual restock needs the LLM.
        if self.llm is None:
            raise RestockError("Manual restocks need the LLM (LLM_API_KEY); use a scenario button instead")
        self._start(f"restock {item}", self._restock({"item": item, "supplier_id": supplier_id}),
                    counts_as_run=True)

    def approve(self, approval_id: int, approve: bool) -> None:
        """Record the owner's answer and, if approved, pay. Runs as a job so it holds the lock:
        the answer and the payment reservation are never split by another job."""
        conn = db.connect(self.db_path)
        try:  # early, friendly check; decide() re-checks atomically inside the job
            approvals.expire_stale(conn)
            row = conn.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise approvals.ApprovalError(f"No approval {approval_id}")
        if row["status"] != "pending":
            raise approvals.ApprovalError(f"Approval {approval_id} is already {row['status']}")

        def fn(conn, gateway):
            approved = approvals.decide(conn, approval_id, approve)
            if approved is None:
                self._log("owner", f"approval {approval_id} denied: no payment")
                return
            self._log("owner", f"approval {approval_id} approved → paying {approved.deal.id}")
            payments.pay(conn, approved.deal, approved.invoice, gateway, approved.payment_id, log=self._log)

        self._start(f"{'approve' if approve else 'deny'} {approval_id}", fn)

    def reset(self) -> None:
        def fn(conn, gateway):
            seed.reset(conn, self.seed_data)
        self.state.log_lines = []
        self._start("reset", fn)
