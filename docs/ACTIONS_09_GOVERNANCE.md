# ACTIONS-09 — guided release setup and CI governance

The ACTIONS-08 verifier remains the **only** release qualification authority.
ACTIONS-09 makes protected refs, GitHub secrets, and an independent private
policy easier to provision. It does not publish a release or call a debug APK
qualified.

## One guided workflow in Termux

1. Install and authenticate `gh` (`pkg install gh` and `gh auth login`), and
   ensure a Java JDK with `keytool` is installed. Push the exact release source
   commit to `main`. The account must have Actions secrets and repository
   ruleset administration permissions.
2. Reuse **Devtool's existing Android signing system**. Your production
   release identity should already be registered in the private LIFE signing
   registry and may be selected in `[targets.android_hud.android.signing.release]`
   by `mode = "devtool"` and `identity_id = "..."`. Devtool reads the keystore,
   alias, certificate SHA-256 and secret provider from that identity; ACTIONS-09
   does **not** generate keys, store a second signing profile, or ask for a new
   signing password. You can inspect it with `devtool android signing inspect`.
   With one eligible durable LIFE Android release signer, identity selection is
   automatic; with multiple, pass `--signing-identity ID`. Use `--signing-target`
   to select an Android target other than `android_hud`.
3. Preview without requesting credentials or changing remote state:

   ```sh
   devtool -r "$HOME/Code/devtool" actions release setup
   ```

4. Explicitly provision GitHub from the already-authorized Devtool signer:

   ```sh
   devtool -r "$HOME/Code/devtool" actions release setup --yes
   ```

   This bridges the existing local signing secret provider to the four GitHub
   Actions secret names required by ACTIONS-08. Devtool still needs `gh` access,
   and you must intentionally approve exporting that signing key material to
   GitHub; it never copies passwords or the keystore into repository files.
   It rejects missing/ambiguous local signing identities rather than silently
   minting a new signing key. If all four GitHub secret names already exist,
   they are preserved unchanged; their values cannot be checked by GitHub.
   A partial existing secret set blocks setup to avoid mixing keys. Use
   `--rotate-secrets --yes` only when deliberately replacing all four with
   values derived from Devtool’s selected signer.

5. Audit GitHub's visible readiness (nonzero exit code if incomplete):

   ```sh
   devtool -r "$HOME/Code/devtool" actions release readiness --json
   ```

   This checks GitHub workflow/ref identity, current remote source SHA, branch
   protection, **names** of all four required Actions secrets, and the private
   owner's policy. GitHub intentionally never reveals secret contents, so
   name presence cannot prove that passwords or keystore bytes are correct.

The default private policy is `$HOME/.config/devtool/release-policy.json`.
The command creates it outside the Git tree with mode 0600 and pins the exact
GitHub source SHA and public signing certificate fingerprint independently checked by the existing AndroidSigningManager. An existing divergent policy is **never overwritten**. Review it
and intentionally move/replace it yourself if approving a new source commit
or changing the signing identity.

If the branch is not protected, the explicit `--yes` operation creates a new
additive active GitHub ruleset for that one branch, denying force-pushes and
branch deletion; it never PUTs or rewrites existing branch protection. GitHub
plans, permissions, or organization policy may reject ruleset creation. If
so, Devtool stops without claiming protection and the maintainer must use
GitHub Settings → Rules → Rulesets. A production team may additionally
require pull-request reviews, status checks and restricted deployment access.

New or explicitly rotated secrets are uploaded with `gh secret set` **through stdin** rather than
shell arguments, repository files or debug logs. Keystore and passwords are
not copied into the policy or repository. Partial remote setup may require a
retry: Devtool cannot roll back GitHub secrets or a newly created ruleset.

## Final qualification

1. Dispatch a **release** variant only from the protected branch.
2. Wait for actual GitHub completed success, signed package upload and GitHub
   provenance attestation. A dispatch accepted response is not success.
3. Register the artifact using `devtool actions download --run-id ... --artifact-id ...`.
4. Verify with `devtool actions release verify --run-id ... --artifact-id ...
   --policy "$HOME/.config/devtool/release-policy.json" --json`.

Only this final verification may return `qualified_release: true` and only
for the exact verified package/provenance. Setup/readiness always returns
`qualified_release: false`. The verifier does not approve publishing, grant
store permissions, sign locally, deploy to devices, or replace mobile testing.

## Safety and limitations

- Setup must be run by the human maintainer with explicit `--yes`. Preview
  never prompts for signing passwords or mutates GitHub.
- An existing private trust anchor cannot be overwritten automatically.
- Ref changes or unpushed commits block provisioning. Exact commit identity
  matters more than a mutable branch name.
- Setup never retrieves an existing secret value or certifies it.
- Release qualification requires JDK/Android inspection tools and `gh`
  attestation verification, plus supported GitHub artifact attestations.
- V1 tests are offline and use mocks for GitHub calls; live provisioning and
  live signed release qualification must be performed by the maintainer.
