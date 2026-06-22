# Third-Party Notices — ChaseOS Core

ChaseOS Core is MIT-licensed. It uses the following third-party software, each
under its own license.

## Required runtime dependency

- **PyYAML** — MIT License. YAML parsing for workflow manifests, role cards, the
  task-type table, schedules, and context boot.

## Optional dependencies (install only via extras)

- **Playwright** (`pip install -e .[browser]`) — Apache-2.0. Live browser /
  operator surface. Without it, the operator surface runs in stub mode.
- **faster-whisper**, **piper-tts**, **sounddevice**, **numpy**
  (`pip install -e .[voice]`) — on-device speech-to-text / text-to-speech.
  Refer to each project for its license terms.
- **pytest**, **pytest-cov** (`pip install -e .[dev]`) — MIT. Test tooling.

## Notes

- Core requires only PyYAML at runtime; everything else is opt-in via extras.
- Bundled example presets, schemas, and templates are part of ChaseOS Core and
  are covered by the repository's MIT `LICENSE.md`.
