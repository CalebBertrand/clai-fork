# CLAI

CLAI is a next-generation, context-aware command-line assistant that combines the reasoning of
large language models with the precision of a Unix shell. It lets you interact with your system
using natural language; safely, intelligently, and transparently.

Every command runs against an **overlayfs sandbox** of your working directory. You see the diff of
everything the session touched before deciding whether to keep it or throw it away.

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
```

Natural-language prompting needs an OpenAI key; the sandboxed shell does not.

```bash
cp .env.example .env    # then add your OPENAI_API_KEY
```

## Running

```bash
./start.sh              # sandboxes the directory you run it from
```

or directly:

```bash
venv/bin/python start_shell.py [directory]   # defaults to the current directory
```

Inside the shell:

| Input | Effect |
| --- | --- |
| `ls -la`, `grep -r foo .`, `cat a \| wc -l` | Runs as a normal shell command, inside the sandbox |
| `/find the largest log files` | Natural-language prompt, translated to a command by the LLM |
| `/exit` | Ends the session and shows the diff |

Working directory, shell variables, quoting, pipes and redirects all behave as they would in bash,
because the whole line is handed to a shell running inside the sandbox.

On exit you get a unified diff of every added, modified and deleted file, then a `Keep changes?`
prompt. Answer `n` and the base directory is left exactly as it was.

## Tests

```bash
venv/bin/python test_sandbox.py            # functional tests, no root needed
venv/bin/mypy . --exclude venv             # type check
sudo venv/bin/python test_sandbox_security.py   # privileged sensitive-path hiding tests
```

## How the sandbox works

CLAI mounts an overlayfs whose lower layer is your real directory and whose upper layer is a
temporary scratch directory. Reads fall through to the real files; writes land in the scratch layer.

```
                 ┌──────────────────────────┐
   commands ───▶ │  merged view (what the   │
                 │  sandboxed shell sees)   │
                 └───────────┬──────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
     ┌────────▼─────────┐          ┌─────────▼──────────┐
     │ upper: /tmp/...  │          │ lower: your dir    │
     │ all writes land  │          │ read-only          │
     │ here             │          │                    │
     └────────┬─────────┘          └────────────────────┘
              │
              └──▶ diff on exit ──▶ keep (copy up) or discard (delete scratch)
```

There are two modes, chosen automatically:

**Unprivileged (default).** Requires Linux 5.11+ with unprivileged user namespaces enabled. CLAI
runs `unshare -rm` and mounts the overlay over your directory *inside that private mount
namespace*, so paths look completely normal to commands while the real directory stays untouched.
A single long-lived shell inside the namespace serves every command, which is what makes `cd` and
shell variables persist.

**Privileged (running as root).** The overlay covers the entire root filesystem and commands run
chrooted into the merged view, with a list of sensitive paths (`~/.ssh`, `~/.aws`, `/etc/shadow`, …)
hidden by overlay whiteouts. If invoked via `sudo`, commands drop back to the original user.

### Limitation worth knowing

In unprivileged mode the overlay covers **only the directory you sandboxed**. Writes to paths
outside it — `cd /tmp && rm something`, or writing to your home directory by absolute path — hit the
real filesystem and are not captured or rolled back. Sensitive-path hiding is likewise a
privileged-mode feature. Run as root if you need the whole filesystem covered.

## Layout

```
start_shell.py        entry point
start.sh              venv wrapper around start_shell.py
shell/                interactive prompt loop and the diff renderer
sandbox/              Sandbox interface and the overlayfs implementation
llm/                  translator façade and the OpenAI adapter
prompt_builder/       system prompt, few-shots, plan JSON schema, allowlists
```
