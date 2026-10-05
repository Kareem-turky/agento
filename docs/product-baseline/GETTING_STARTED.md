# Getting Started

> **Status:** Supplemental onboarding checklist. The `Kareem-turky/agento` repository contains application code and manifests, but their exact commands were not independently executed while preparing this document.

## What you can do in this workspace

- Read the approved product and architecture decisions.
- Review the verified FulFly API discovery.
- Review the intended MVP workflow and acceptance criteria.
- Use these documents as implementation contracts.

Use the repository root README and checked-in manifests as the current authority for installation and startup. This document records the verification standard expected from those instructions.

## Required repository artifacts

The implementation repository must provide:

- Python project manifest and lock file.
- FastAPI application entry point.
- Next.js project manifest and lock file.
- Database schema and migration configuration.
- Dockerfiles and a local compose file.
- An example environment file containing names only, never secrets.
- Test commands and fixtures.
- Seeded demo data for the first read-only workflow.

## Expected onboarding flow

1. Install the documented versions of Docker and the language runtimes.
2. Copy the example environment file to a local ignored environment file.
3. Add development-only secrets through the approved secret mechanism.
4. Start PostgreSQL and Redis.
5. Run database migrations.
6. Seed internal demo data.
7. Start the backend and frontend.
8. Run unit, integration, policy, and workflow tests.
9. Execute the daily operations workflow against demo data.
10. Confirm that the report, audit records, and metrics are created.

## First implementation milestone

Before connecting live FulFly credentials, the system should support the full daily operations workflow using deterministic demo fixtures. This proves domain mapping, policy enforcement, reporting, audit logging, and failure handling without exposing customer data.

## Rules for credentials

- Never commit credentials or access tokens.
- Never paste secrets into prompts, logs, fixtures, screenshots, or reports.
- Use distinct development and production credentials.
- Redact phone numbers and customer identifiers in logs.
- Rotate a credential immediately if it is accidentally exposed.

## Completion condition for this document

Reconcile this checklist with the repository's exact, tested commands. Every command must be executed successfully from a clean checkout before being marked verified here.
