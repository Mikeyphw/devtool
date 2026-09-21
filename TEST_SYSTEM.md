# Devtool first-class test system

Devtool treats tests as semantic `TestSpec` objects and records canonical `TestResult` objects independently of the framework that executed them.

## Evidence precedence

The authoritative order is:

1. `devtool.test/v1` structured protocol events
2. structured framework reports (JUnit XML, TRX, Go/Node machine output)
3. known framework stdout parsers
4. process exit status as the last-resort signal

Higher-quality evidence replaces lower-quality evidence for the same stable suite/case identity; it must not be added as a duplicate test.

## Compatibility

Legacy `--task`, artifact `validation.script`, existing stdout parsers, and old run-history shapes remain compatibility inputs. Canonical results preserve passed, failed, error, skipped, cancelled, xfail, and xpass; the historical four counters are projections only.

`pytest`, Gradle/JUnit, .NET, Go, Cargo/Rust, Maven/Surefire, and Node (Jest/Vitest/TAP) all converge on the same result/UI/history model. Artifact `validation.tests`, repository test declarations, workflows, the `devtool-test` helper, and the Python testing SDK use the same contract.

## Release seal

`devtool.test_system_seal.assert_test_system_sealed()` verifies protocol version, evidence precedence, history schema, runner registration, and rich-status support. The TSYS-06 validation gate combines that contract with regression coverage for the complete TSYS stack.
