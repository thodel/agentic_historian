# UBELIX, read-only, from the ATR MCP server

Three tools on the MCP server on tei, `ubelix_queue`, `ubelix_job` and
`ubelix_record`, answer "is the training still queued, was it preempted, what
does its log say" from a cloud session (#674). Until now each of those questions
cost a round trip through a person: copy a command, run it on submit02, paste
the output back.

## Why it is shaped like this

A cloud session cannot reach UBELIX at all: the cluster sits inside the
university network and the session's egress proxy refuses ssh anyway
([`docs/CLAUDE_CODE_CONNECTIVITY.md`](../../docs/CLAUDE_CODE_CONNECTIVITY.md)).
The MCP server on tei is reachable from the session, and tei can reach UBELIX.
The missing hop is tei → UBELIX, and this directory is that hop.

**The key can run one script and nothing else.** UBELIX's `authorized_keys`
binds it to `atr-mcp-ro` as a forced command, so sshd runs that script whatever
tei sends. The script understands three requests:

| request | runs |
|---|---|
| `queue` | `squeue -u $USER`, then `squeue --start` |
| `job <digits>` | `sacct -j <id> -u $USER -D -X` (preemptions and requeues) |
| `record <id> [lines]` | reads `job.json` and the tail of `logs/<stage>.log` (or `runner.log`) under `/scratch/network/users/$USER/runs/jobs/<id>/`, at most 200 lines |

Anything else exits 2 having run nothing. A stolen MCP token therefore costs
reading the queue and the trainer's records, never submitting, cancelling or
writing. `scancel`, `sbatch` and `close_job` are deliberately absent; adding one
is a separate decision with its own issue.

Both sides validate. tei (`mcp_atr/ubelix.py`) refuses before opening a
connection, and the script refuses again on UBELIX, because the forced command is
configuration on another machine that nothing on tei can verify. A test keeps the
two copies of each pattern identical.

## Before installing

1. **UBELIX's terms of use.** Check whether non-interactive key access from a
   university server is allowed, and whether the login requires a second factor
   that rules a key out. This is the gate; do not install around it.
2. **tei reaches submit02.** As the user the MCP service runs as:
   `ssh -o BatchMode=yes <submit02 host> true` with your normal key. A
   `Could not resolve` or a timeout means the route, not the key.

## Install

### 1. On tei: a key for this purpose only

As the user that `atr-mcp.service` runs as (its `User=`):

```bash
ssh-keygen -t ed25519 -N '' -C atr-mcp@tei -f ~/.ssh/ubelix_mcp
cat ~/.ssh/ubelix_mcp.pub
```

No passphrase: a service cannot type one, and the forced command is what limits
the key. It never goes into the repository and never into an agent.

### 2. On UBELIX: the script and the forced command

```bash
mkdir -p ~/bin
# copy deploy/ubelix-ro/atr-mcp-ro from a checkout to ~/bin/atr-mcp-ro, then:
chmod 755 ~/bin/atr-mcp-ro
head -1 ~/bin/atr-mcp-ro && python3 --version      # 3.7 or newer
```

Append **one line** to `~/.ssh/authorized_keys`, the public key from step 1
behind the options:

```
restrict,command="/storage/homefs/th19c587/bin/atr-mcp-ro" ssh-ed25519 AAAA… atr-mcp@tei
```

Use the absolute path (`echo ~/bin/atr-mcp-ro` prints it). `restrict` turns
off PTY, port, agent and X11 forwarding. Your own key's line stays as it is.

### 3. On tei: check the binding before wiring it up

```bash
K=~/.ssh/ubelix_mcp; H=<submit02 host>
ssh -T -i $K -o IdentitiesOnly=yes $H queue                 # your squeue
ssh -T -i $K -o IdentitiesOnly=yes $H 'hostname'; echo "exit $?"   # must be: refused …, exit 2
ssh -T -i $K -o IdentitiesOnly=yes $H 'job 18929698'
```

The second line is the one that matters: if `hostname` prints a hostname, the
forced command is not in effect and the key is a full login. Remove the line
from `authorized_keys` and redo step 2.

The first connection also records UBELIX's host key in `~/.ssh/known_hosts`.
That has to happen here, by hand: the service runs with `BatchMode=yes` and a
read-only home (`ProtectHome=read-only`), so it can neither ask nor write.

### 4. The MCP server

Two lines in `/etc/atr-mcp.env`, then restart:

```bash
sudo tee -a /etc/atr-mcp.env >/dev/null <<EOF
ATR_UBELIX_SSH_HOST=<submit02 host, or user@host>
ATR_UBELIX_SSH_KEY=/home/<service user>/.ssh/ubelix_mcp
EOF
sudo systemctl restart atr-mcp
```

A restart does not touch running batches (`KillMode=process`, see
[`deploy/mcp-atr/README.md`](../mcp-atr/README.md#operating-it)). Sessions opened
before the restart do not see the new tools; tool sets are fixed at session
start.

Without both variables the tools answer "not configured" and do nothing.

## Removing it

Delete the `atr-mcp-ro` line from UBELIX's `authorized_keys`. That alone closes
the hop, whatever tei still has configured.
