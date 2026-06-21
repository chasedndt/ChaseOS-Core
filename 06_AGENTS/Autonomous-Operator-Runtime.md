# Autonomous Operator Runtime

The Autonomous Operator Runtime pattern lets bounded workflows execute repeatable tasks while preserving human review and system governance.

## Runtime Pipeline

A safe operator workflow should:

1. load declared context;
2. validate manifest and role scope;
3. classify task authority;
4. execute only the approved handler;
5. write bounded artifacts;
6. emit audit evidence;
7. escalate when approval is required.

## Non-Goals

- no silent canonical promotion;
- no uncontrolled filesystem traversal;
- no credential value storage;
- no external side effects without approval;
- no private runtime state in public Core.

## Core Boundary

Core describes the pipeline pattern and schemas. Private deployments own live workflows, schedules, operator briefs, and runtime state.

*Graph links: [[OpenClaw-Runtime-Profile]]*
