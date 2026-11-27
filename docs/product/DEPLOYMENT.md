# Deployment

For a full live agent workspace, run egctl serve on a host with Docker, a built DSH binary and a configured DeepSeek credential. The workspace preflight reports what the server process can access. The browser and sandbox do not need the DeepSeek credential.

For an offline container deployment use docker compose -f deploy/compose.yaml up --build on a Docker host supporting host networking. Open http://127.0.0.1:8765. The app binds loopback and stores data in the named volume. Read-only mounts provide the experiment code, frozen archive, repository metadata, plugin marker and sample. Stop with docker compose -f deploy/compose.yaml down. The live agent workspace uses the host installation described above.

For an isolated demonstration start egctl serve --demo-mode --data-dir .epochgate. Its data lives under .epochgate/demo-mode. Use a separate configured port if the main console is running. In the isolated console use Runs → clear local runs to reset demonstration records.
