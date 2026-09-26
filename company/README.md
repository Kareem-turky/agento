# company/

Per-installation configuration supplied by whoever deploys the product
(`config/`, `policies/`, `operating_model/`). Data and configuration only — no
code specific to any company is committed to this repository.

- `operating_model/operating-model.example.yaml` — a **non-production example** of the
  Company Operating Model (`app.company.operating_model.CompanyOperatingModel`) with
  generic placeholder data. The application does not load it; tests validate it. It must
  never contain secrets, credentials, prompts or URLs.
- `config/`, `policies/` — empty placeholders.
