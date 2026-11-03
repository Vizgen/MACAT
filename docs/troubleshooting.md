# Troubleshooting

Most failed starts are one of three things: the port is already taken, the
server was started outside the repository root, or the Python environment is not
the one the app was installed into. The commands below are for Linux and macOS,
with the Windows (Command Prompt) equivalent where it differs; the README's
install section lists what has been tested.

## 1. Check the port before starting

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8700/api/jobs
pgrep -af 'uvicorn.*8700'
```

- `200`: the app is already running. Use it; do **not** start a second process
  on the same port.
- `000` with no `pgrep` match: the port is free; start normally.
- `000` *with* a `pgrep` match: a stale process is holding the port; see 2.

On Windows, `netstat -ano | findstr :8700` lists anything listening on the port
(no output means it is free); `curl.exe` works as above.

## 2. `[Errno 98] ... address already in use`

On Windows the same error reads `[Errno 10048] error while attempting to bind on
address ('127.0.0.1', 8700): only one usage of each socket address
(protocol/network address/port) is normally permitted`.

The message appears *after* a startup banner:

```text
INFO:     Started server process [1255000]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
ERROR:    [Errno 98] error while attempting to bind on address ('0.0.0.0', 8700): address already in use
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
```

`Application startup complete.` does not mean the server came up: read the last
lines, not the first. Retrying is safe; a failed start leaves nothing to clean
up.

Find what owns the port, then either reuse it or free it:

```bash
ss -lptn 'sport = :8700'          # owning PID/command (Linux)
lsof -nP -iTCP:8700 -sTCP:LISTEN  # same (Linux and macOS)
```

On Windows, the PID is the last column of `netstat`, and `tasklist` names it:

```bat
netstat -ano | findstr :8700
tasklist /FI "PID eq 8752"
```

The usual owners:

- **`python.exe` / `uvicorn`:** an earlier MACAT. Open
  <http://127.0.0.1:8700> and use it, or stop it as below.
- **`ssh`:** an SSH tunnel to a remote MACAT (see 6). Close it, or use another
  port.
- **`Code.exe` (VS Code):** VS Code connected to a remote machine (Remote-SSH)
  forwards ports in use there to the same port on your computer, so
  `127.0.0.1:8700` reaches the remote machine, not yours. Stop it in VS Code's
  **Ports** panel (right-click → **Stop Forwarding Port**), or set
  `remote.autoForwardPorts` to `false`; or just use another port.

Check the owning user and PID before doing anything. If it is your own stale
server, stop it gracefully first:

```bash
pkill -f 'uvicorn web.server:app.*8700'
sleep 2
pgrep -af 'uvicorn.*8700' || echo 'port 8700 free'
```

Only if it will not exit:

```bash
pkill -9 -f 'uvicorn web.server:app.*8700'
```

On Windows, stop an identified MACAT with `taskkill /PID <PID>` (add `/F` only
if it will not exit).

Never kill a PID you have not identified. If the port belongs to someone else,
run on a different port instead. Pick one nothing else is likely to use:
`8701` is the usual tunnel fallback (see 6), so it is often taken too.

```bash
python -m uvicorn web.server:app --host 127.0.0.1 --port 18700
```

The port comes only from `--port`, not from the environment.

## 3. The server will not import, or crashes at startup

- `ModuleNotFoundError` for `fastapi` or the atlas stack: the shell is not in
  the environment the app was installed into. Activate it, or call that
  environment's interpreter by its full path (needed under `nohup`, `tmux` or
  cron).
- `Error loading ASGI app. Could not import module "web.server"`: the server was
  started outside the repository root. `cd` to the repository root first.

## 4. The page does not change after an update

The server serves the prebuilt page in `web/frontend/dist/`, which updates with
the code. Hard-reload the browser (Ctrl-Shift-R, or Cmd-Shift-R on macOS). If
you changed the frontend yourself, see "For developers" below.

## 5. A server that stays up for days

For a long-lived server, start it with single-threaded math libraries, and
inside `tmux` (or `screen`) so it survives closing the terminal:

```bash
OPENBLAS_NUM_THREADS=1 \
OMP_NUM_THREADS=1 \
MKL_NUM_THREADS=1 \
NUMEXPR_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 \
python -m uvicorn web.server:app --host 127.0.0.1 --port 8700
```

```bash
tmux new-session -s macat   # detach with Ctrl-b d
tmux attach -t macat        # reattach later
```

## 6. Running on a remote machine

Start the server there with `--host 127.0.0.1`, and reach it through an SSH
tunnel from your own computer:

```bash
ssh -L 8700:127.0.0.1:8700 <user>@<remote-host>   # then open http://127.0.0.1:8700
```

If your local port is busy, map a different local port and keep the remote one
at `8700`:

```bash
ssh -L 8701:127.0.0.1:8700 <user>@<remote-host>   # then open http://127.0.0.1:8701
```

`bind: Address already in use` from `ssh` means the *local* port is occupied,
usually by an earlier tunnel: `lsof -nP -iTCP:8701 -sTCP:LISTEN`.

## 7. S3 paths

- **"could not reach S3 from this server ... not your path or your access"
  (503):** the network to S3 failed, even after one retry. Try again later.
- **Access denied:** the AWS CLI's credentials (`aws configure`, or
  `AWS_PROFILE`) cannot read that path.
- **Archived:** the object is in Glacier or Deep Archive. Restore it in S3 first;
  the app never requests archived objects.

## 8. The data folder

- **"No sections found (expected <experiment>/region_*/ folders)":** the folder
  must hold experiment folders, each holding `region_*` folders with
  `cell_metadata.csv` and `cell_by_gene.csv`. One level is searched, no deeper.
  Point the data folder at the folder *above* your experiments.
- **New experiments do not appear:** press **Rescan**. The folder is only
  scanned when set or rescanned.
- **"can only be viewed or set on the computer MACAT runs on":** you reached the
  server over the network. Use an SSH tunnel (section 6), or set
  `MACAT_DATA_DIR` where the server is started.
- **No Change button:** the folder is set by `MACAT_DATA_DIR` for this server.

## 9. Windows

- **A checkout inside OneDrive** (or another synced folder): jobs are written to
  `pipeline_output\` in the checkout, and OneDrive syncs every output file and
  can lock one while MACAT writes it. Keep the outputs elsewhere:
  `set ALLEN_OUT_ROOT=C:\MACAT_output` in the same Command Prompt before starting.
- **`←[32mINFO←[0m:` in the log:** the console is not showing colour codes. It is
  harmless; Windows Terminal shows the colours.
- **Activating a venv:** `.venv\Scripts\activate`, not `source .venv/bin/activate`.

## For developers: the frontend build

Users never build the frontend. These apply only after changing
`web/frontend/src`:

- **Node version:** Vite needs Node >= 20.19. Check with `node --version`; on
  Linux and macOS, `nvm install 20 && nvm use 20`.
- **`npm ci` fails:** run it inside `web/frontend/`. It needs network access to
  the npm registry, and installs exactly what `package-lock.json` lists.
  Vulnerability warnings from `npm ci` are covered in
  [`development_testing.md`](development_testing.md).
- **Your change does not show:** rebuild (`npm run build` in `web/frontend/`),
  then hard-reload. Commit the rebuilt `dist/` with the source change;
  `node tests/test_frontend_dist.mjs` fails if you forget.
