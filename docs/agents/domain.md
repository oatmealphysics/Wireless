# Domain Docs

This is a single-context repository for a wireless capability program.

## Before exploring

- Read `GLOSSARY.md` and use its canonical terms.
- Read the ADRs under `docs/adr/` that affect the work area.
- If a required concept is missing, use the domain-modeling workflow rather
  than silently inventing a synonym.

## Consumer rules

- Do not use words listed under `_Avoid_` as synonyms for canonical terms.
- Keep `GLOSSARY.md` free of implementation details.
- Surface an ADR conflict explicitly instead of silently overriding it.
- Create an ADR only for a hard-to-reverse, surprising decision that resulted
  from a real trade-off.

## Layout

```text
/
├── GLOSSARY.md
└── docs/
    └── adr/
        └── 0001-example-decision.md
```
