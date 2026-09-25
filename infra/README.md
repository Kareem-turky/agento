# infra/

Infrastructure definitions beyond local development (future).
Local PostgreSQL + pgvector and Redis live in the root `docker-compose.yml`;
`infra/postgres/init/` holds database bootstrap scripts mounted by that file.
