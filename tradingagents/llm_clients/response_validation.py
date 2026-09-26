"""Reject incomplete model responses before they become trading evidence."""

from collections.abc import Mapping


class IncompleteResponseError(RuntimeError):
    """A model stopped before producing a usable, complete response."""


def validate_response(response, source: str = "LLM", *, allow_empty: bool = False):
    """Check provider completion metadata, including tool-only responses.

    Keep this check at agent boundaries too: ``bind_tools`` and structured-output
    runnables can bypass a provider wrapper's ``invoke`` method. Never infer
    completion just because the model returned no tool calls.
    """
    for attribute in ("response_metadata", "additional_kwargs"):
        metadata = getattr(response, attribute, None)
        if not isinstance(metadata, Mapping):
            continue
        reason = next((metadata.get(key) for key in
                       ("finish_reason", "stop_reason", "stopReason")
                       if metadata.get(key)), None)
        normalized = str(reason or "").lower().replace("_", "")
        incomplete = metadata.get("incomplete_details")
        status = str(metadata.get("status", "")).lower()
        if normalized in {"length", "maxtokens", "maxoutputtokens", "modelcontextwindowexceeded"} or incomplete or status == "incomplete":
            raise IncompleteResponseError(
                f"{source} returned incomplete output (token or context limit). "
                "Increase max_tokens or reduce the input, then retry the analysis."
            )

    if not allow_empty and not getattr(response, "tool_calls", None):
        content = getattr(response, "content", "")
        if isinstance(content, list):
            content = "\n".join(
                block if isinstance(block, str) else block.get("text", "")
                for block in content
                if isinstance(block, str) or isinstance(block, Mapping) and block.get("type") == "text"
            )
        if isinstance(content, str) and not content.strip():
            raise IncompleteResponseError(
                f"{source} returned no usable text or tool calls. "
                "Check the model and max_tokens setting, then retry the analysis."
            )
    return response
