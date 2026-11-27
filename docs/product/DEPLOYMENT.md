# Deployment

For a full live agent workspace, run egctl serve on a host with Docker, a built DSH binary and a configured DeepSeek credential. The workspace preflight reports what the server process can access. The browser and sandbox do not need the DeepSeek credential.

## Windows Docker permissions

If Docker works in PowerShell but EpochGate reports DENIED, the project virtual environment may have inherited a Low integrity label. Its Python can then be denied access to the Docker Desktop named pipe. Use the Windows launcher to install the locked runtime in a separate, non-editable environment under `%LOCALAPPDATA%\EpochGate\docker-runtime` and probe Docker with that interpreter.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/start-windows-docker.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/start-windows-docker.ps1 -DataDir .epochgate
```

Use the data directory and config from the previous service when switching. The launcher starts a foreground service after the probe succeeds. Stop the old service first, or use `-Port` with a separate data directory to run a parallel instance. After pulling an update, stop the service and pass `-RefreshRuntime` to reinstall the non-editable package.

## Offline deployment

For an offline container deployment use docker compose -f deploy/compose.yaml up --build on a Docker host supporting host networking. Open http://127.0.0.1:8765. The app binds loopback and stores data in the named volume. Read-only mounts provide the experiment code, frozen archive, repository metadata, plugin marker and sample. Stop with docker compose -f deploy/compose.yaml down. The live agent workspace uses the host installation described above.

For an isolated demonstration start egctl serve --demo-mode --data-dir .epochgate. Its data lives under .epochgate/demo-mode. Use a separate configured port if the main console is running. In the isolated console use Runs → clear local runs to reset demonstration records.
