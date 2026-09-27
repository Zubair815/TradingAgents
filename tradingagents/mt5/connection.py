"""MetaTrader 5 Terminal Connection Manager (Phase 14).

Manages terminal initialization, broker authorization, connection health,
and lifecycle teardown with robust error diagnostics and thread-safety.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from tradingagents.mt5.errors import (
    MT5AuthorizationError,
    MT5ConnectionError,
    MT5NotInstalledError,
    MT5TerminalNotFoundError,
)
from tradingagents.mt5.models import MT5ConnectionStatus

logger = logging.getLogger(__name__)



class MT5ConnectionManager:
    """Manages the connection lifecycle to a MetaTrader 5 terminal."""

    def __init__(
        self,
        path: str | Path | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int = 60000,
        portable: bool = False,
        mt5_api: Any = None,
    ):
        self.path = str(path) if path is not None else None
        self.login = int(login) if login is not None else None
        self.password = password
        self.server = server
        self.timeout = timeout
        self.portable = portable
        self._lock = threading.Lock()
        self.status = MT5ConnectionStatus.DISCONNECTED

        # Resolved MT5 library API
        if mt5_api is not None:
            self._mt5 = mt5_api
        else:
            try:
                import MetaTrader5 as real_mt5
                self._mt5 = real_mt5
            except ImportError:
                self._mt5 = None

    @property
    def api(self) -> Any:
        """Return the underlying MetaTrader 5 API module."""
        if self._mt5 is None:
            raise MT5NotInstalledError()
        return self._mt5

    def connect(
        self,
        path: str | Path | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int | None = None,
        portable: bool | None = None,
    ) -> bool:
        """Initialize connection to the MetaTrader 5 terminal and authorize account.

        Raises:
            MT5NotInstalledError: If MetaTrader5 library is missing.
            MT5TerminalNotFoundError: If custom path to terminal64.exe does not exist.
            MT5AuthorizationError: If account login credentials fail.
            MT5ConnectionError: If terminal IPC initialization fails.
        """
        mt5 = self.api

        eff_path = str(path) if path is not None else self.path
        eff_login = int(login) if login is not None else self.login
        eff_password = password if password is not None else self.password
        eff_server = server if server is not None else self.server
        eff_timeout = timeout if timeout is not None else self.timeout
        eff_portable = portable if portable is not None else self.portable

        if eff_path and not Path(eff_path).exists():
            raise MT5TerminalNotFoundError(f"MetaTrader 5 terminal executable not found at: {eff_path}")

        with self._lock:
            self.status = MT5ConnectionStatus.CONNECTING

            # Build initialization kwargs
            init_kwargs: dict[str, Any] = {"timeout": eff_timeout}
            if eff_path:
                init_kwargs["path"] = eff_path
            if eff_portable:
                init_kwargs["portable"] = True

            logger.info("Initializing MetaTrader 5 connection (timeout=%d ms)...", eff_timeout)
            try:
                init_success = mt5.initialize(**init_kwargs)
            except Exception as exc:
                self.status = MT5ConnectionStatus.FAILED
                raise MT5ConnectionError(f"Exception during MT5 initialization: {exc}") from exc

            if not init_success:
                code, desc = self._get_last_error()
                self.status = MT5ConnectionStatus.FAILED
                raise MT5ConnectionError(f"Failed to initialize MetaTrader 5: {desc}", code=code)

            # If account credentials provided, authorize via mt5.login
            if eff_login is not None:
                login_kwargs: dict[str, Any] = {"login": eff_login}
                if eff_password is not None:
                    login_kwargs["password"] = eff_password
                if eff_server is not None:
                    login_kwargs["server"] = eff_server

                try:
                    authorized = mt5.login(**login_kwargs)
                except Exception as exc:
                    self.status = MT5ConnectionStatus.FAILED
                    with contextlib.suppress(Exception):
                        mt5.shutdown()
                    raise MT5AuthorizationError(f"Exception during MT5 login: {exc}") from exc

                if not authorized:
                    code, desc = self._get_last_error()
                    self.status = MT5ConnectionStatus.FAILED
                    with contextlib.suppress(Exception):
                        mt5.shutdown()
                    raise MT5AuthorizationError(
                        f"Failed to authorize account {eff_login} on server {eff_server}: {desc}",
                        code=code,
                    )


            self.status = MT5ConnectionStatus.CONNECTED
            logger.info("MetaTrader 5 connection established successfully.")
            return True

    def disconnect(self) -> None:
        """Shutdown and terminate the MetaTrader 5 connection."""
        if self._mt5 is None:
            return
        with self._lock:
            try:
                self._mt5.shutdown()
            except Exception as exc:
                logger.warning("Error during MT5 shutdown: %s", exc)
            finally:
                self.status = MT5ConnectionStatus.DISCONNECTED
                logger.info("MetaTrader 5 connection closed.")

    def is_connected(self) -> bool:
        """Verify if the terminal is currently initialized and connected."""
        if self._mt5 is None or self.status != MT5ConnectionStatus.CONNECTED:
            return False
        try:
            term_info = self._mt5.terminal_info()
            if term_info is None:
                return False
            # term_info has .connected attribute in official API
            return bool(getattr(term_info, "connected", True))
        except Exception:
            return False

    def get_terminal_info(self) -> dict[str, Any]:
        """Query terminal build, data paths, and connection status."""
        mt5 = self.api
        info = mt5.terminal_info()
        if info is None:
            code, desc = self._get_last_error()
            raise MT5ConnectionError(f"Failed to retrieve terminal info: {desc}", code=code)
        if hasattr(info, "_asdict"):
            return info._asdict()
        if isinstance(info, dict):
            return dict(info)
        return {attr: getattr(info, attr) for attr in dir(info) if not attr.startswith("_")}

    def get_version(self) -> tuple[int, int, str]:
        """Query the MetaTrader 5 terminal version tuple (version, build, build_date)."""
        mt5 = self.api
        ver = mt5.version()
        if ver is None:
            code, desc = self._get_last_error()
            raise MT5ConnectionError(f"Failed to query MT5 version: {desc}", code=code)
        return tuple(ver)

    def _get_last_error(self) -> tuple[int, str]:
        """Helper to safely fetch (error_code, description) from mt5.last_error()."""
        if self._mt5 is None:
            return 0, "No error"
        try:
            err = self._mt5.last_error()
            if isinstance(err, tuple) and len(err) == 2:
                return err[0], str(err[1])
            return 0, str(err)
        except Exception:
            return 0, "Unknown MT5 error"

    def __enter__(self) -> MT5ConnectionManager:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
