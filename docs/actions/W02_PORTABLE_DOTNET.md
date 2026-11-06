# W02 — native portable .NET multi-job DAG (candidate)

This change permits the existing `ubuntu-24.04-x64,windows-2022-x64` host
matrix **only when every native node is a reviewed .NET provider**. Both host
profiles must independently admit their .NET source/provider plans; Windows
nodes use the Windows-reviewed plan rather than reusing the Linux plan.

The graph digest includes the *two distinct host provider plan hashes*, and
cross-host edges are never generated. Existing non-.NET mixed Windows/POSIX
workflows remain prohibited except for the previously qualified C++ path.
Windows-only .NET projects do not acquire Linux eligibility. .NET 6 EOL
compatibility opt-in remains mandatory; no .NET migration is inferred.

Source regression:

    python3 -B -m pytest -q tests/test_w02_portable_dotnet.py tests/test_rm09_dotnet.py tests/test_rm07_toolchain_profiles.py

This is **not** evidence of 58 hosted test executions, remote artifact
attestation or a production-ready release workflow. The consumer's 58-test
workflow remains active, and the native 12-node source graph is separately
qualified before hosted node execution is enabled.
