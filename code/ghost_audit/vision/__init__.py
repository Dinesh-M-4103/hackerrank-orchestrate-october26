"""Optional screenshot interpretation (Gemini behind a feature flag, with a fake client for tests).

Nothing in this package runs unless explicitly enabled, and nothing here decides deletion status, policy actions or
step order: it only turns a screenshot into *directly observed* text and identifiers, which the deterministic pipeline
then treats like any other (time-gated) evidence.
"""
