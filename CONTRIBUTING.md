# Contributing — ChaseOS Core

ChaseOS Core is MIT-licensed and developed in the open, but it is early-stage and
governed.

> **External code contributions are not yet being accepted.**

Until the contribution policy, CLA/DCO decision, CI/security checks, and maintainer
governance are finalised (see `GOVERNANCE.md`), we are not merging external core
contributions. This protects the licence boundary between MIT Core and the
proprietary ChaseOS Studio/Cloud/Control Kernel. Issues, reproductions, and design
feedback are welcome now; report security issues privately (`SECURITY.md`).

When contributions open, expect: a DCO sign-off or CLA; tests for any change
(`pytest`); no new runtime dependencies without discussion (Core is stdlib-leaning);
and **no code that imports proprietary ChaseOS Studio/Cloud/Control-Kernel modules** —
Core must remain cleanly MIT and independently buildable.
