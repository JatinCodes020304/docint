# Railway PostgreSQL

The production deployment is designed to use Railway managed PostgreSQL.

## Setup

1. Add a PostgreSQL database service in the same Railway project.
2. In the `docint` service Variables, add a reference variable:
   `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   (select the reference from Railway if the service has another name).
3. Redeploy `docint`.

The app accepts `postgresql://` and legacy `postgres://` URLs. The PostgreSQL driver is included in `backend/requirements.txt`. SQLAlchemy creates missing tables during startup.

## Persistence proof

Process one document, confirm it appears in the dashboard, restart/redeploy `docint`, and verify the same record remains.

Never commit the database password or expanded connection URL.
