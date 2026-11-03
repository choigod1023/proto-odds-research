# Verification receipt

- Frozen population: 728 fixtures; complete eligible roster and actual starter: 728; exclusions: 0.
- Independent replay verified 728 fixture identities and 1,898 distinct referenced official response bodies, including all response SHA-256 values and byte lengths.
- Eight unit tests passed, including explicit starter flags, name-change evidence, doubleheader and resumed-game disambiguation, and target-statistic poisoning of identity matching.
- Source and staged Git bytes matched exactly. Both `core.autocrlf=true` and `core.autocrlf=false` produced the same staged blob IDs:

| Artifact | SHA-256 | Git blob SHA-1 |
| --- | --- | --- |
| `rosters.jsonl` | `2c56d0139dd6de89777aefe9e74d3e925af5bb9dafc3d4603c68f44fd640d485` | `acc638e54d3fd726c52a10a8422fd134f3c5040f` |
| `batter-identity-only.json` | `7aafb72ca2df318bf3642b7f5ddcc59fbd09013cdc3a70b5ff672b33c2f4254f` | `690e2e7ccf3da4724d916401ab30fdc2c0c3183a` |
| `official-evidence.tar.gz` | `512bed8407fca4132d2497e2dcc7b1ee43cd92133d7ffbe1e65d4ff4eae88800` | `74a01452df1dd72a557068efab2e654cd3c2e758` |

Collection did not fully meet the requested minimum spacing: 128 observed gaps
were shorter than 1.5 seconds during an accidental overlapping-process interval;
92 requests repeated an already requested normalized URL and parameters. Both
processes were stopped and exclusive locking added. The 1,767 logged attempts
plus a conservative six possible interrupted attempts remain below 2,400.
This issue is an operational limitation, not evidence of historical pregame receipt.
