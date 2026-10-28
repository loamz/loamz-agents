# Loamz Agents deployment

The standalone Compose cell runs the Agents server with PostgreSQL, Redis and Keycloak. Its file store uses a local folder when no Data URL is configured. The shared Keycloak realm remains in `keycloak/`.

Prepare the local environment with `deploy/compose/gen-env.sh`, then use `make cell` or `make cell-dev`. To connect to an existing Loamz Data instance, set `AGENTS_DATA_URL` to the address reachable from the containers and run Compose with both the `agents` and `with-data` profiles. The latter checks the Data endpoint; it does not start a Data service. Run the e2e script with the same URL in its environment to include Data checks.

Build `deploy/docker/loamz-agents.Dockerfile` from the repository root. Image release workflows target amd64 and arm64. The inherited Helm and Terraform files have not yet been split for Agents; do not use them as standalone deployment instructions.
