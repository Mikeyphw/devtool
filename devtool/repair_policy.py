from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .models import Issue, OperationResult


_AUTO_FIX_CODES = {"CS0121", "CS0136", "CS1061", "CS8076", "CS8361", "CS8514", "CS8516"}
_CONFIRM_FIX_CODES = {"CS0103", "CS0246", "NU1101", "NU1605", "NU1701"}
_MANUAL_REVIEW_CODES = {"CS0535", "CS0546", "CS0104"}

_AUTO_FIX_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bGetFullPath\s*\(", re.IGNORECASE),
        "Replace `string.GetFullPath(...)` with `System.IO.Path.GetFullPath(...)`.",
    ),
    (
        re.compile(r"\bGetDirectoryName\s*\(", re.IGNORECASE),
        "Replace `string.GetDirectoryName(...)` with `System.IO.Path.GetDirectoryName(...)`.",
    ),
    (
        re.compile(r"\bSplit\s*\(", re.IGNORECASE),
        "Make the separator explicit with a `char[]` or `string[]` overload.",
    ),
    (
        re.compile(r"\b(interpolat|ternary|\?:)\b", re.IGNORECASE),
        "Parenthesize the ternary expression or split it into a temporary before interpolation.",
    ),
]

_CONFIRM_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(type or namespace name|does not contain a definition for|cannot find symbol)\b", re.IGNORECASE),
        "Check the rename map or restore the missing reference before rewriting the call site.",
    ),
    (
        re.compile(r"\bpackage reference|package restore|nuget\b", re.IGNORECASE),
        "Restore or realign the test/package references before rewriting the project.",
    ),
    (
        re.compile(r"\bnon-existent property|unknown property|cannot assign to\b", re.IGNORECASE),
        "Apply the framework-version specific QML/Avalonia replacement after confirming the target API.",
    ),
]

_MANUAL_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\btype or namespace name\b.*\bcould not be found\b|\bmissing\b.*\breplacement\b", re.IGNORECASE),
        "Review the deleted symbol or interface manually; there is no safe mechanical rename.",
    ),
    (
        re.compile(r"\bUI\b.*\bregression\b|\blayout\b.*\bregression\b", re.IGNORECASE),
        "Review the behavioral or UI change manually.",
    ),
]


@dataclass(slots=True)
class RepairFinding:
    bucket: str
    code: str
    file: str
    line: int | None
    message: str
    fix: str


@dataclass(slots=True)
class RepairReport:
    auto_fix: list[RepairFinding] = field(default_factory=list)
    confirm_fix: list[RepairFinding] = field(default_factory=list)
    manual_review: list[RepairFinding] = field(default_factory=list)

    def has_findings(self) -> bool:
        return bool(self.auto_fix or self.confirm_fix or self.manual_review)

    def counts(self) -> dict[str, int]:
        return {
            "auto_fix": len(self.auto_fix),
            "confirm_fix": len(self.confirm_fix),
            "manual_review": len(self.manual_review),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "auto_fix": [asdict(finding) for finding in self.auto_fix],
            "confirm_fix": [asdict(finding) for finding in self.confirm_fix],
            "manual_review": [asdict(finding) for finding in self.manual_review],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> RepairReport:
        payload = payload or {}

        def _items(key: str) -> list[RepairFinding]:
            items = payload.get(key)
            if not isinstance(items, list):
                return []
            findings: list[RepairFinding] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                findings.append(
                    RepairFinding(
                        bucket=str(item.get("bucket") or key),
                        code=str(item.get("code") or ""),
                        file=str(item.get("file") or ""),
                        line=item.get("line"),
                        message=str(item.get("message") or ""),
                        fix=str(item.get("fix") or ""),
                    )
                )
            return findings

        return cls(
            auto_fix=_items("auto_fix"),
            confirm_fix=_items("confirm_fix"),
            manual_review=_items("manual_review"),
        )


def _normalize_message(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _matching_fix(code: str, message: str) -> tuple[str, str] | None:
    normalized = _normalize_message(message)
    upper_code = code.upper()

    if upper_code in _AUTO_FIX_CODES:
        if upper_code == "CS1061" and ("getfullpath" in normalized.casefold() or "getdirectoryname" in normalized.casefold()):
            if "getdirectoryname" in normalized.casefold():
                return "auto_fix", "Replace `string.GetDirectoryName(...)` with `System.IO.Path.GetDirectoryName(...)`."
            return "auto_fix", "Replace `string.GetFullPath(...)` with `System.IO.Path.GetFullPath(...)`."
        if upper_code == "CS0121" and "split" in normalized.casefold():
            return "auto_fix", "Make the `Split(...)` separator explicit with a `char[]` or `string[]`."
        if upper_code == "CS0136":
            return "auto_fix", "Rename the inner local so it no longer shadows the outer symbol."
        if upper_code in {"CS8076", "CS8361", "CS8514", "CS8516"}:
            return "auto_fix", "Parenthesize the interpolated expression or split the ternary into temporaries."

    for pattern, fix in _AUTO_FIX_RULES:
        if pattern.search(normalized):
            return "auto_fix", fix
    for pattern, fix in _CONFIRM_RULES:
        if pattern.search(normalized):
            return "confirm_fix", fix
    for pattern, fix in _MANUAL_RULES:
        if pattern.search(normalized):
            return "manual_review", fix

    if upper_code in _CONFIRM_FIX_CODES:
        if upper_code in {"CS0103", "CS0246"}:
            return "confirm_fix", "Check the rename map or restore the missing dependency before rewriting the symbol."
        return "confirm_fix", "Restore the package/reference set or apply the framework-specific substitution after confirmation."
    if upper_code in _MANUAL_REVIEW_CODES:
        return "manual_review", "Review the behavior or deleted symbol manually."

    if "type or namespace name" in normalized.casefold() or "does not contain a definition for" in normalized.casefold():
        return "manual_review", "The symbol appears deleted or renamed without an obvious migration path."
    return None


def build_repair_report(results: list[OperationResult]) -> RepairReport:
    report = RepairReport()
    seen: set[tuple[str, str, str, str, str]] = set()

    for result in results:
        for issue in result.issues:
            match = _matching_fix(issue.code or "", issue.message or "")
            if match is None:
                continue
            bucket, fix = match
            finding = RepairFinding(
                bucket=bucket,
                code=issue.code or "-",
                file=issue.file or "-",
                line=issue.line,
                message=_normalize_message(issue.message),
                fix=fix,
            )
            key = (finding.bucket, finding.code, finding.file, str(finding.line or ""), finding.fix)
            if key in seen:
                continue
            seen.add(key)
            getattr(report, bucket).append(finding)

    return report


def repair_summary_lines_from_report(report: RepairReport, *, limit_per_bucket: int = 3) -> list[str]:
    if not report.has_findings():
        return []
    lines = ["Repair summary:"]
    sections = (
        ("auto_fix", "Auto-fix candidates"),
        ("confirm_fix", "Needs confirmation"),
        ("manual_review", "Manual review"),
    )
    for bucket, heading in sections:
        findings = getattr(report, bucket)
        if not findings:
            continue
        lines.append(f"- {heading}: {len(findings)}")
        for finding in findings[:limit_per_bucket]:
            location = finding.file
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            message = f"{finding.message} -> {finding.fix}" if finding.message else finding.fix
            lines.append(f"  - {location} {finding.code}: {message}")
        if len(findings) > limit_per_bucket:
            lines.append(
                f"  - …and {len(findings) - limit_per_bucket} more {bucket.replace('_', ' ')} item"
                f"{'s' if len(findings) - limit_per_bucket != 1 else ''}; see the full JSON summary."
            )
    return lines


def repair_summary_text(results: list[OperationResult]) -> str:
    return "\n".join(repair_summary_lines_from_results(results)).strip()


def _finding_from_mapping(item: dict[str, Any], *, default_bucket: str = "applied") -> RepairFinding:
    return RepairFinding(
        bucket=str(item.get("bucket") or default_bucket),
        code=str(item.get("code") or ""),
        file=str(item.get("file") or ""),
        line=item.get("line"),
        message=str(item.get("message") or ""),
        fix=str(item.get("fix") or ""),
    )


def applied_repair_findings_from_results(results: list[OperationResult]) -> list[RepairFinding]:
    findings: list[RepairFinding] = []
    for result in results:
        metadata = result.metadata or {}
        payload = metadata.get("applied_repairs")
        if not isinstance(payload, list):
            continue
        for item in payload:
            if isinstance(item, dict):
                findings.append(_finding_from_mapping(item))
    return findings


def _applied_repair_context(results: list[OperationResult]) -> tuple[list[RepairFinding], dict[str, Any]]:
    applied = applied_repair_findings_from_results(results)
    metadata: dict[str, Any] = {}
    for result in results:
        current = result.metadata or {}
        if current.get("applied_repairs"):
            metadata = current
            break
    return applied, metadata


def repair_summary_lines_from_results(results: list[OperationResult]) -> list[str]:
    candidate_report = build_repair_report(results)
    candidate_lines = repair_summary_lines_from_report(candidate_report)
    applied_findings, metadata = _applied_repair_context(results)
    if not applied_findings:
        return candidate_lines

    lines = ["Repair summary:"]
    lines.append(f"- Applied repairs: {len(applied_findings)}")
    for finding in applied_findings[:3]:
        location = finding.file
        if finding.line is not None:
            location = f"{location}:{finding.line}"
        message = f"{finding.message} -> {finding.fix}" if finding.message else finding.fix
        lines.append(f"  - {location} {finding.code}: {message}")
    if len(applied_findings) > 3:
        lines.append(
            f"  - …and {len(applied_findings) - 3} more applied repair"
            f"{'s' if len(applied_findings) - 3 != 1 else ''}; see the full JSON summary."
        )
    before = metadata.get("before_failures")
    after = metadata.get("after_failures")
    if metadata.get("restored"):
        lines.append("- Restored original source files because the retry did not reduce the error set.")
    elif before is not None and after is not None:
        lines.append(f"- Validation failures: {before} -> {after}")
    if candidate_lines:
        lines.append("")
        lines.extend(candidate_lines)
    return lines


def repair_summary_lines_from_payload(payload: dict[str, Any] | None) -> list[str]:
    payload = payload or {}
    report = RepairReport.from_dict(payload.get("repairs") if isinstance(payload, dict) else None)
    applied = []
    repairs_payload = payload.get("repairs") if isinstance(payload, dict) else None
    if isinstance(repairs_payload, dict):
        applied_payload = repairs_payload.get("applied")
        if isinstance(applied_payload, list):
            for item in applied_payload:
                if isinstance(item, dict):
                    applied.append(_finding_from_mapping(item))
    if applied:
        result = ["Repair summary:", f"- Applied repairs: {len(applied)}"]
        for finding in applied[:3]:
            location = finding.file
            if finding.line is not None:
                location = f"{location}:{finding.line}"
            message = f"{finding.message} -> {finding.fix}" if finding.message else finding.fix
            result.append(f"  - {location} {finding.code}: {message}")
        if len(applied) > 3:
            result.append(
                f"  - …and {len(applied) - 3} more applied repair"
                f"{'s' if len(applied) - 3 != 1 else ''}; see the full JSON summary."
            )
        if isinstance(repairs_payload, dict):
            before = repairs_payload.get("before_failures")
            after = repairs_payload.get("after_failures")
            if repairs_payload.get("restored"):
                result.append("- Restored original source files because the retry did not reduce the error set.")
            elif before is not None and after is not None:
                result.append(f"- Validation failures: {before} -> {after}")
        candidate = repair_summary_lines_from_report(report)
        if candidate:
            result.append("")
            result.extend(candidate)
        return result
    return repair_summary_lines_from_report(report)
