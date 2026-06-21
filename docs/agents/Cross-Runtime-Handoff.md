# Cross-Runtime Handoff

ChaseOS can coordinate multiple runtime instances. Cross-runtime handoff must preserve authority boundaries.

## Required fields

- source runtime;
- target runtime role;
- task class;
- allowed reads;
- allowed writes;
- approval requirements;
- expected output artifact;
- verification command.

## Safety

A target runtime does not gain authority because another runtime mentions it. Authority comes from the active workflow manifest, role card, and approval gate.
