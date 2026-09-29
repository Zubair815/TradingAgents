"""One read-only MT5 runtime, shared by lifespan and Forex API dependencies."""

import threading

from tradingagents.dataflows.config import get_config
from tradingagents.journal.post_close import ClosedTradeProcessor
from tradingagents.mt5.service import MT5ObservationService


class ForexRuntime:
    def __init__(self, observer, journal_manager, learning_manager):
        self.observer = observer
        self.journal_manager = journal_manager
        self.journal = journal_manager.journal
        self.learning_manager = learning_manager
        self.processor = ClosedTradeProcessor(
            journal=self.journal, history_provider=learning_manager.history_provider,
            learning_mgr=learning_manager,
        )
        self.service = MT5ObservationService(
            observer,
            journal_manager,
            poll_interval_seconds=get_config()["mt5_poll_interval_seconds"],
            post_close_processor=self.processor,
        )
        self._lock = threading.Lock()
        self._closing = False
        self.closed = False
        self._disconnect_pending = False
        self._connect_done = threading.Event()
        self._connect_done.set()

    def start(self):
        # No implicit terminal launch/login during application startup.
        if not self._closing and self.observer.connection.is_connected():
            self.service.start()

    def connect(self, **kwargs):
        with self._lock:
            if self._closing or self._disconnect_pending or not self._connect_done.is_set():
                raise RuntimeError("MT5 runtime is busy or stopping")
            self._connect_done.clear()
        try:
            # A lost connection leaves the existing worker in standby. Join it
            # before reconnecting, then start the same service again.
            connected = bool(self.observer.connection.is_connected())
            if not connected:
                if not self.service.stop(timeout=2):
                    raise RuntimeError("Previous MT5 poll has not stopped")
                connected = self.observer.connection.connect(**kwargs)
            with self._lock:
                if connected and not self._closing and not self._disconnect_pending:
                    self.service.start()
                    return True
                return False
        finally:
            self._connect_done.set()

    def disconnect(self, timeout=2.0, *, close_journal=False):
        """Bounded stop; cleanup waits for native calls before releasing resources."""
        with self._lock:
            self._closing = self._closing or close_journal
            if self.closed:
                return True
            if self._disconnect_pending:
                return False
            self._disconnect_pending = True
        stopped = self.service.stop(timeout)

        def cleanup():
            self._connect_done.wait()
            worker = self.service._worker_thread
            if worker is not None and worker.is_alive():
                worker.join()
            try:
                self.observer.connection.disconnect()
            except Exception as exc:
                self.service.last_error = type(exc).__name__
            finally:
                with self._lock:
                    if self._closing:
                        self.journal.close()
                        self.closed = True
                    self._disconnect_pending = False

        # Native MT5 IPC cannot be forcibly cancelled. A daemon cleanup retains
        # the runtime and journal until any in-flight call returns.
        thread = threading.Thread(target=cleanup, name="MT5Runtime-Cleanup", daemon=True)
        thread.start()
        if stopped:
            thread.join(timeout)
        return stopped and not thread.is_alive()

    def close(self):
        return self.disconnect(close_journal=True)
