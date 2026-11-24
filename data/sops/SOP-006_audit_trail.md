# SOP-006 Audit Trail and Record Integrity (Fictional Training SOP)

Fictional procedure for the tracker audit trail.

## What Is Logged

The audit trail records the time of each action in UTC, the reference number, the action performed (report created, status changed, closure requested, closure approved or rejected, rejected transition attempts), the pseudonymous operator code and role, and the old and new status.

## What Is Not Logged

The audit trail never stores report descriptions, event descriptions or any personal information. It contains metadata only.

## Immutability

Audit entries are append-only. They cannot be edited or deleted, and any attempt to modify an audit entry is blocked by the database. Corrections are made by recording a new action, never by changing history.

## Operator Codes

Operators sign in with a pseudonymous code such as OP-01 and a role (Intake Operator or Safety Reviewer). Real names and email addresses are not stored in the tracker.
