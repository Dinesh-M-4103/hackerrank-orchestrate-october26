import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ghost_audit.packaging import build_trace_zip, find_claude_sessions, verify_trace_zip

LINE = json.dumps({"type": "user", "message": "hello"})


class TraceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def session(self, name="s1.jsonl", lines=3, extra=""):
        p = self.tmp / "live" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("\n".join([LINE] * lines) + extra + "\n", encoding="utf-8")
        return p

    def export(self, name="export.txt", text="readable transcript"):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_finds_only_this_repos_sessions_and_never_the_memory_folder(self):
        home = self.tmp / "home"
        proj = home / ".claude" / "projects" / "c--Users-x-Desktop-my-repo"
        (proj / "memory").mkdir(parents=True)
        (proj / "a.jsonl").write_text(LINE, encoding="utf-8")
        (proj / "memory" / "m.md").write_text("private", encoding="utf-8")
        other = home / ".claude" / "projects" / "c--Users-x-Desktop-other"
        other.mkdir(parents=True)
        (other / "b.jsonl").write_text(LINE, encoding="utf-8")
        found = find_claude_sessions(Path("C:/Users/x/Desktop/my-repo"), home)
        self.assertEqual([p.name for p in found], ["a.jsonl"])

    def test_a_complete_trace_verifies(self):
        s, e = self.session(), self.export()
        out = self.tmp / "agent-trace.zip"
        names = build_trace_zip(out, [s], [e])
        self.assertEqual(names, ["agent-trace/claude-code/s1.jsonl", "agent-trace/claude-code/export.txt"])
        self.assertEqual(verify_trace_zip(out, None, [s]), [])
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(zf.read("agent-trace/claude-code/s1.jsonl"), s.read_bytes())     # original bytes, unaltered

    def test_missing_export_is_reported(self):
        s = self.session()
        out = self.tmp / "t.zip"
        build_trace_zip(out, [s], [])
        self.assertIn("no readable export", {p.kind for p in verify_trace_zip(out, None, [s])})

    def test_a_copy_that_is_older_than_the_live_session_is_stale(self):
        s, e = self.session(lines=3), self.export()
        out = self.tmp / "t.zip"
        build_trace_zip(out, [s], [e])
        s.write_text(s.read_text(encoding="utf-8") + (LINE + "\n") * 5, encoding="utf-8")     # the session kept growing
        self.assertIn("stale copy", {p.kind for p in verify_trace_zip(out, None, [s])})

    def test_a_new_session_that_is_not_in_the_archive_is_reported(self):
        s, e = self.session("s1.jsonl"), self.export()
        out = self.tmp / "t.zip"
        build_trace_zip(out, [s], [e])
        s2 = self.session("s2.jsonl")
        self.assertIn("session missing from the trace", {p.kind for p in verify_trace_zip(out, None, [s, s2])})

    def test_building_refuses_secrets_instead_of_silently_editing_the_trace(self):
        secret = "LIVE-KEY-VALUE-0123456789-ABCDEF"
        s = self.session(extra="\n" + json.dumps({"type": "tool", "out": secret}))
        with self.assertRaises(ValueError) as cm:
            build_trace_zip(self.tmp / "t.zip", [s], [self.export()], secret)
        self.assertNotIn(secret, str(cm.exception))
        shaped = self.session("s3.jsonl", extra="\n" + json.dumps({"k": "AIza" + "B" * 35}))
        with self.assertRaises(ValueError):
            build_trace_zip(self.tmp / "t2.zip", [shaped], [self.export()])

    def test_credential_files_and_duplicate_names_are_refused(self):
        s = self.session()
        with self.assertRaises(ValueError):
            build_trace_zip(self.tmp / "t.zip", [s], [self.export(".env", "x")])
        with self.assertRaises(ValueError):
            build_trace_zip(self.tmp / "t.zip", [s], [self.export("s1.jsonl", "dup")])
        with self.assertRaises(ValueError):
            build_trace_zip(self.tmp / "t.zip", [], [self.export()])

    def test_verifier_catches_bad_archives(self):
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("agent-trace/claude-code/s.jsonl", LINE + "\nnot json\n")
            zf.writestr("agent-trace/claude-code/export.txt", "ok")
            zf.writestr("agent-trace/claude-code/.env", "x")
            zf.writestr("agent-trace/memory/note.md", "x")
            zf.writestr("stray.txt", "x")
            zf.writestr("agent-trace/claude-code/inner.zip", "x")
        kinds = {p.kind for p in verify_trace_zip(bad)}
        self.assertGreaterEqual(kinds, {"invalid JSONL", "forbidden entry", "unexpected location", "nested archive"})


if __name__ == "__main__":
    unittest.main()
