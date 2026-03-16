"""
PermitGrab - Lifecycle Stage Classification
Assigns color-coded lifecycle pills to permits, signals, and violations.
"""

# Lifecycle stages in chronological order
LIFECYCLE_STAGES = {
    "early_signal": {
        "label": "Early Signal",
        "color": "#7C3AED",  # Purple
        "order": 1,
    },
    "approved": {
        "label": "Approved",
        "color": "#2563EB",  # Blue
        "order": 2,
    },
    "filed": {
        "label": "Filed",
        "color": "#4F46E5",  # Indigo
        "order": 3,
    },
    "permitted": {
        "label": "Permitted",
        "color": "#16A34A",  # Green
        "order": 4,
    },
    "in_progress": {
        "label": "In Progress",
        "color": "#D97706",  # Amber
        "order": 5,
    },
    "final": {
        "label": "Final",
        "color": "#0D9488",  # Teal
        "order": 6,
    },
    "violation": {
        "label": "Violation",
        "color": "#DC2626",  # Red
        "order": 0,  # Special - shown as secondary
    },
    "denied": {
        "label": "Denied",
        "color": "#6B7280",  # Gray
        "order": 7,
    },
}

# Keywords for status matching (case-insensitive)
FILED_KEYWORDS = ["filed", "application", "submitted", "in review", "plan check", "pending review"]
PERMITTED_KEYWORDS = ["issued", "approved", "active", "completed", "permit issued"]
IN_PROGRESS_KEYWORDS = ["in progress", "partial", "under construction", "inspection"]
FINAL_KEYWORDS = ["final", "c of o", "certificate", "closed", "finaled"]
DENIED_KEYWORDS = ["denied", "expired", "revoked", "withdrawn", "cancelled", "void"]


def get_record_type(record):
    """Determine the type of record (permit, signal, or violation)."""
    if record.get("signal_id"):
        return "signal"
    if record.get("violation_id"):
        return "violation"
    if record.get("permit_number") or record.get("id"):
        return "permit"
    return "unknown"


def match_status_keywords(status, keywords):
    """Check if status contains any of the keywords (case-insensitive)."""
    if not status:
        return False
    status_lower = status.lower()
    return any(kw in status_lower for kw in keywords)


def get_lifecycle_stage(record, linked_violations=None):
    """
    Determine the lifecycle stage for a record.

    Args:
        record: A permit, signal, or violation record
        linked_violations: List of violations linked to this record (optional)

    Returns:
        dict with 'label', 'color', 'stage_key', and optional 'secondary' for violations
    """
    record_type = get_record_type(record)
    status = record.get("status", "") or ""

    # Determine primary stage
    stage_key = None

    if record_type == "signal":
        # Signals: check status
        status_lower = status.lower()
        if match_status_keywords(status, DENIED_KEYWORDS):
            stage_key = "denied"
        elif "approved" in status_lower or "granted" in status_lower:
            stage_key = "approved"
        else:
            stage_key = "early_signal"

    elif record_type == "violation":
        # Violations always show as violation
        stage_key = "violation"

    else:
        # Permits: match against keywords in order
        if match_status_keywords(status, DENIED_KEYWORDS):
            stage_key = "denied"
        elif match_status_keywords(status, FINAL_KEYWORDS):
            stage_key = "final"
        elif match_status_keywords(status, IN_PROGRESS_KEYWORDS):
            stage_key = "in_progress"
        elif match_status_keywords(status, PERMITTED_KEYWORDS):
            stage_key = "permitted"
        elif match_status_keywords(status, FILED_KEYWORDS):
            stage_key = "filed"
        else:
            # Default for permits with unrecognized status
            stage_key = "filed"

    # Build result
    stage = LIFECYCLE_STAGES[stage_key]
    result = {
        "label": stage["label"],
        "color": stage["color"],
        "stage_key": stage_key,
        "order": stage["order"],
        "secondary": None,
    }

    # Check for linked violations (adds secondary pill)
    if linked_violations and len(linked_violations) > 0 and stage_key != "violation":
        violation_stage = LIFECYCLE_STAGES["violation"]
        result["secondary"] = {
            "label": violation_stage["label"],
            "color": violation_stage["color"],
        }

    return result


def get_pill_html(stage_result):
    """
    Generate HTML for lifecycle pill(s).

    Args:
        stage_result: Result from get_lifecycle_stage()

    Returns:
        HTML string for the pill(s)
    """
    def make_pill(label, color):
        # Convert hex color to rgba with 15% opacity for background
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        bg_color = f"rgba({r},{g},{b},0.15)"

        return (
            f'<span style="background:{bg_color};color:{color};'
            f'font-size:11px;font-weight:600;padding:2px 10px;'
            f'border-radius:9999px;display:inline-block;">{label}</span>'
        )

    html = make_pill(stage_result["label"], stage_result["color"])

    if stage_result.get("secondary"):
        sec = stage_result["secondary"]
        html += f'<span style="margin-left:4px;">{make_pill(sec["label"], sec["color"])}</span>'

    return html


def get_pill_css_class(stage_key):
    """Get CSS class name for a lifecycle stage."""
    return f"pill-{stage_key.replace('_', '-')}"


def get_most_advanced_stage(records, linked_violations=None):
    """
    Given multiple records for an address, return the most advanced lifecycle stage.

    Args:
        records: List of permit/signal records
        linked_violations: List of violations for the address

    Returns:
        The most advanced stage result
    """
    if not records:
        return None

    best_stage = None
    best_order = -1

    for record in records:
        stage = get_lifecycle_stage(record, linked_violations)
        if stage["order"] > best_order:
            best_order = stage["order"]
            best_stage = stage

    # Add violation secondary if any violations exist
    if linked_violations and len(linked_violations) > 0 and best_stage:
        if best_stage.get("stage_key") != "violation":
            violation_stage = LIFECYCLE_STAGES["violation"]
            best_stage["secondary"] = {
                "label": violation_stage["label"],
                "color": violation_stage["color"],
            }

    return best_stage


def get_timeline_stages(records):
    """
    Get all stages a project has passed through for timeline display.

    Args:
        records: List of all records for an address

    Returns:
        List of stages with 'reached' boolean
    """
    # Determine which stages have been reached
    reached_stages = set()
    has_violation = False

    for record in records:
        stage = get_lifecycle_stage(record)
        reached_stages.add(stage["stage_key"])
        if stage["stage_key"] == "violation" or stage.get("secondary"):
            has_violation = True

    # Build timeline in order
    timeline_order = ["early_signal", "approved", "filed", "permitted", "in_progress", "final"]
    timeline = []

    for stage_key in timeline_order:
        stage = LIFECYCLE_STAGES[stage_key]
        timeline.append({
            "key": stage_key,
            "label": stage["label"],
            "color": stage["color"],
            "reached": stage_key in reached_stages,
            "has_violation": has_violation and stage_key in reached_stages,
        })

    return timeline


# For CSV export - just the label text
def get_lifecycle_label(record, linked_violations=None):
    """Get just the lifecycle stage label for CSV export."""
    stage = get_lifecycle_stage(record, linked_violations)
    label = stage["label"]
    if stage.get("secondary"):
        label += f" + {stage['secondary']['label']}"
    return label
