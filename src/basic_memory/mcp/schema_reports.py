"""Markdown renderings of schema reports, shared by the schema tools (#1832)."""

from basic_memory.schemas.schema import DriftReport, InferenceReport, ValidationReport


def format_validation_report(report: ValidationReport) -> str:
    """Render a ValidationReport as readable markdown.

    Produces output the LLM can display directly instead of trying to
    interpret raw JSON, which leads to "undefined — invalid" rendering.
    """
    lines: list[str] = []

    # --- Header ---
    type_label = report.note_type or "all"
    lines.append(f"# Schema Validation: {type_label}")
    lines.append("")
    lines.append(
        f"Notes: {report.total_notes} | Valid: {report.valid_count} "
        f"| Warnings: {report.warning_count} | Errors: {report.error_count}"
    )
    lines.append("")

    # --- Per-type breakdown (all-types mode) ---
    if report.type_summaries:
        lines.append("## By Type")
        lines.append("")
        for summary in report.type_summaries:
            if summary.total_entities == 0:
                lines.append(f"- **{summary.note_type}**: no notes")
            else:
                lines.append(
                    f"- **{summary.note_type}**: {summary.valid_count}/{summary.total_notes} valid"
                )
        lines.append("")

    # --- Per-note results ---
    for r in report.results:
        status = "valid" if r.passed else "INVALID"
        lines.append(f"- **{r.note_identifier}** — {status}")
        for w in r.warnings:
            lines.append(f"  - warning: {w}")
        for e in r.errors:
            lines.append(f"  - error: {e}")

    return "\n".join(lines)


def format_inference_report(report: InferenceReport) -> str:
    """Render an InferenceReport as readable markdown.

    Without this formatter the LLM receives raw JSON and renders
    field names as "undefined".
    """
    lines: list[str] = []

    # --- Header ---
    lines.append(f"# Schema Inference: {report.note_type}")
    lines.append("")
    lines.append(f"Notes analyzed: {report.notes_analyzed}")
    lines.append("")

    # --- Suggested schema YAML ---
    if report.suggested_schema:
        lines.append("## Suggested Schema")
        lines.append("")
        lines.append("```yaml")
        lines.append("---")
        lines.append(f"title: {report.note_type.title()}")
        lines.append("type: schema")
        lines.append(f"entity: {report.note_type}")
        lines.append("version: 1")
        lines.append("schema:")
        for field_name, field_def in report.suggested_schema.items():
            lines.append(f"  {field_name}: {field_def}")
        lines.append("---")
        lines.append("```")
        lines.append("")

    # --- Field frequency table ---
    if report.field_frequencies:
        lines.append("## Field Frequencies")
        lines.append("")
        for f in report.field_frequencies:
            pct = f"{f.percentage:.0%}"
            req_marker = "required" if f.name in report.suggested_required else "optional"
            samples = ", ".join(f.sample_values[:3]) if f.sample_values else ""
            sample_str = f" (e.g. {samples})" if samples else ""
            lines.append(
                f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total}) "
                f"[{req_marker}]{sample_str}"
            )
        lines.append("")

    # --- Excluded fields ---
    if report.excluded:
        lines.append("## Excluded (below threshold)")
        lines.append("")
        for name in report.excluded:
            lines.append(f"- {name}")
        lines.append("")

    return "\n".join(lines)


def format_drift_report(report: DriftReport) -> str:
    """Render a DriftReport as readable markdown.

    Without this formatter the LLM receives raw JSON and renders
    field names as "undefined".
    """
    lines: list[str] = []

    # --- Header ---
    lines.append(f"# Schema Drift: {report.note_type}")
    lines.append("")

    has_drift = report.new_fields or report.dropped_fields or report.cardinality_changes

    if not has_drift:
        lines.append("No drift detected — schema matches actual usage.")
        return "\n".join(lines)

    # --- New fields ---
    if report.new_fields:
        lines.append("## New Fields (in notes but not in schema)")
        lines.append("")
        for f in report.new_fields:
            pct = f"{f.percentage:.0%}"
            lines.append(f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total})")
        lines.append("")

    # --- Dropped fields ---
    if report.dropped_fields:
        lines.append("## Dropped Fields (in schema but rare in notes)")
        lines.append("")
        for f in report.dropped_fields:
            pct = f"{f.percentage:.0%}"
            lines.append(f"- **{f.name}** ({f.source}) — {pct} ({f.count}/{f.total})")
        lines.append("")

    # --- Cardinality changes ---
    if report.cardinality_changes:
        lines.append("## Cardinality Changes")
        lines.append("")
        for change in report.cardinality_changes:
            lines.append(f"- {change}")
        lines.append("")

    return "\n".join(lines)
