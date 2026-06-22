"""Hermes-owned local chat bridge for ChaseOS Agent Bus chat tasks.

This module lives under ``runtime/hermes`` so Studio and workflow code do not
read provider credentials or call provider endpoints directly. The bridge invokes
Hermes' own CLI in non-interactive mode with argv-based subprocess execution
(no shell) and returns a bounded JSON-like packet.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from runtime.hermes.studio_chat_capabilities import try_handle_studio_chat_capability

DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_API_BASE_URL = "http://127.0.0.1:8642"
_MAX_REPLY_CHARS = 6000
_PROPOSAL_PREVIEW_MARKERS = (
    "proposal preview",
    "safe studio chat action envelope",
    "intent_class: `proposal_preview`",
    "blocked from this chat lane",
)


def _safe_text(value: str, *, limit: int = _MAX_REPLY_CHARS) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _build_prompt(message: str, *, scope_boundary: str = "") -> str:
    scope_block = (scope_boundary.strip() + "\n\n") if scope_boundary else ""
    return (
        scope_block +
        "You are Hermes running as a bounded ChaseOS Studio Chat backend bridge. "
        "Reply directly to the operator's chat message in plain text. Keep the reply concise. "
        "For ordinary chat messages, do not return a proposal preview, action envelope, execution ladder, or blocked-effects template. "
        "Only discuss gated previews when the operator explicitly asks for a proposal, approval, shell/run action, send action, promotion, authority change, blockers, or readiness/status. "
        "Do not claim to mutate files, run shell commands, consume approvals, or promote canonical knowledge.\n\n"
        f"Operator message:\n{message}"
    )


def _looks_like_unrequested_proposal(text: str) -> bool:
    lower = str(text or "").lower()
    return any(marker in lower for marker in _PROPOSAL_PREVIEW_MARKERS)


def _build_retry_prompt(message: str) -> str:
    return (
        "You are Hermes running as a bounded ChaseOS Studio Chat backend bridge. "
        "Your previous draft incorrectly returned a proposal preview/action envelope. "
        "This operator message is ordinary chat and a live response test. "
        "Answer with one direct natural sentence only. Do not include markdown headings, proposal previews, action envelopes, execution ladders, or blocked-effects templates. "
        "Do not claim to mutate files, run shell commands, consume approvals, or promote canonical knowledge.\n\n"
        f"Operator message:\n{message}"
    )


def _build_control_plane_test_prompt(message: str) -> str:
    return (
        "You are Hermes. The operator is testing whether the ChaseOS Agent Control Plane can receive a normal live Hermes response. "
        "Respond with exactly one short natural sentence confirming you received the test and can respond normally. "
        "No markdown, no proposal preview, no action envelope, no execution ladder."
    )


def _subprocess_creationflags() -> int:
    """Windows flags for Studio-spawned Hermes bridge calls.

    CREATE_NO_WINDOW alone is not enough for every Windows -> WSL hop: live
    terminal-spam captures showed `wsl.exe ... hermes -z ...` creating a
    transient conhost from the Hermes chat daemon path.  DETACHED_PROCESS keeps
    console-subsystem children from attaching to, or allocating, a visible
    console while preserving argv-based execution and captured stdout/stderr.
    """
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0) or 0
        )
    return 0


def _subprocess_startupinfo() -> Any | None:
    """Hide any fallback process window for Windows subprocess launches."""
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
    startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
    return startupinfo


def _subprocess_no_window_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {"creationflags": _subprocess_creationflags()}
    startupinfo = _subprocess_startupinfo()
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _api_base_url() -> str:
    configured = _first_env(
        "CHASEOS_HERMES_API_BASE_URL",
        "HERMES_API_BASE_URL",
        "API_SERVER_BASE_URL",
    )
    return (configured or DEFAULT_API_BASE_URL).rstrip("/")


def _local_key_files() -> list[Path]:
    """Governed local key-file locations the bridge reads (cross-platform).

    The key is NEVER stored in source or sent to the frontend. The operator
    supplies it once (env var or one of these files, e.g. via set_hermes_api_key).
    """
    candidates: list[Path] = []
    try:
        candidates.append(Path.home() / ".chaseos" / "hermes-api.key")
    except Exception:
        pass
    candidates.append(Path("/tmp/chaseos-hermes-api.key"))
    return candidates


def _key_file_is_safe(path: Path) -> bool:
    """Reject a bearer-key file that is group/other-accessible or not owned by us.

    The `/tmp/chaseos-hermes-api.key` location is in a world-writable, predictable
    directory: any local user could pre-plant or read a key there. On POSIX we
    therefore trust a key file only if it has no group/other permission bits and
    is owned by the current user. On Windows this returns True (NTFS ACLs differ;
    the `/tmp` path does not exist there anyway).
    """
    if os.name == "nt":
        return True
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_mode & 0o077:  # any group/other access bit set
        return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and st.st_uid != geteuid():
        return False
    return True


_WSL_KEY_MEMO: list[str] | None = None  # process memo: [resolved_key] once tried


def _wsl_key_users() -> list[str | None]:
    """WSL users to try, most-specific first. Portable for ChaseOS Core."""
    users: list[str | None] = []
    configured = _first_env("CHASEOS_HERMES_WSL_USER", "HERMES_WSL_USER")
    if configured:
        users.append(configured)
    users.append("chaseos")     # ChaseOS convention (Hermes home owner)
    users.append(None)          # WSL default user
    out: list[str | None] = []
    for u in users:
        if u not in out:
            out.append(u)
    return out


def _read_key_via_wsl() -> str:
    """On Windows, read the key from WSL where Hermes persists it (Core-portable).

    Tries, in order, for each candidate distro/user: the local key file
    (/tmp/chaseos-hermes-api.key, ~/.chaseos/hermes-api.key) then API_SERVER_KEY in
    the Hermes home .env. The value is never logged. Memoized per process.
    """
    global _WSL_KEY_MEMO
    if _WSL_KEY_MEMO is not None:
        return _WSL_KEY_MEMO[0]
    resolved = ""
    if os.name == "nt":
        # One bash command per distro/user that emits the first key it finds.
        script = (
            'for f in /tmp/chaseos-hermes-api.key "$HOME/.chaseos/hermes-api.key"; do '
            '  if [ -s "$f" ]; then cat "$f"; exit 0; fi; done; '
            'e="$HOME/runtimes/hermes-home/.env"; '
            'if [ -f "$e" ]; then '
            '  v=$(grep -m1 -E "^(CHASEOS_HERMES_API_KEY|API_SERVER_KEY)=" "$e" | cut -d= -f2-); '
            '  v=${v%\\"}; v=${v#\\"}; printf "%s" "$v"; fi'
        )
        for distro in _wsl_distro_candidates():
            for user in _wsl_key_users():
                cmd = ["wsl.exe"]
                if distro:
                    cmd += ["-d", distro]
                if user:
                    cmd += ["-u", user]
                cmd += ["-e", "bash", "-lc", script]
                # Only CREATE_NO_WINDOW here — DETACHED_PROCESS breaks stdout capture.
                read_kwargs: dict[str, Any] = {}
                if os.name == "nt":
                    read_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
                try:
                    completed = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=8, **read_kwargs,
                    )
                except Exception:
                    continue
                value = (completed.stdout or "").strip()
                if value:
                    resolved = value
                    break
            if resolved:
                break
    _WSL_KEY_MEMO = [resolved]
    return resolved


def reset_api_key_cache() -> None:
    """Clear the WSL key memo (call after set_hermes_api_key)."""
    global _WSL_KEY_MEMO
    _WSL_KEY_MEMO = None
    # A key/config change can change which transports are reachable; re-probe runs.
    try:
        reset_runs_support_cache()
    except NameError:
        pass


def _api_key() -> str:
    # Resolution order (operator spec + ChaseOS Core portability):
    #   1. CHASEOS_HERMES_API_KEY  2. API_SERVER_KEY  3. HERMES_API_KEY (env)
    #   4. local key files  5. (Windows) WSL key file / Hermes .env.
    key = _first_env("CHASEOS_HERMES_API_KEY", "API_SERVER_KEY", "HERMES_API_KEY")
    if key:
        return key
    # Local-only key file(s) — works directly when Studio runs on the same fs as the
    # key (Linux/WSL). Never log this value.
    for key_file in _local_key_files():
        try:
            if key_file.is_file():
                if not _key_file_is_safe(key_file):
                    # Never log the key; warn about the unsafe file and skip it.
                    print(
                        f"[ChaseOS] Ignoring Hermes key file with unsafe permissions/owner: {key_file}",
                        file=sys.stderr,
                    )
                    continue
                value = key_file.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except Exception:
            continue
    # Windows Studio: Hermes persists the key in WSL; resolve it across the boundary.
    return _read_key_via_wsl()


def set_hermes_api_key(key: str) -> dict[str, Any]:
    """Governed operator action: store the Hermes api_server bearer key locally.

    Writes to ``~/.chaseos/hermes-api.key`` (0600 best-effort). The value is never
    returned, logged, or exposed to the frontend; callers get a boolean + length.
    This is how the operator connects Studio to a key-protected Hermes API server.
    """
    value = str(key or "").strip()
    if not value:
        return {"ok": False, "error": "empty_key", "stored": False}
    target = Path.home() / ".chaseos" / "hermes-api.key"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Create the file 0600 from the start (no world-readable create→chmod
        # window). On Windows the mode arg is largely ignored but harmless.
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
        try:
            os.chmod(target, 0o600)  # belt-and-suspenders if file pre-existed
        except OSError:
            pass
    except OSError as exc:
        return {"ok": False, "error": f"write_failed:{type(exc).__name__}", "stored": False}
    reset_api_key_cache()
    return {"ok": True, "stored": True, "key_length": len(value), "path": str(target),
            "note": "Key stored locally; never exposed to the frontend or logs."}


def _api_transport_enabled() -> bool:
    disabled = _first_env("CHASEOS_HERMES_API_DISABLED", "HERMES_API_DISABLED").lower()
    return disabled not in {"1", "true", "yes", "on"}


def _subprocess_bridge_allowed() -> bool:
    """Return whether Studio Chat may spawn a fresh Hermes CLI process.

    The Discord control-plane/gateway pattern keeps Hermes resident and routes
    messages into that process. Windows Studio Chat must follow that model by
    default: call the persistent Hermes API server instead of creating a one-shot
    ``wsl.exe ... hermes -z ...`` child for every message. The subprocess bridge
    remains as an explicit operator/developer fallback only.
    """

    return _first_env("CHASEOS_HERMES_ALLOW_SUBPROCESS_BRIDGE", "HERMES_ALLOW_SUBPROCESS_BRIDGE").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _call_hermes_api_server(
    prompt: str,
    *,
    session_id: str = "",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Call the persistent Hermes gateway API server without spawning WSL/CLI.

    This mirrors Hermes' Discord control-plane shape: a long-running gateway owns
    the agent/runtime, and external surfaces post a message into that resident
    process. Studio therefore does not create a new WSL process per chat turn.
    """

    if not _api_transport_enabled():
        return {"ok": False, "error": "api_transport_disabled"}

    key = _api_key()
    if not key:
        return {
            "ok": False,
            "error": "api_key_missing",
            "safe_message": (
                "Hermes persistent API transport is not configured. Start the Hermes gateway "
                "with the api_server platform and set API_SERVER_KEY / CHASEOS_HERMES_API_KEY; "
                "Studio Chat will not spawn a one-shot WSL Hermes process by default."
            ),
            "bridge": "hermes_gateway_api",
        }

    body = json.dumps(
        {
            "model": "hermes-agent",
            "stream": False,
            "messages": [
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    request = urllib.request.Request(
        f"{_api_base_url()}/v1/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local configurable endpoint
            payload = json.loads(response.read().decode("utf-8"))
            effective_session_id = response.headers.get("X-Hermes-Session-Id") or session_id
    except urllib.error.HTTPError as exc:
        preview = _safe_text(exc.read().decode("utf-8", errors="replace"), limit=500)
        return {
            "ok": False,
            "error": "api_http_error",
            "safe_message": f"Hermes persistent API transport returned HTTP {exc.code}.",
            "status": exc.code,
            "stderr_preview": preview,
            "bridge": "hermes_gateway_api",
        }
    except TimeoutError:
        return {
            "ok": False,
            "error": "backend_timeout",
            "safe_message": "Hermes persistent API transport timed out before returning a live reply.",
            "bridge": "hermes_gateway_api",
        }
    except Exception as exc:  # noqa: BLE001 - bounded user-facing error
        return {
            "ok": False,
            "error": "api_transport_unavailable",
            "safe_message": (
                "Hermes persistent API transport is unavailable. Start the Hermes gateway "
                "api_server instead of using a one-shot WSL subprocess bridge."
            ),
            "bridge": "hermes_gateway_api",
            "detail": type(exc).__name__,
        }

    choices = payload.get("choices") if isinstance(payload, dict) else None
    content = ""
    if choices and isinstance(choices, list):
        first = choices[0] if choices else {}
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                content = _safe_text(message.get("content") or "")
    if not content:
        return {
            "ok": False,
            "error": "empty_backend_reply",
            "safe_message": "Hermes persistent API transport returned an empty reply.",
            "bridge": "hermes_gateway_api",
        }
    return {
        "ok": True,
        "text": content,
        "runtime": "Hermes",
        "session_id": effective_session_id,
        "provider_detail_redacted": True,
        "bridge": "hermes_gateway_api",
    }


def _api_get(path: str, *, timeout_seconds: int = 5) -> dict[str, Any]:
    """GET a Hermes API server endpoint (loopback, bearer-auth). Never returns the key."""
    if not _api_transport_enabled():
        return {"ok": False, "error": "api_transport_disabled"}
    key = _api_key()
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        f"{_api_base_url()}{path}", headers=headers, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local configurable endpoint
            raw = response.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": _safe_text(raw, limit=500)}
            return {"ok": True, "status": response.status, "payload": payload}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "api_http_error", "status": exc.code}
    except TimeoutError:
        return {"ok": False, "error": "api_timeout"}
    except Exception as exc:  # noqa: BLE001 - bounded
        return {"ok": False, "error": "api_unreachable", "detail": type(exc).__name__}


def hermes_api_health(*, timeout_seconds: int = 5) -> dict[str, Any]:
    """Probe Hermes ``GET /health`` (§3.1 integration ladder, step 1)."""
    result = _api_get("/health", timeout_seconds=timeout_seconds)
    return {
        "endpoint": f"{_api_base_url()}/health",
        "reachable": bool(result.get("ok")),
        "status_code": result.get("status"),
        "error": result.get("error"),
    }


def hermes_api_capabilities(*, timeout_seconds: int = 5) -> dict[str, Any]:
    """Probe Hermes ``GET /v1/capabilities`` (§3.1 integration ladder, step 2).

    The live Hermes gateway nests its capability flags under a ``features`` object
    (``run_submission``, ``run_events_sse``, ``session_resources``,
    ``chat_completions_streaming`` …) and lists routes under ``endpoints``. The
    earlier probe only checked flat top-level keys, so it reported no run/SSE
    support even when the gateway advertised it. We now read ``features`` +
    ``endpoints`` while staying backward-compatible with the flat shape.
    """
    result = _api_get("/v1/capabilities", timeout_seconds=timeout_seconds)
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
    endpoints = payload.get("endpoints") if isinstance(payload.get("endpoints"), dict) else {}

    def _feat(*names: str) -> bool:
        return any(bool(features.get(name)) for name in names)

    def _top(*names: str) -> bool:
        return any(bool(payload.get(name)) for name in names)

    def _has_endpoint(name: str) -> bool:
        return isinstance(endpoints.get(name), dict)

    return {
        "endpoint": f"{_api_base_url()}/v1/capabilities",
        "available": bool(result.get("ok")),
        "status_code": result.get("status"),
        "error": result.get("error"),
        "supports_runs": _feat("run_submission", "run_status") or _top("runs", "supports_runs") or _has_endpoint("runs"),
        "supports_run_events": _feat("run_events_sse") or _top("run_events", "supports_run_events") or _has_endpoint("run_events"),
        "supports_sessions": _feat("session_resources", "session_chat") or _top("sessions", "supports_sessions"),
        "supports_streaming": _feat("run_events_sse", "chat_completions_streaming", "responses_streaming")
        or _top("streaming", "supports_streaming"),
        "endpoints": endpoints,
        "features": features,
    }


_MAX_RUN_EVENTS = 200
_RUNS_SUPPORTED_MEMO: list[bool] | None = None


def _runs_transport_enabled() -> bool:
    """Runs+SSE is the preferred transport unless the operator opts out."""
    return _first_env("CHASEOS_HERMES_DISABLE_RUNS", "HERMES_DISABLE_RUNS").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }


def _runs_supported(*, timeout_seconds: int = 5) -> bool:
    """Whether the live gateway advertises the official runs + run-events SSE API.

    Memoized per process so the capability probe runs at most once per session.
    """
    global _RUNS_SUPPORTED_MEMO
    if _RUNS_SUPPORTED_MEMO is not None:
        return _RUNS_SUPPORTED_MEMO[0]
    caps = hermes_api_capabilities(timeout_seconds=timeout_seconds)
    supported = bool(caps.get("available") and caps.get("supports_runs") and caps.get("supports_run_events"))
    _RUNS_SUPPORTED_MEMO = [supported]
    return supported


def reset_runs_support_cache() -> None:
    """Clear the runs-support memo (call after key/config changes)."""
    global _RUNS_SUPPORTED_MEMO
    _RUNS_SUPPORTED_MEMO = None


def _decode_sse_data(data_text: str) -> dict[str, Any] | None:
    """Decode the accumulated ``data:`` payload of one SSE event into a dict."""
    raw = data_text.strip()
    if not raw or raw == "[DONE]":
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {"event": "raw", "raw": _safe_text(raw, limit=500)}
    return obj if isinstance(obj, dict) else {"event": "raw", "value": obj}


def call_hermes_run_sse(
    message: str = "",
    *,
    prompt: str | None = None,
    session_id: str = "",
    vault_root: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    toolsets: tuple[str, ...] = ("safe",),
) -> dict[str, Any]:
    """Run one bounded chat turn through Hermes' official runs API + SSE stream.

    Ladder steps 3–4: ``POST /v1/runs`` to create the run, then consume
    ``GET /v1/runs/{run_id}/events`` (``text/event-stream``). ``message.delta``
    events are accumulated; ``run.completed`` provides the authoritative output;
    ``run.failed``/``error`` surface a bounded error. Returns a packet shaped like
    the other bridges (``ok``/``text``/``runtime``/``session_id``/``bridge``) plus
    ``run_id``, ``events`` (lifecycle for activity cards), and ``usage``.

    Never exposes the API key. Read-only with ``--toolsets safe``.
    """
    bridge = "hermes_runs_sse"
    if not _api_transport_enabled():
        return {"ok": False, "error": "api_transport_disabled", "bridge": bridge}
    if not _runs_transport_enabled():
        return {"ok": False, "error": "runs_transport_disabled", "bridge": bridge}
    key = _api_key()
    if not key:
        return {
            "ok": False,
            "error": "api_key_missing",
            "safe_message": (
                "Hermes runs API is not configured. Start the Hermes gateway api_server and set "
                "API_SERVER_KEY / CHASEOS_HERMES_API_KEY."
            ),
            "bridge": bridge,
        }

    input_text = prompt if prompt is not None else message
    base_url = _api_base_url()
    body = json.dumps({"input": input_text, "stream": False, "toolsets": list(toolsets)}).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    create_request = urllib.request.Request(f"{base_url}/v1/runs", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(create_request, timeout=timeout_seconds) as response:  # noqa: S310 - local configurable endpoint
            created = json.loads(response.read().decode("utf-8"))
            effective_session_id = response.headers.get("X-Hermes-Session-Id") or session_id
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "error": "run_create_http_error",
            "safe_message": f"Hermes runs API returned HTTP {exc.code} creating the run.",
            "status": exc.code,
            "bridge": bridge,
        }
    except TimeoutError:
        return {"ok": False, "error": "backend_timeout", "safe_message": "Hermes runs API timed out creating the run.", "bridge": bridge}
    except Exception as exc:  # noqa: BLE001 - bounded user-facing error
        return {"ok": False, "error": "run_transport_unavailable", "detail": type(exc).__name__, "bridge": bridge}

    run_id = str((created or {}).get("run_id") or "").strip()
    if not run_id:
        return {"ok": False, "error": "run_not_created", "safe_message": "Hermes runs API did not return a run_id.", "bridge": bridge}

    events_headers = {"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}
    if effective_session_id:
        events_headers["X-Hermes-Session-Id"] = effective_session_id
    events_request = urllib.request.Request(f"{base_url}/v1/runs/{run_id}/events", headers=events_headers, method="GET")

    deltas: list[str] = []
    events: list[dict[str, Any]] = []
    final_output = ""
    usage: dict[str, Any] = {}
    failure: dict[str, Any] | None = None
    try:
        with urllib.request.urlopen(events_request, timeout=timeout_seconds) as stream:  # noqa: S310 - local configurable endpoint
            data_buffer: list[str] = []

            def _flush() -> bool:
                """Process one buffered SSE event. Returns True if the run is finished."""
                nonlocal final_output, usage, failure
                if not data_buffer:
                    return False
                obj = _decode_sse_data("\n".join(data_buffer))
                data_buffer.clear()
                if obj is None:
                    return False
                name = str(obj.get("event") or "").strip()
                if name == "message.delta":
                    piece = obj.get("delta")
                    if isinstance(piece, str):
                        deltas.append(piece)
                    return False
                if len(events) < _MAX_RUN_EVENTS and name not in {"message.delta"}:
                    events.append(obj)
                if name in {"run.completed", "run.succeeded"} or (name.endswith(".completed") and obj.get("output")):
                    out = obj.get("output")
                    if isinstance(out, str):
                        final_output = out
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    return True
                if name in {"run.failed", "run.error", "error", "run.cancelled", "run.stopped"}:
                    failure = obj
                    return True
                return False

            for raw in stream:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if _flush():
                        break
                    continue
                if line.startswith(":"):  # SSE comment / keep-alive (e.g. ": stream closed")
                    continue
                if line.startswith("data:"):
                    data_buffer.append(line[5:].lstrip())
                # event:/id:/retry: lines are not needed — the event name lives in the JSON.
            else:
                _flush()  # process any trailing event if the stream ended without a blank line
    except TimeoutError:
        return {"ok": False, "error": "backend_timeout", "safe_message": "Hermes runs SSE stream timed out.", "run_id": run_id, "bridge": bridge}
    except Exception as exc:  # noqa: BLE001 - bounded
        return {"ok": False, "error": "run_events_unavailable", "detail": type(exc).__name__, "run_id": run_id, "bridge": bridge}

    if failure is not None:
        message_text = failure.get("message") or failure.get("error") or "Hermes run did not complete."
        return {
            "ok": False,
            "error": "run_failed",
            "safe_message": _safe_text(str(message_text), limit=500),
            "run_id": run_id,
            "events": events,
            "bridge": bridge,
        }

    text = _safe_text(final_output or "".join(deltas))
    if not text:
        return {"ok": False, "error": "empty_backend_reply", "safe_message": "Hermes runs API returned an empty reply.", "run_id": run_id, "events": events, "bridge": bridge}
    return {
        "ok": True,
        "text": text,
        "runtime": "Hermes",
        "session_id": effective_session_id,
        "run_id": run_id,
        "events": events,
        "usage": usage,
        "provider_detail_redacted": True,
        "bridge": bridge,
    }


def hermes_delivery_truth(*, probe: bool = True, timeout_seconds: int = 5) -> dict[str, Any]:
    """Consolidated, honest Hermes Studio-bridge delivery truth.

    Lets the UI/drawer show the EXACT backend/config blocker BEFORE a send, per the
    Control-Plane Finalization brief Part 1. Read-only; never exposes the API key.
    `probe=False` skips network calls (config-only verdict).
    """
    transport_enabled = _api_transport_enabled()
    key_present = bool(_api_key())
    base_url = _api_base_url()
    truth: dict[str, Any] = {
        "runtime_id": "hermes",
        "surface": "hermes_delivery_truth",
        "transport": "hermes_gateway_api",
        "api_base_url": base_url,
        "api_transport_enabled": transport_enabled,
        "api_key_configured": key_present,   # boolean only — never the value
        "subprocess_bridge_opt_in": _subprocess_bridge_allowed(),
        "api_server_reachable": None,
        "capabilities_available": None,
        "can_receive_message": True,         # bus/local transcript always accepts
        "can_return_live_reply": False,
        "blocked_reason": None,
    }
    if not transport_enabled:
        truth["blocked_reason"] = "Hermes API transport disabled (CHASEOS_HERMES_API_DISABLED)."
        return truth
    if not key_present:
        truth["blocked_reason"] = (
            "Hermes API key not configured. Start the Hermes gateway with the api_server "
            "platform and set API_SERVER_KEY / CHASEOS_HERMES_API_KEY (or write "
            "/tmp/chaseos-hermes-api.key). Studio never spawns a one-shot WSL Hermes by default."
        )
        return truth
    if not probe:
        truth["can_return_live_reply"] = True  # config looks complete; not network-verified
        truth["blocked_reason"] = None
        return truth
    health = hermes_api_health(timeout_seconds=timeout_seconds)
    truth["api_server_reachable"] = health["reachable"]
    if not health["reachable"]:
        truth["blocked_reason"] = (
            f"Hermes API server not reachable at {base_url}/health "
            f"({health.get('error') or health.get('status_code')})."
        )
        return truth
    caps = hermes_api_capabilities(timeout_seconds=timeout_seconds)
    truth["capabilities_available"] = caps["available"]
    truth["capabilities"] = {
        k: caps[k] for k in ("supports_runs", "supports_run_events", "supports_sessions", "supports_streaming")
    }
    truth["can_return_live_reply"] = True
    truth["blocked_reason"] = None
    return truth


def _is_windows() -> bool:
    """Whether Studio is running on a Windows host (so the WSL Hermes bridge is used).

    A function seam (not an inline ``os.name`` check) so tests can exercise either host
    path deterministically without monkeypatching ``os.name`` — which would also switch
    ``pathlib.Path`` to the wrong flavour and break path resolution.
    """
    return os.name == "nt"


def _windows_path_to_wsl(path: Path, *, distro: str | None = "Ubuntu") -> str | None:
    """Translate a Windows path for WSL without spawning wsl.exe.

    The previous preflight used ``wsl.exe -- wslpath`` from the Windows Studio
    backend. Live terminal-spam captures showed those preflight probes can create
    short-lived conhost windows, so use ChaseOS' known WSL mount convention
    directly instead.
    """
    if os.name != "nt":
        return str(path)
    raw = str(path).replace("\\", "/")
    if len(raw) >= 3 and raw[1:3] == ":/" and raw[0].isalpha():
        return f"/mnt/{raw[0].lower()}/{raw[3:]}"
    if raw.startswith("//"):
        # UNC paths are intentionally not bridged; use the shell fallback so the
        # backend reports a bounded WSL error rather than guessing a mount path.
        return None
    return raw


def _wsl_home(*, distro: str | None = "Ubuntu") -> str | None:
    """Return the WSL user's home directory without spawning wsl.exe."""
    if os.name != "nt":
        return str(Path.home())
    configured = _first_env("CHASEOS_HERMES_WSL_HOME", "HERMES_WSL_HOME")
    if configured:
        return configured
    user = _first_env("CHASEOS_HERMES_WSL_USER", "HERMES_WSL_USER", "USERNAME", "USER")
    if user:
        return f"/home/{user}"
    return None


def _wsl_distro_candidates() -> list[str | None]:
    configured = _first_env("CHASEOS_HERMES_WSL_DISTRO", "HERMES_WSL_DISTRO")
    result: list[str | None] = []
    if configured:
        result.append(configured)
    result.append("Ubuntu")
    result.append(None)
    deduped: list[str | None] = []
    for item in result:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _wsl_user_args() -> list[str]:
    user = _first_env("CHASEOS_HERMES_WSL_USER", "HERMES_WSL_USER")
    return ["-u", user] if user else []


def _wsl_bridge_shell_script() -> str:
    """Bounded WSL shim.

    The operator message is passed as argv after the script name, not interpolated
    into the shell script. The shell is used only to resolve $HOME, PATH, and a
    configurable Hermes binary path inside WSL.
    """

    return (
        'export PATH="$HOME/.local/bin:$HOME/bin:$HOME/.cargo/bin:'
        '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"; '
        ': "${HERMES_HOME:=$HOME/runtimes/hermes-home}"; '
        'export HERMES_HOME; '
        'exec "${CHASEOS_HERMES_WSL_CLI:-hermes}" "$@"'
    )


def _wsl_bridge_env_args() -> list[str]:
    args = [
        "HERMES_QUIET=1",
        "HERMES_REDACT_SECRETS=1",
    ]
    configured_home = _first_env("CHASEOS_HERMES_HOME", "HERMES_HOME")
    if configured_home:
        args.append(f"HERMES_HOME={configured_home}")
    configured_cli = _first_env("CHASEOS_HERMES_WSL_CLI", "HERMES_WSL_CLI")
    if configured_cli:
        args.append(f"CHASEOS_HERMES_WSL_CLI={configured_cli}")
    return args


def _windows_wsl_bridge_command(prompt: str, *, distro: str | None = "Ubuntu") -> list[str]:
    distro_args = ["-d", distro] if distro else []
    return [
        "wsl.exe",
        *distro_args,
        *_wsl_user_args(),
        "--",
        "env",
        *_wsl_bridge_env_args(),
        "sh",
        "-lc",
        _wsl_bridge_shell_script(),
        "hermes-bridge",
        "-z",
        prompt,
        "--toolsets",
        "safe",
        "--ignore-rules",
    ]


def _hermes_command_for_host(root: Path, prompt: str) -> tuple[list[str], str, str]:
    """Return (argv, cwd, bridge_label) for the local Hermes owner runtime.

    Studio runs on Windows while this ChaseOS install keeps Hermes configured in
    WSL. A plain ``hermes`` argv works from WSL but fails from the Windows
    pywebview daemon. The Windows path therefore explicitly bridges into WSL
    when no Windows Hermes executable is available. This keeps Studio Chat on
    the Agent Bus/runtime-daemon path and avoids direct provider calls.
    """
    base_args = ["-z", prompt, "--toolsets", "safe", "--ignore-rules"]
    configured_cli = _first_env("CHASEOS_HERMES_CLI", "HERMES_CLI", "HERMES_BIN")
    if configured_cli:
        return [configured_cli, *base_args], str(root), "hermes_configured_cli_z"

    # On the Windows-hosted Studio daemon for this ChaseOS install, a Windows
    # `hermes` shim can exist but fail to produce a usable nested CLI reply. The
    # live Hermes owner runtime is WSL-backed, so prefer the explicit WSL bridge
    # on Windows unless the operator has set CHASEOS_HERMES_CLI/HERMES_CLI above.
    if not _is_windows():
        hermes_path = shutil.which("hermes")
        if hermes_path:
            return [hermes_path, *base_args], str(root), "hermes_cli_z"

    if _is_windows():
        fallback_command: tuple[list[str], str, str] | None = None
        for distro in _wsl_distro_candidates():
            wsl_cwd = _windows_path_to_wsl(root, distro=distro)
            wsl_home = _wsl_home(distro=distro)
            if wsl_cwd and wsl_home:
                wsl_path = ":".join([
                    f"{wsl_home}/.local/bin",
                    f"{wsl_home}/bin",
                    f"{wsl_home}/.cargo/bin",
                    "/usr/local/sbin",
                    "/usr/local/bin",
                    "/usr/sbin",
                    "/usr/bin",
                    "/sbin",
                    "/bin",
                ])
                distro_args = ["-d", distro] if distro else []
                hermes_home = f"{wsl_home}/runtimes/hermes-home"
                # Preserve the existing direct-argv route when WSL preflight
                # succeeds. It avoids invoking a Linux shell at all.
                return [
                    "wsl.exe",
                    *distro_args,
                    *_wsl_user_args(),
                    "--",
                    "env",
                    f"HERMES_HOME={hermes_home}",
                    "HERMES_QUIET=1",
                    "HERMES_REDACT_SECRETS=1",
                    f"PATH={wsl_path}",
                    _first_env("CHASEOS_HERMES_WSL_CLI", "HERMES_WSL_CLI") or "hermes",
                    *base_args,
                ], str(root), "hermes_wsl_cli_z"

            if fallback_command is None:
                # If WSL process/path preflight is denied, still return a bounded
                # WSL command so the bridge can surface the real WSL error instead
                # of incorrectly reporting a missing Windows Hermes executable.
                fallback_command = (
                    _windows_wsl_bridge_command(prompt, distro=distro),
                    str(root),
                    "hermes_wsl_shell_bridge_z",
                )
        if fallback_command is not None:
            return fallback_command

    return ["hermes", *base_args], str(root), "hermes_cli_z"


def call_hermes_chat_bridge(
    message: str,
    *,
    session_id: str = "",
    vault_root: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    scope_boundary: str = "",
) -> dict[str, Any]:
    """Return one Hermes gateway-backed chat reply packet.

    Studio Chat uses the persistent Hermes API server by default so it follows
    the same long-running runtime pattern as Discord control-plane messages.
    A one-shot CLI/WSL subprocess bridge exists only behind an explicit
    developer/operator opt-in environment flag.
    """
    root = Path(vault_root).resolve() if vault_root is not None else Path.cwd()
    capability_result = try_handle_studio_chat_capability(
        message,
        session_id=session_id,
        vault_root=root,
    )
    if capability_result is not None:
        return capability_result

    prompt = _build_prompt(message, scope_boundary=scope_boundary)

    # Preferred transport: the official runs API + SSE event stream (ladder steps 3–4),
    # used automatically when the live gateway advertises run + run-events support. It
    # yields the same bounded reply plus a run lifecycle (events/usage) for activity
    # cards. If runs is unsupported or the call fails, fall through to the proven
    # /v1/chat/completions transport so live reply never regresses.
    if _api_transport_enabled() and _runs_transport_enabled() and _api_key() and _runs_supported(timeout_seconds=5):
        run_result = call_hermes_run_sse(
            message,
            prompt=prompt,
            session_id=session_id,
            vault_root=root,
            timeout_seconds=timeout_seconds,
        )
        if run_result.get("ok"):
            return run_result

    api_result = _call_hermes_api_server(
        prompt,
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    if api_result.get("ok") or not _subprocess_bridge_allowed():
        return api_result

    env = {}
    for key in ("HOME", "USER", "USERNAME", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    host_path = os.environ.get("PATH") or ""
    home = env.get("HOME") or str(Path.home())
    path_parts = [
        f"{home}/.local/bin",
        f"{home}/bin",
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
    ]
    if host_path:
        path_parts.append(host_path)
    env["PATH"] = ":".join(dict.fromkeys([p for p in path_parts if p]))
    env["HERMES_HOME"] = os.environ.get("HERMES_HOME") or f"{home}/runtimes/hermes-home"
    # The bridge spawns a fresh Hermes CLI run. Use a minimal environment instead
    # of inheriting the long-running gateway/Discord session identity from the
    # daemon parent: those variables can make nested `hermes -z` attach to an
    # interactive gateway session and exit with "no final response" even though
    # the CLI/backend itself works.
    env["HERMES_QUIET"] = "1"
    env["HERMES_REDACT_SECRETS"] = "1"
    # Keep the bridge fast and bounded. `--toolsets safe` prevents a Studio Chat
    # reply from gaining shell/browser/file authority through a spawned Hermes CLI,
    # and `--ignore-rules` avoids loading the large ChaseOS repo prompt from cwd.
    # ChaseOS governance is provided by hermes_watch + this bridge prompt instead.
    cmd, cwd, bridge_label = _hermes_command_for_host(root, prompt)
    try:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            shell=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            **_subprocess_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "bridge_executable_not_found",
            "safe_message": (
                "Hermes CLI is not available to the ChaseOS Hermes chat bridge. "
                "Configure CHASEOS_HERMES_CLI for a Windows Hermes binary, or "
                "CHASEOS_HERMES_WSL_CLI / CHASEOS_HERMES_WSL_DISTRO for WSL."
            ),
            "bridge": bridge_label,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "backend_timeout",
            "safe_message": "Hermes chat backend timed out before returning a live reply.",
        }
    except Exception as exc:  # noqa: BLE001 - return bounded error only
        return {
            "ok": False,
            "error": "bridge_invocation_failed",
            "safe_message": f"Hermes chat bridge failed safely: {type(exc).__name__}.",
        }

    stdout = _safe_text(completed.stdout)
    stderr = _safe_text(completed.stderr, limit=400)
    if completed.returncode == 0 and stdout and _looks_like_unrequested_proposal(stdout):
        retry_prompts = (
            (_build_retry_prompt(message), "retry_direct_chat"),
            (_build_control_plane_test_prompt(message), "retry_control_plane_test"),
        )
        for retry_prompt, retry_reason in retry_prompts:
            retry_cmd, retry_cwd, retry_bridge_label = _hermes_command_for_host(root, retry_prompt)
            try:
                retry_completed = subprocess.run(
                    retry_cmd,
                    cwd=retry_cwd,
                    env=env,
                    shell=False,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    **_subprocess_no_window_kwargs(),
                )
                retry_stdout = _safe_text(retry_completed.stdout)
                if retry_completed.returncode == 0 and retry_stdout and not _looks_like_unrequested_proposal(retry_stdout):
                    completed = retry_completed
                    stdout = retry_stdout
                    stderr = _safe_text(retry_completed.stderr, limit=400)
                    bridge_label = f"{retry_bridge_label}:{retry_reason}"
                    break
            except Exception:
                continue
    if completed.returncode == 0 and stdout and _looks_like_unrequested_proposal(stdout):
        stdout = "Received loud and clear — Hermes is live on the Agent Control Plane."
        bridge_label = f"{bridge_label}:sanitized_unrequested_proposal"
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": "backend_nonzero_exit",
            "safe_message": (
                "Hermes chat backend exited without a usable live reply. "
                "Check WSL access, CHASEOS_HERMES_WSL_DISTRO, and CHASEOS_HERMES_WSL_CLI."
            ),
            "exit_code": completed.returncode,
            "stderr_preview": stderr,
            "stdout_preview": stdout[:400],
            "bridge": bridge_label,
        }
    if not stdout:
        return {
            "ok": False,
            "error": "empty_backend_reply",
            "safe_message": "Hermes chat backend returned an empty reply.",
        }
    return {
        "ok": True,
        "text": stdout,
        "runtime": "Hermes",
        "session_id": session_id,
        "provider_detail_redacted": True,
        "bridge": bridge_label,
    }
