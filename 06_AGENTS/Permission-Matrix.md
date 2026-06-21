# Permission Matrix

Use this matrix to describe which roles can read, propose, queue approvals, or execute approved writes.

| Surface | Read | Propose | Approval Required | Direct Write |
|---|---:|---:|---:|---:|
| Public docs | yes | yes | when changing truth | no |
| Private memory | deployment-defined | deployment-defined | yes | no |
| Runtime state | deployment-defined | deployment-defined | yes | no |
| Host mutation | no by default | no by default | explicit approval | no by default |

Direct writes should remain rare and documented.
