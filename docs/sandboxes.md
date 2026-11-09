# Sandboxes

A sandbox runs the model's file and shell tools somewhere other than the host
process. moka supports podman, docker, and bubblewrap.

Sandboxes are optional. Without one, tools run in-process in your workspace and
rely on the role's permission settings.

## How sandboxes are scoped

Sandbox definitions are files. They can be:

- **Global**: usable from any project, in
  `~/.config/moka/sandboxes/<name>.toml`
- **Project-local**: usable from one project, under
  `~/.config/moka/projects/<name>_<hash>/sandboxes/`

The active sandbox is selected per project. moka never writes sandbox files into
your repository.

On first run, moka seeds a global `bubblewrap` sandbox if the global sandbox
folder does not exist yet.

## Quick start with bubblewrap

bubblewrap uses host binaries with a reduced view of the filesystem. It needs no
image and no build step.

```text
/sandbox start bubblewrap
```

If no sandbox is running, `/sandbox` opens a picker.

Check the status bar: `{sandbox}` should show the active sandbox. Run
`/sandbox stop` to return to in-process tools.

## Container sandbox

For podman or docker, create a sandbox and give it an image or a Dockerfile.

```text
/sandbox create dev docker
/sandbox init dev python
/sandbox build dev
/sandbox start dev
```

The `init` step writes a starter Containerfile/Dockerfile next to the sandbox
file. The image must provide `python3`, because moka runs its tool worker inside
the container.

Choose the runtime you want:

```text
/sandbox create dev podman
/sandbox create dev docker
```

## Everyday commands

| Command | What it does |
|---|---|
| `/sandbox` | Pick a sandbox or show the subcommands |
| `/sandbox start [name]` | Activate a sandbox |
| `/sandbox stop` | Deactivate it and use in-process tools |
| `/sandbox restart` | Restart the active sandbox from its current image |
| `/sandbox terminal` | Open a shell inside the active sandbox |
| `/sandbox build <name>` | Build the sandbox image from its Dockerfile |
| `/sandbox edit [name]` | Edit a sandbox's file |
| `/sandbox create <name> <type> [global\|local]` | Create a new definition |
| `/sandbox path <name>` | Print the sandbox file's path |
| `/sandbox remove <name>` | Delete a sandbox definition |

## Settings

A sandbox file can set:

- `type`: `podman`, `docker`, or `bubblewrap`
- `description`: text shown in the picker
- `image` and `dockerfile`: container image and build recipe
- `network`: allow networking; off by default
- `run_args`: extra runtime arguments
- `instructions`: guidance sent to the model while this sandbox is active

For container sandboxes, `/sandbox build` builds the image. moka never builds an
image automatically, so a changed Dockerfile needs a build and then a restart.

## Sandboxes and permissions

A sandbox changes where tools execute; it does not change whether they are
allowed. The role's `no` / `ask` / `yes` setting still applies before a tool is
dispatched.

To require a sandbox for a role, add this to the role file:

```toml
require_sandbox = true
```

Then activate a sandbox before starting a conversation with that role.

## Limits

- A container sandbox sees the workspace and the tool worker, not your whole
  home directory.
- Networking is off unless `network = true`.
- A bubblewrap sandbox has a limited set of host paths and no host environment.
- The sandbox runtime is the isolation boundary. moka does not add a command
  allowlist or path policy inside it.

For the security model, see [SECURITY.md](../SECURITY.md).
