"""Read-only self-detection of malicious / anomalous agent behavior.

ChaseOS has a strong PREVENTIVE layer (Gate, protected-file guard, role cards)
but historically no DETECTIVE layer: rich behavioral signals were collected and
never analyzed. This module consumes signals the system already produces and
flags anomaly signatures over a rolling window:

- gate-denial telemetry (`07_LOGS/Runtime-Audits/gate-denials.jsonl`) —
  protected-file write attempts, break-glass activations, denial spikes;
- AOR execution audit records (`07_LOGS/Agent-Activity/*.json`) — escalation /
  failure spikes;
- runtime events (`07_LOGS/Runtime-Events/runtime-events.jsonl`) — actions whose
  `authority` block reports a shell/provider/connector side effect;
- unreviewed incident candidates (`runtime/memory/repair/{runtime}.json`).

It is alert-only: it never blocks, mutates runtime state, or calls a provider.
``scan_behavior`` returns a structured report; ``write_report`` persists it under
``07_LOGS/Runtime-Audits/``. Pure stdlib.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── thresholds (conservative; tune via scan_behavior kwargs) ─────────────────
DEFAULT_WINDOW_HOURS = 24
DENIAL_SPIKE = 10          # total gate denials in window → MEDIUM
ESCALATION_SPIKE = 8       # escalated/failed audit records in window → MEDIUM
INCIDENT_CANDIDATE_ALERT = 5  # unreviewed incident candidates → MEDIUM

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

_AUDITS_DIR_REL = "07_LOGS/Runtime-Audits"
_DENIALS_REL = "07_LOGS/Runtime-Audits/gate-denials.jsonl"
_ACTIVITY_DIR_REL = "07_LOGS/Agent-Activity"
_EVENTS_REL = "07_LOGS/Runtime-Events/runtime-events.jsonl"
_REPAIR_DIR_REL = "runtime/memory/repair"


@dataclass
class TripwireFinding:
    signal: str
    severity: str
    count: int
    detail: str
    samples: list[str] = field(default_factory=list)


@dataclass
class TripwireReport:
    ok: bool                       # True = no HIGH/MEDIUM anomaly
    scanned_at: str
    window_hours: int
    findings: list[dict]
    counts: dict
    highest_severity: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


# ── individual signal scanners ───────────────────────────────────────────────
def _scan_gate_denials(vault_root: Path, cutoff: datetime) -> list[TripwireFinding]:
    rows = _read_jsonl(vault_root / _DENIALS_REL)
    recent = [r for r in rows if (_parse_ts(r.get("ts")) or cutoff) >= cutoff]
    findings: list[TripwireFinding] = []

    protected = [r for r in recent if r.get("hook") == "protected_write_guard" and r.get("decision") == "block"]
    if protected:
        findings.append(TripwireFinding(
            signal="protected_file_write_attempt",
            severity=SEV_HIGH,
            count=len(protected),
            detail="agent attempted to write a protected file (blocked by the Gate)",
            samples=[str(r.get("target")) for r in protected[:5]],
        ))

    break_glass = [r for r in recent if r.get("gate_disabled") or r.get("decision") == "break_glass"]
    if break_glass:
        findings.append(TripwireFinding(
            signal="break_glass_active",
            severity=SEV_HIGH,
            count=len(break_glass),
            detail="CHASEOS_GATE_DISABLE was active — all Gate enforcement bypassed",
            samples=[str(r.get("target") or r.get("hook")) for r in break_glass[:5]],
        ))

    if len(recent) >= DENIAL_SPIKE:
        findings.append(TripwireFinding(
            signal="gate_denial_spike",
            severity=SEV_MEDIUM,
            count=len(recent),
            detail=f"{len(recent)} gate denials in window (>= {DENIAL_SPIKE}) — possible probing/injection",
        ))
    return findings


def _scan_agent_activity(vault_root: Path, cutoff: datetime) -> list[TripwireFinding]:
    activity_dir = vault_root / _ACTIVITY_DIR_REL
    if not activity_dir.is_dir():
        return []
    anomalous = []
    for path in activity_dir.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(rec, dict) and str(rec.get("status", "")).lower() in {"escalated", "failed", "blocked"}:
            anomalous.append((path.name, rec.get("escalation_reason") or rec.get("error") or rec.get("status")))
    findings: list[TripwireFinding] = []
    if len(anomalous) >= ESCALATION_SPIKE:
        findings.append(TripwireFinding(
            signal="escalation_failure_spike",
            severity=SEV_MEDIUM,
            count=len(anomalous),
            detail=f"{len(anomalous)} escalated/failed workflow runs in window (>= {ESCALATION_SPIKE})",
            samples=[f"{name}: {reason}" for name, reason in anomalous[:5]],
        ))
    return findings


def _scan_runtime_events(vault_root: Path, cutoff: datetime) -> list[TripwireFinding]:
    rows = _read_jsonl(vault_root / _EVENTS_REL)
    recent = [r for r in rows if (_parse_ts(r.get("created_at") or r.get("ts")) or cutoff) >= cutoff]
    side_effects: list[str] = []
    for r in recent:
        authority = r.get("authority") if isinstance(r.get("authority"), dict) else {}
        if any(authority.get(k) for k in ("shell_command_performed", "provider_call_performed", "connector_call_performed")):
            flags = [k for k in ("shell_command_performed", "provider_call_performed", "connector_call_performed") if authority.get(k)]
            side_effects.append(f"{r.get('adapter_id') or r.get('runtime_name') or '?'}:{r.get('event_type') or '?'} [{','.join(flags)}]")
    findings: list[TripwireFinding] = []
    if side_effects:
        # Informational by default — surfaces shell/provider/connector side effects
        # for operator review (off-policy correlation vs manifests is a follow-up).
        findings.append(TripwireFinding(
            signal="external_side_effect_events",
            severity=SEV_LOW,
            count=len(side_effects),
            detail="runtime events reported shell/provider/connector side effects",
            samples=side_effects[:5],
        ))
    return findings


def _scan_incident_candidates(vault_root: Path) -> list[TripwireFinding]:
    repair_dir = vault_root / _REPAIR_DIR_REL
    if not repair_dir.is_dir():
        return []
    unreviewed = 0
    samples: list[str] = []
    for path in repair_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        candidates = data.get("incident_candidates") if isinstance(data, dict) else None
        if isinstance(candidates, list):
            for c in candidates:
                if isinstance(c, dict) and not c.get("operator_reviewed", False):
                    unreviewed += 1
                    if len(samples) < 5:
                        samples.append(f"{path.stem}: {c.get('reason') or c.get('summary') or 'unreviewed'}")
    findings: list[TripwireFinding] = []
    if unreviewed >= INCIDENT_CANDIDATE_ALERT:
        findings.append(TripwireFinding(
            signal="unreviewed_incident_candidates",
            severity=SEV_MEDIUM,
            count=unreviewed,
            detail=f"{unreviewed} unreviewed incident candidates accumulated (>= {INCIDENT_CANDIDATE_ALERT})",
            samples=samples,
        ))
    return findings


# ── orchestrator ─────────────────────────────────────────────────────────────
def scan_behavior(
    vault_root: Path | str,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now: datetime | None = None,
) -> TripwireReport:
    root = Path(vault_root)
    current = _now(now)
    cutoff = current - timedelta(hours=window_hours)

    findings: list[TripwireFinding] = []
    findings += _scan_gate_denials(root, cutoff)
    findings += _scan_agent_activity(root, cutoff)
    findings += _scan_runtime_events(root, cutoff)
    findings += _scan_incident_candidates(root)

    severities = {f.severity for f in findings}
    highest = SEV_HIGH if SEV_HIGH in severities else SEV_MEDIUM if SEV_MEDIUM in severities else SEV_LOW if SEV_LOW in severities else None
    ok = highest not in (SEV_HIGH, SEV_MEDIUM)

    counts = {
        "findings": len(findings),
        "high": sum(1 for f in findings if f.severity == SEV_HIGH),
        "medium": sum(1 for f in findings if f.severity == SEV_MEDIUM),
        "low": sum(1 for f in findings if f.severity == SEV_LOW),
    }
    return TripwireReport(
        ok=ok,
        scanned_at=current.isoformat().replace("+00:00", "Z"),
        window_hours=window_hours,
        findings=[asdict(f) for f in findings],
        counts=counts,
        highest_severity=highest,
    )


def write_report(vault_root: Path | str, report: TripwireReport) -> Path:
    root = Path(vault_root)
    out_dir = root / _AUDITS_DIR_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.scanned_at.replace(":", "").replace("-", "").replace("Z", "")
    out_path = out_dir / f"tripwire-{stamp}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    """`python -m runtime.agent_control.behavior_tripwire [--vault-root P] [--window-hours N] [--write] [--json]`"""
    import argparse

    parser = argparse.ArgumentParser(description="Scan recent agent behavior signals for anomalies (read-only).")
    parser.add_argument("--vault-root", default=None)
    parser.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--write", action="store_true", help="Persist the report under 07_LOGS/Runtime-Audits/")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.vault_root:
        root = Path(args.vault_root)
    else:
        import os
        env = os.environ.get("CHASEOS_VAULT_ROOT")
        root = Path(env) if env else Path(__file__).resolve().parents[2]

    report = scan_behavior(root, window_hours=args.window_hours)
    if args.write:
        write_report(root, report)
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"behavior tripwire: ok={report.ok} highest={report.highest_severity} "
              f"findings={report.counts['findings']} (high={report.counts['high']} medium={report.counts['medium']})")
        for f in report.findings:
            print(f"  [{f['severity'].upper()}] {f['signal']} x{f['count']} — {f['detail']}")
    # Non-zero exit when a HIGH anomaly is present (for scheduled alerting).
    return 1 if report.highest_severity == SEV_HIGH else 0


if __name__ == "__main__":
    raise SystemExit(main())
