"""Append-only markdown decision log for TradingAgents."""

import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating


class TradingMemoryLog:
    """Append-only markdown log of trading decisions and reflections."""

    # HTML comment: cannot appear in LLM prose output, safe as a hard delimiter
    _SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
    # Precompiled patterns — avoids re-compilation on every load_entries() call
    _DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
    _REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)

    _lock = threading.Lock()

    def __init__(self, config: dict = None):
        cfg = config or {}
        self._log_path = None
        path = cfg.get("memory_log_path")
        if path:
            self._log_path = Path(path).expanduser()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # Optional cap on resolved entries. None disables rotation.
        self._max_entries = cfg.get("memory_log_max_entries")

    def _atomic_write(self, content: str) -> None:
        """Atomically replace the log file with new content, retrying transient Windows file lock errors."""
        legacy_tmp = self._log_path.with_suffix(".tmp")
        try:
            if legacy_tmp.exists():
                legacy_tmp.unlink()
        except Exception:
            pass

        unique_id = uuid.uuid4().hex
        tmp_path = self._log_path.with_name(f"{self._log_path.name}.{unique_id}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        max_attempts = 15
        for attempt in range(max_attempts):
            try:
                tmp_path.replace(self._log_path)
                return
            except PermissionError:
                if attempt == max_attempts - 1:
                    try:
                        if tmp_path.exists():
                            tmp_path.unlink()
                    except Exception:
                        pass
                    raise
                time.sleep(0.02 * (attempt + 1))
            except Exception:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
                raise

    # --- Write path (Phase A) ---

    def store_decision(
        self,
        ticker: str,
        trade_date: str,
        final_trade_decision: str,
    ) -> None:
        """Append pending entry at end of propagate(). No LLM call."""
        if not self._log_path:
            return
        rating = parse_rating(final_trade_decision)
        tag = f"[{trade_date} | {ticker} | {rating} | pending]"
        entry = f"{tag}\n\nDECISION:\n{final_trade_decision}{self._SEPARATOR}"
        with self._lock:
            # Idempotency guard: fast raw-text scan instead of full parse. Any entry
            # for this ticker and date blocks another, pending or settled: a re-run
            # after the outcome landed would otherwise count the same decision twice
            # in past context and in every aggregate over the log.
            if self._log_path.exists():
                raw = self._log_path.read_text(encoding="utf-8")
                for line in raw.splitlines():
                    if line.startswith(f"[{trade_date} | {ticker} |") and line.endswith("]"):
                        return
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(entry)

    # --- Read path (Phase A) ---

    def load_entries(self) -> list[dict]:
        """Parse all entries from log. Returns list of dicts."""
        if not self._log_path:
            return []
        with self._lock:
            if not self._log_path.exists():
                return []
            text = self._log_path.read_text(encoding="utf-8")
        raw_entries = [e.strip() for e in text.split(self._SEPARATOR) if e.strip()]
        entries = []
        for raw in raw_entries:
            parsed = self._parse_entry(raw)
            if parsed:
                entries.append(parsed)
        return entries

    def get_pending_entries(self) -> list[dict]:
        """Return entries with outcome:pending (for Phase B)."""
        return [e for e in self.load_entries() if e.get("pending")]

    def get_past_context(
        self, ticker: str, n_same: int = 5, n_cross: int = 3, as_of: str | None = None
    ) -> str:
        """Return formatted past context string for agent prompt injection.

        When ``as_of`` (yyyy-mm-dd) is given, only lessons whose outcome was
        already known by that date are included — an entry is kept only if it
        stores a resolution date (``resolved:...``) that is on or before
        ``as_of``. This keeps a historical/backtest run from learning from
        outcomes that had not happened yet (#1251). ``as_of=None`` disables the
        filter, so live runs and pre-migration entries are unaffected.
        """
        entries = [e for e in self.load_entries() if not e.get("pending")]
        if as_of is not None:
            entries = [e for e in entries if e.get("resolved") and e["resolved"] <= as_of]
        if not entries:
            return ""

        same, cross = [], []
        for e in reversed(entries):
            if len(same) >= n_same and len(cross) >= n_cross:
                break
            if e["ticker"] == ticker and len(same) < n_same:
                same.append(e)
            elif e["ticker"] != ticker and len(cross) < n_cross:
                cross.append(e)

        if not same and not cross:
            return ""

        parts = []
        if same:
            parts.append(f"Past analyses of {ticker} (most recent first):")
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format_reflection_only(e) for e in cross)
        return "\n\n".join(parts)

    # --- Update path (Phase B) ---

    def update_with_outcome(
        self,
        ticker: str,
        trade_date: str,
        raw_return: float,
        alpha_return: float,
        holding_days: int,
        reflection: str,
        resolution_date: str | None = None,
    ) -> None:
        """Replace pending tag and append REFLECTION section using atomic write.

        Finds the first pending entry matching (trade_date, ticker), updates
        its tag with return figures (and the ``resolution_date`` the outcome
        became known), and appends a REFLECTION section.  Uses a temp-file +
        os.replace() so a crash mid-write never corrupts the log.
        """
        if not self._log_path:
            return

        with self._lock:
            if not self._log_path.exists():
                return

            text = self._log_path.read_text(encoding="utf-8")
            blocks = text.split(self._SEPARATOR)

            pending_prefix = f"[{trade_date} | {ticker} |"
            raw_pct = f"{raw_return:+.1%}"
            alpha_pct = f"{alpha_return:+.1%}"

            updated = False
            new_blocks = []
            for block in blocks:
                stripped = block.strip()
                if not stripped:
                    new_blocks.append(block)
                    continue

                lines = stripped.splitlines()
                tag_line = lines[0].strip()

                if (
                    not updated
                    and tag_line.startswith(pending_prefix)
                    and tag_line.endswith("| pending]")
                ):
                    # Parse rating from the existing pending tag
                    fields = [f.strip() for f in tag_line[1:-1].split("|")]
                    rating = fields[2]
                    new_tag = self._resolved_tag(
                        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
                    )
                    rest = "\n".join(lines[1:])
                    new_blocks.append(
                        f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{reflection}"
                    )
                    updated = True
                else:
                    new_blocks.append(block)

            if not updated:
                return

            new_blocks = self._apply_rotation(new_blocks)
            new_text = self._SEPARATOR.join(new_blocks)
            self._atomic_write(new_text)

    def batch_update_with_outcomes(self, updates: list[dict]) -> None:
        """Apply multiple outcome updates in a single read + atomic write.

        Each element of updates must have keys: ticker, trade_date,
        raw_return, alpha_return, holding_days, reflection.
        """
        if not self._log_path or not updates:
            return

        with self._lock:
            if not self._log_path.exists():
                return

            text = self._log_path.read_text(encoding="utf-8")
            blocks = text.split(self._SEPARATOR)

            # Build lookup keyed by (trade_date, ticker) for O(1) dispatch
            update_map = {(u["trade_date"], u["ticker"]): u for u in updates}

            new_blocks = []
            for block in blocks:
                stripped = block.strip()
                if not stripped:
                    new_blocks.append(block)
                    continue

                lines = stripped.splitlines()
                tag_line = lines[0].strip()

                matched = False
                for (trade_date, ticker), upd in list(update_map.items()):
                    pending_prefix = f"[{trade_date} | {ticker} |"
                    if tag_line.startswith(pending_prefix) and tag_line.endswith("| pending]"):
                        fields = [f.strip() for f in tag_line[1:-1].split("|")]
                        rating = fields[2]
                        raw_pct = f"{upd['raw_return']:+.1%}"
                        alpha_pct = f"{upd['alpha_return']:+.1%}"
                        new_tag = self._resolved_tag(
                            trade_date, ticker, rating, raw_pct, alpha_pct,
                            upd["holding_days"], upd.get("resolution_date"),
                        )
                        rest = "\n".join(lines[1:])
                        new_blocks.append(
                            f"{new_tag}\n\n{rest.lstrip()}\n\nREFLECTION:\n{upd['reflection']}"
                        )
                        del update_map[(trade_date, ticker)]
                        matched = True
                        break

                if not matched:
                    new_blocks.append(block)

            new_blocks = self._apply_rotation(new_blocks)
            new_text = self._SEPARATOR.join(new_blocks)
            self._atomic_write(new_text)

    # --- Helpers ---

    @staticmethod
    def _resolved_tag(
        trade_date, ticker, rating, raw_pct, alpha_pct, holding_days, resolution_date
    ) -> str:
        """Build a resolved entry tag, recording the outcome's known-by date.

        ``resolution_date`` (the date of the last price bar used for the return)
        is the point-in-time cutoff a later run filters on (#1251). Omitted when
        unavailable, keeping the legacy 6-field tag.
        """
        tag = f"[{trade_date} | {ticker} | {rating} | {raw_pct} | {alpha_pct} | {holding_days}d"
        if resolution_date:
            tag += f" | resolved:{resolution_date}"
        return tag + "]"

    def _apply_rotation(self, blocks: list[str]) -> list[str]:
        """Drop oldest resolved blocks when their count exceeds max_entries.

        Pending blocks are always kept (they represent unprocessed work).
        Returns ``blocks`` unchanged when rotation is disabled or under cap.
        """
        if not self._max_entries or self._max_entries <= 0:
            return blocks

        # Tag each block with (kept, is_resolved) by parsing tag-line markers.
        decisions = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                decisions.append((block, False))
                continue
            tag_line = stripped.splitlines()[0].strip()
            is_resolved = (
                tag_line.startswith("[")
                and tag_line.endswith("]")
                and not tag_line.endswith("| pending]")
            )
            decisions.append((block, is_resolved))

        resolved_count = sum(1 for _, r in decisions if r)
        if resolved_count <= self._max_entries:
            return blocks

        to_drop = resolved_count - self._max_entries
        kept: list[str] = []
        for block, is_resolved in decisions:
            if is_resolved and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _parse_entry(self, raw: str) -> dict | None:
        lines = raw.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        if not (tag_line.startswith("[") and tag_line.endswith("]")):
            return None
        fields = [f.strip() for f in tag_line[1:-1].split("|")]
        if len(fields) < 4:
            return None
        # Optional trailing "resolved:YYYY-MM-DD" field records when the outcome
        # became known, for point-in-time filtering (#1251).
        resolved = None
        for f in fields[6:]:
            if f.startswith("resolved:"):
                resolved = f[len("resolved:"):].strip()
        entry = {
            "date": fields[0],
            "ticker": fields[1],
            "rating": fields[2],
            "pending": fields[3] == "pending",
            "raw": fields[3] if fields[3] != "pending" else None,
            "alpha": fields[4] if len(fields) > 4 else None,
            "holding": fields[5] if len(fields) > 5 else None,
            "resolved": resolved,
        }
        body = "\n".join(lines[1:]).strip()
        decision_match = self._DECISION_RE.search(body)
        reflection_match = self._REFLECTION_RE.search(body)
        entry["decision"] = decision_match.group(1).strip() if decision_match else ""
        entry["reflection"] = reflection_match.group(1).strip() if reflection_match else ""
        return entry

    def _format_full(self, e: dict) -> str:
        raw = e["raw"] or "n/a"
        alpha = e["alpha"] or "n/a"
        holding = e["holding"] or "n/a"
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{e['decision']}"]
        if e["reflection"]:
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    def _format_reflection_only(self, e: dict) -> str:
        tag = f"[{e['date']} | {e['ticker']} | {e['rating']} | {e['raw'] or 'n/a'}]"
        if e["reflection"]:
            return f"{tag}\n{e['reflection']}"
        text = e["decision"][:300]
        suffix = "..." if len(e["decision"]) > 300 else ""
        return f"{tag}\n{text}{suffix}"


    # --- SQLite Migration Path ---

    def migrate_to_sqlite(self, sqlite_path: str | Path | None = None) -> int:
        """Migrate existing markdown decision log entries to an indexed SQLite database.

        Provides a forward-compatible transition path from flat-file markdown to
        SQLite with transactions and indexed lookups (#Issue 4).
        Returns the number of migrated entries.
        """
        if not self._log_path or not self._log_path.exists():
            return 0
        if sqlite_path is None:
            sqlite_path = self._log_path.with_suffix(".sqlite3")
        else:
            sqlite_path = Path(sqlite_path)

        entries = self.load_entries()
        if not entries:
            return 0

        conn = sqlite3.connect(str(sqlite_path), timeout=30.0)
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_decisions (
                        entry_id TEXT PRIMARY KEY,
                        ticker TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        rating TEXT,
                        status TEXT NOT NULL,
                        raw_return TEXT,
                        alpha_return TEXT,
                        holding_days INTEGER,
                        resolution_date TEXT,
                        decision TEXT,
                        reflection TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memory_ticker ON memory_decisions(ticker, trade_date)"
                )
                for e in entries:
                    entry_id = f"{e['ticker']}:{e['date']}"
                    status = "pending" if e.get("pending") else "resolved"
                    raw_val = e.get("raw") or e.get("raw_return")
                    alpha_val = e.get("alpha") or e.get("alpha_return")
                    holding_val = e.get("holding") or e.get("holding_days")
                    if isinstance(holding_val, str) and holding_val.endswith("d") and holding_val[:-1].isdigit():
                        holding_val = int(holding_val[:-1])
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO memory_decisions (
                            entry_id, ticker, trade_date, rating, status,
                            raw_return, alpha_return, holding_days,
                            resolution_date, decision, reflection
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            e.get("ticker", ""),
                            e.get("date", ""),
                            e.get("rating", ""),
                            status,
                            str(raw_val) if raw_val is not None else None,
                            str(alpha_val) if alpha_val is not None else None,
                            str(holding_val) if holding_val is not None else None,
                            e.get("resolved"),
                            e.get("decision", ""),
                            e.get("reflection", ""),
                        ),
                    )
            return len(entries)
        finally:
            conn.close()
