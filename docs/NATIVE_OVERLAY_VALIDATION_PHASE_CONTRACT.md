# Native overlay validation phase contract

An explicit validation request (`--validate`, `--validate-only`, `--defer-validate`, or `--static-validate`) **requests execution of applicable declared validation**. It does **not** override a manifest that explicitly sets `validation.restore`, `validation.build`, and/or `validation.test` to `false`.

For a non-exclusive native validation declaration, disabled built-in phases become the corresponding skip flags whether validation was auto-required by the artifact or explicitly requested by the CLI. This includes an overlay declaring `validation.tests` for Python-only suites or a checksummed `validation.script`: it must not unexpectedly launch an Android Gradle pipeline.

If the manifest declares an **exclusive** `validation.tasks` plan or the operator explicitly supplies a supported `--task` plan, those tasks are the selected execution plan. Built-in phase booleans are not translated into skip flags for that plan. Existing explicitly supplied CLI `--skip-*` flags are preserved and validated by the native task/skip conflict guard; the defaulting layer must never silently clear them.

Explicit `--no-validate`, preview-only, and non-overlay commands remain read-only with regard to these defaults. A required validation policy must still run a real validator or required tests; zero-check success is prohibited.

This changes argument normalization only. It does not add an alternate validation engine, alter transaction/rollback authority, grant remote delivery approval, qualify device/release work, or execute Gradle locally. Regression coverage: `tests/test_validation_manifest_skip_contract.py`.
