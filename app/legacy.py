"""Reading records written before the two-class model. History and query-log
records used to carry one three-state `status`; it maps without loss to the
model class plus the policy that was applied, so those files stay readable
and are rewritten in the current shape on their next update."""

LEGACY_STATUS = {
    "seguro": ("legitima", "advertencia"),
    "advertencia": ("phishing", "advertencia"),
    "bloqueo": ("phishing", "bloqueo"),
}


def upgrade(record):
    """The record with `status` replaced by `verdict` and `policy`; a record
    already in the current shape, or one that is not a known legacy record, is
    returned unchanged for the caller's own validation."""
    if (not isinstance(record, dict) or "status" not in record or "verdict" in record
            or "policy" in record or record["status"] not in LEGACY_STATUS):
        return record
    verdict, policy = LEGACY_STATUS[record["status"]]
    upgraded = {name: value for name, value in record.items() if name != "status"}
    return {**upgraded, "verdict": verdict, "policy": policy}
