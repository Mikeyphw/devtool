package io.github.mikeceead.devtoolhud;

import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

final class RootCommandBroker {
    private static final int MAX_OUTPUT = 2 * 1024 * 1024;
    private static final long TIMEOUT_SECONDS = 35L;
    private static final long MUTATION_TIMEOUT_SECONDS = 75L;
    private static final long NETWORK_TIMEOUT_SECONDS = 150L;
    private static final String TERMUX_PREFIX = "/data/data/com.termux/files/usr";
    private static final String TERMUX_HOME = "/data/data/com.termux/files/home";

    private final Context context;
    private final CancellationToken cancellation;
    private final String operationId;

    RootCommandBroker(Context context) {
        this(context, null, "");
    }

    RootCommandBroker(Context context, CancellationToken cancellation) {
        this(context, cancellation, "");
    }

    RootCommandBroker(Context context, CancellationToken cancellation, String operationId) {
        this.context = context.getApplicationContext();
        this.cancellation = cancellation;
        this.operationId = safeOperationId(operationId);
    }

    boolean available() {
        CommandResult result = executeRoot("id -u", 5L);
        return result.exitCode == 0 && result.text().trim().equals("0");
    }

    RepositoryInspection inspect(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if [ ! -d \"$repo\" ]; then printf '__DEVTOOL_ERROR__\\nmissing\\n'; exit 3; fi; "
                + "if ! git -C \"$repo\" rev-parse --is-inside-work-tree >/dev/null 2>&1; then "
                + "printf '__DEVTOOL_ERROR__\\nnot_git\\n'; exit 4; fi; "
                + "printf '__DEVTOOL_ROOT__\\n'; git -C \"$repo\" rev-parse --show-toplevel; "
                + "printf '__DEVTOOL_STATUS__\\n'; git -C \"$repo\" status --porcelain=v2 --branch --untracked-files=normal; "
                + "printf '__DEVTOOL_REMOTE__\\n'; git -C \"$repo\" remote get-url origin 2>/dev/null "
                + "|| { first=$(git -C \"$repo\" remote | head -n 1); [ -z \"$first\" ] || git -C \"$repo\" remote get-url \"$first\"; } 2>/dev/null || true; "
                + "printf '__DEVTOOL_PROJECT__\\n'; root=$(git -C \"$repo\" rev-parse --show-toplevel); "
                + "if [ -f \"$root/app/src/main/AndroidManifest.xml\" ] || find \"$root\" -maxdepth 5 -path '*/src/main/AndroidManifest.xml' -print -quit 2>/dev/null | grep -q .; then echo android; "
                + "elif [ -f \"$root/settings.gradle\" ] || [ -f \"$root/settings.gradle.kts\" ] || [ -f \"$root/gradlew\" ]; then echo gradle; "
                + "elif find \"$root\" -maxdepth 2 -name '*.sln' -print -quit 2>/dev/null | grep -q .; then echo dotnet; "
                + "elif [ -f \"$root/pyproject.toml\" ] || [ -f \"$root/setup.py\" ]; then echo python; "
                + "elif [ -f \"$root/Cargo.toml\" ]; then echo rust; "
                + "elif [ -f \"$root/package.json\" ]; then echo node; "
                + "elif [ -f \"$root/CMakeLists.txt\" ]; then echo cmake; else echo generic; fi";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        return parseInspection(result);
    }

    GitWorkingTree workingTree(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if [ ! -d \"$repo\" ]; then echo 'Repository directory does not exist'; exit 3; fi; "
                + "git -C \"$repo\" status --porcelain=v2 -z --branch --untracked-files=all";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.truncated) return GitWorkingTree.parse(
                "Git status exceeded the bounded output limit".getBytes(StandardCharsets.UTF_8), 75, false);
        return GitWorkingTree.parse(result.bytes, result.exitCode, result.timedOut);
    }

    List<GitCommitRecord> commits(RepositoryLocation location, int requestedLimit) {
        int limit = Math.max(1, Math.min(250, requestedLimit));
        String repo = commandPath(location);
        String format = "%H%x00%h%x00%P%x00%an%x00%ae%x00%aI%x00%D%x00%s%x00";
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if ! git -C \"$repo\" rev-parse --is-inside-work-tree >/dev/null 2>&1; then exit 4; fi; "
                + "head=$(git -C \"$repo\" rev-parse HEAD 2>/dev/null || true); printf '%s\\0' \"$head\"; "
                + "git -C \"$repo\" log -n " + limit + " --date=iso-strict --pretty=format:"
                + shellQuote(format) + " 2>/dev/null || true";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.exitCode != 0 || result.timedOut || result.truncated) return new ArrayList<>();
        return GitCommitRecord.parse(result.bytes);
    }

    GitCommitDetail commitDetail(RepositoryLocation location, String hash) {
        GitCommitDetail detail = new GitCommitDetail();
        detail.hash = cleanHash(hash);
        if (detail.hash.isEmpty()) {
            detail.error = "Invalid commit hash";
            return detail;
        }
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" show --no-ext-diff --no-color --format=fuller "
                + "--stat --summary --name-status --find-renames --find-copies "
                + shellQuote(detail.hash);
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.timedOut) detail.error = "Commit details timed out";
        else if (result.exitCode != 0) detail.error = compact(result.text(), "Unable to read commit");
        else detail.text = result.text() + (result.truncated ? "\n\n[Output truncated at 2 MiB]" : "");
        return detail;
    }

    CommandResult diff(RepositoryLocation location, String relativePath, boolean staged,
                       boolean untracked) {
        String path = safeRelativePath(relativePath);
        if (path.isEmpty()) return CommandResult.error("Invalid repository path");
        String repo = commandPath(location);
        String payload;
        if (untracked) {
            payload = "repo=" + shellQuote(repo) + "; file=\"$repo\"/" + shellQuote(path) + "; "
                    + "git -C \"$repo\" diff --no-index --no-ext-diff --no-color --unified=3 "
                    + "-- /dev/null \"$file\"; code=$?; [ \"$code\" -eq 0 ] || [ \"$code\" -eq 1 ]";
        } else {
            payload = "repo=" + shellQuote(repo) + "; git -C \"$repo\" diff "
                    + (staged ? "--cached " : "")
                    + "--no-ext-diff --no-color --unified=3 -- " + shellQuote(path);
        }
        return execute(location, payload, TIMEOUT_SECONDS);
    }

    CommandResult stage(RepositoryLocation location, List<String> paths) {
        String arguments = pathArguments(paths);
        if (arguments.isEmpty()) return CommandResult.error("No valid files selected");
        String repo = commandPath(location);
        return execute(location, "repo=" + shellQuote(repo) + "; git -C \"$repo\" add -- " + arguments,
                TIMEOUT_SECONDS);
    }

    CommandResult unstage(RepositoryLocation location, List<String> paths) {
        String arguments = pathArguments(paths);
        if (arguments.isEmpty()) return CommandResult.error("No valid files selected");
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" restore --staged -- " + arguments
                + " 2>/dev/null || git -C \"$repo\" reset -q HEAD -- " + arguments
                + " 2>/dev/null || git -C \"$repo\" rm --cached --ignore-unmatch -- " + arguments;
        return execute(location, payload, TIMEOUT_SECONDS);
    }

    CommandResult discardWorktree(RepositoryLocation location, List<String> paths) {
        String arguments = pathArguments(paths);
        if (arguments.isEmpty()) return CommandResult.error("No valid tracked files selected");
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" restore --worktree -- " + arguments
                + " 2>/dev/null || git -C \"$repo\" checkout -- " + arguments;
        return execute(location, payload, TIMEOUT_SECONDS);
    }

    CommandResult stageAll(RepositoryLocation location) {
        String repo = commandPath(location);
        return execute(location, "repo=" + shellQuote(repo) + "; git -C \"$repo\" add -A",
                TIMEOUT_SECONDS);
    }

    CommandResult unstageAll(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" restore --staged :/ 2>/dev/null "
                + "|| git -C \"$repo\" reset -q HEAD -- 2>/dev/null "
                + "|| git -C \"$repo\" rm -r --cached --ignore-unmatch .";
        return execute(location, payload, TIMEOUT_SECONDS);
    }


    List<GitBranchRecord> branches(RepositoryLocation location) {
        String repo = commandPath(location);
        String format = "%(refname)%00%(refname:short)%00%(objectname)%00%(upstream:short)%00%(upstream:track,nobracket)%00%(HEAD)%00";
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if ! git -C \"$repo\" rev-parse --is-inside-work-tree >/dev/null 2>&1; then exit 4; fi; "
                + "git -C \"$repo\" for-each-ref --sort=refname --format=" + shellQuote(format + "local%00")
                + " refs/heads; "
                + "git -C \"$repo\" for-each-ref --sort=refname --format=" + shellQuote(format + "remote%00")
                + " refs/remotes";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.exitCode != 0 || result.timedOut || result.truncated) return new ArrayList<>();
        return GitBranchRecord.parse(result.bytes);
    }

    List<GitStashRecord> stashes(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" stash list "
                + "--format=" + shellQuote("%gd%x00%H%x00%aI%x00%gs%x00") + " 2>/dev/null || true";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.exitCode != 0 || result.timedOut || result.truncated) return new ArrayList<>();
        return GitStashRecord.parse(result.bytes);
    }

    CommandResult commit(RepositoryLocation location, String message) {
        String cleanMessage = safeCommitMessage(message);
        if (cleanMessage.isEmpty()) return CommandResult.error("Commit message is required");
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if ! git -C \"$repo\" diff --cached --quiet --; then :; else echo 'No staged changes to commit'; exit 65; fi; "
                + "if git -C \"$repo\" diff --name-only --diff-filter=U | grep -q .; then echo 'Resolve conflicts before committing'; exit 66; fi; "
                + "GIT_TERMINAL_PROMPT=0 git -C \"$repo\" commit -m " + shellQuote(cleanMessage);
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    CommandResult fetchAll(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "GIT_TERMINAL_PROMPT=0 git -C \"$repo\" fetch --all --prune --tags";
        return execute(location, payload, NETWORK_TIMEOUT_SECONDS);
    }

    CommandResult pullFastForward(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "branch=$(git -C \"$repo\" symbolic-ref --quiet --short HEAD) || { echo 'Cannot pull while HEAD is detached'; exit 67; }; "
                + "upstream=$(git -C \"$repo\" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) "
                + "|| { echo 'Current branch has no upstream'; exit 68; }; "
                + "GIT_TERMINAL_PROMPT=0 git -C \"$repo\" pull --ff-only";
        return execute(location, payload, NETWORK_TIMEOUT_SECONDS);
    }

    CommandResult push(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "branch=$(git -C \"$repo\" symbolic-ref --quiet --short HEAD) || { echo 'Cannot push while HEAD is detached'; exit 67; }; "
                + "if git -C \"$repo\" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then "
                + "GIT_TERMINAL_PROMPT=0 git -C \"$repo\" push; "
                + "else remote=$(git -C \"$repo\" remote | sed -n '/^origin$/p' | head -n 1); "
                + "[ -n \"$remote\" ] || remote=$(git -C \"$repo\" remote | head -n 1); "
                + "[ -n \"$remote\" ] || { echo 'Repository has no configured remote'; exit 69; }; "
                + "GIT_TERMINAL_PROMPT=0 git -C \"$repo\" push --set-upstream \"$remote\" HEAD; fi";
        return execute(location, payload, NETWORK_TIMEOUT_SECONDS);
    }

    CommandResult checkoutBranch(RepositoryLocation location, String branch, boolean remote) {
        String cleanBranch = safeRefName(branch);
        if (cleanBranch.isEmpty()) return CommandResult.error("Invalid branch name");
        String repo = commandPath(location);
        String payload;
        if (remote) {
            int slash = cleanBranch.indexOf('/');
            if (slash <= 0 || slash + 1 >= cleanBranch.length()) return CommandResult.error("Invalid remote branch");
            String local = cleanBranch.substring(slash + 1);
            payload = "repo=" + shellQuote(repo) + "; remote_branch=" + shellQuote(cleanBranch)
                    + "; local_branch=" + shellQuote(local) + "; "
                    + "git check-ref-format 'refs/remotes/'\"$remote_branch\" >/dev/null "
                    + "&& git check-ref-format --branch \"$local_branch\" >/dev/null "
                    + "|| { echo 'Invalid branch name'; exit 64; }; "
                    + "if git -C \"$repo\" show-ref --verify --quiet 'refs/heads/'\"$local_branch\"; then "
                    + "git -C \"$repo\" switch \"$local_branch\" 2>/dev/null || git -C \"$repo\" checkout \"$local_branch\"; "
                    + "else git -C \"$repo\" switch --track -c \"$local_branch\" \"$remote_branch\" 2>/dev/null "
                    + "|| git -C \"$repo\" checkout --track -b \"$local_branch\" \"$remote_branch\"; fi";
        } else {
            payload = "repo=" + shellQuote(repo) + "; branch=" + shellQuote(cleanBranch) + "; "
                    + "git check-ref-format --branch \"$branch\" >/dev/null || { echo 'Invalid branch name'; exit 64; }; "
                    + "git -C \"$repo\" switch \"$branch\" 2>/dev/null || git -C \"$repo\" checkout \"$branch\"";
        }
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    CommandResult createBranch(RepositoryLocation location, String branch) {
        String cleanBranch = safeRefName(branch);
        if (cleanBranch.isEmpty()) return CommandResult.error("Invalid branch name");
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; branch=" + shellQuote(cleanBranch) + "; "
                + "git check-ref-format --branch \"$branch\" >/dev/null || { echo 'Invalid branch name'; exit 64; }; "
                + "git -C \"$repo\" switch -c \"$branch\" 2>/dev/null || git -C \"$repo\" checkout -b \"$branch\"";
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    CommandResult stashIncludingUntracked(RepositoryLocation location, String message) {
        String cleanMessage = safeStashMessage(message);
        String repo = commandPath(location);
        String suffix = cleanMessage.isEmpty() ? "" : " -m " + shellQuote(cleanMessage);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "if git -C \"$repo\" diff --quiet -- && git -C \"$repo\" diff --cached --quiet -- "
                + "&& [ -z \"$(git -C \"$repo\" ls-files --others --exclude-standard | head -n 1)\" ]; then "
                + "echo 'Working tree has no changes to stash'; exit 65; fi; "
                + "git -C \"$repo\" stash push --include-untracked" + suffix;
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    CommandResult popStash(RepositoryLocation location, String reference) {
        String cleanReference = safeStashReference(reference);
        if (cleanReference.isEmpty()) return CommandResult.error("Invalid stash reference");
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; stash_ref=" + shellQuote(cleanReference) + "; "
                + "git -C \"$repo\" rev-parse --verify \"$stash_ref^{commit}\" >/dev/null 2>&1 "
                + "|| { echo 'Stash no longer exists'; exit 64; }; "
                + "git -C \"$repo\" stash pop \"$stash_ref\"";
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    CommandResult hardResetTracked(RepositoryLocation location) {
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "git -C \"$repo\" rev-parse --verify HEAD >/dev/null 2>&1 "
                + "|| { echo 'Repository has no commit to reset to'; exit 64; }; "
                + "git -C \"$repo\" reset --hard HEAD";
        return execute(location, payload, MUTATION_TIMEOUT_SECONDS);
    }

    GitSyncPreview syncPreview(RepositoryLocation location, int requestedLimit) {
        GitSyncPreview preview = new GitSyncPreview();
        int limit = Math.max(1, Math.min(50, requestedLimit));
        String repo = commandPath(location);
        String statePayload = "repo=" + shellQuote(repo) + "; "
                + "upstream=$(git -C \"$repo\" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) "
                + "|| { echo '__NO_UPSTREAM__'; exit 68; }; "
                + "counts=$(git -C \"$repo\" rev-list --left-right --count HEAD...@{upstream}) || exit $?; "
                + "printf '%s\n%s\n' \"$upstream\" \"$counts\"";
        CommandResult state = execute(location, statePayload, TIMEOUT_SECONDS);
        if (state.cancelled) {
            preview.error = "Preview cancelled";
            return preview;
        }
        String text = state.text().trim();
        if (text.contains("__NO_UPSTREAM__")) {
            preview.error = "Current branch has no upstream";
            return preview;
        }
        if (state.exitCode != 0 || state.timedOut) {
            preview.error = compact(text, "Unable to compare with upstream");
            return preview;
        }
        String[] lines = text.split("\\R", 3);
        if (lines.length < 2) {
            preview.error = "Git returned an incomplete upstream comparison";
            return preview;
        }
        preview.upstream = lines[0].trim();
        String[] counts = lines[1].trim().split("\\s+");
        if (counts.length >= 2) {
            try {
                preview.ahead = Integer.parseInt(counts[0]);
                preview.behind = Integer.parseInt(counts[1]);
            } catch (NumberFormatException ignored) {
                preview.error = "Git returned invalid divergence counts";
                return preview;
            }
        }
        preview.outgoing = rangeCommits(location, "@{upstream}..HEAD", limit);
        preview.incoming = rangeCommits(location, "HEAD..@{upstream}", limit);
        return preview;
    }

    RemoteDiagnostics remoteDiagnostics(RepositoryLocation location) {
        RemoteDiagnostics diagnostics = new RemoteDiagnostics();
        String repo = commandPath(location);
        String payload = "repo=" + shellQuote(repo) + "; "
                + "remote=$(git -C \"$repo\" remote get-url origin 2>/dev/null "
                + "|| { first=$(git -C \"$repo\" remote | head -n 1); [ -z \"$first\" ] || git -C \"$repo\" remote get-url \"$first\"; } 2>/dev/null || true); "
                + "printf 'REMOTE=%s\n' \"$remote\"; "
                + "command -v git >/dev/null 2>&1 && echo GIT=1 || echo GIT=0; "
                + "command -v ssh >/dev/null 2>&1 && echo SSH=1 || echo SSH=0; "
                + "[ -n \"${SSH_AUTH_SOCK:-}\" ] && echo AGENT=1 || echo AGENT=0; "
                + "[ -n \"${GIT_ASKPASS:-}${SSH_ASKPASS:-}\" ] && echo ASKPASS=1 || echo ASKPASS=0; "
                + "helper=$(git -C \"$repo\" config --get credential.helper 2>/dev/null || true); printf 'HELPER=%s\n' \"$helper\"; "
                + "for file in \"$HOME\"/.ssh/id_ed25519 \"$HOME\"/.ssh/id_ecdsa \"$HOME\"/.ssh/id_rsa; do [ -r \"$file\" ] && printf 'IDENTITY=%s\n' \"$file\"; done";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.cancelled) diagnostics.error = "Remote diagnostic cancelled";
        else if (result.exitCode != 0 || result.timedOut) diagnostics.error = compact(result.text(), "Unable to inspect remote configuration");
        for (String line : result.text().split("\\R")) {
            if (line.startsWith("REMOTE=")) diagnostics.remote = line.substring(7).trim();
            else if (line.equals("GIT=1")) diagnostics.gitAvailable = true;
            else if (line.equals("SSH=1")) diagnostics.sshAvailable = true;
            else if (line.equals("AGENT=1")) diagnostics.sshAgentAvailable = true;
            else if (line.equals("ASKPASS=1")) diagnostics.askPassConfigured = true;
            else if (line.startsWith("HELPER=")) diagnostics.credentialHelper = line.substring(7).trim();
            else if (line.startsWith("IDENTITY=")) diagnostics.identityFiles.add(line.substring(9).trim());
        }
        diagnostics.transport = remoteTransport(diagnostics.remote);
        diagnostics.host = remoteHost(diagnostics.remote);
        if (diagnostics.transport.equals("ssh") && !diagnostics.host.isEmpty() && diagnostics.sshAvailable) {
            String hostPayload = "host=" + shellQuote(diagnostics.host) + "; "
                    + "[ -r \"$HOME/.ssh/known_hosts\" ] && ssh-keygen -F \"$host\" -f \"$HOME/.ssh/known_hosts\" >/dev/null 2>&1";
            CommandResult known = execute(location, hostPayload, 10L);
            diagnostics.knownHost = known.exitCode == 0;
        }
        diagnostics.ready = diagnostics.gitAvailable && (diagnostics.remote.isEmpty()
                || diagnostics.transport.equals("ssh") && diagnostics.sshAvailable && diagnostics.knownHost
                    && (diagnostics.sshAgentAvailable || !diagnostics.identityFiles.isEmpty())
                || diagnostics.transport.equals("https") && (!diagnostics.credentialHelper.isEmpty() || diagnostics.askPassConfigured));
        addRemoteRemediation(diagnostics, location);
        return diagnostics;
    }

    private static void addRemoteRemediation(RemoteDiagnostics diagnostics, RepositoryLocation location) {
        if (!diagnostics.gitAvailable) {
            diagnostics.remediationSteps.add(location.environment.equals("termux")
                    ? "pkg install git" : "sudo apt install git");
            return;
        }
        if (diagnostics.remote.isEmpty()) {
            diagnostics.remediationSteps.add("git remote add origin <remote-url>");
            return;
        }
        if (diagnostics.transport.equals("ssh")) {
            if (!diagnostics.sshAvailable) {
                diagnostics.remediationSteps.add(location.environment.equals("termux")
                        ? "pkg install openssh" : "sudo apt install openssh-client");
            }
            if (!diagnostics.host.isEmpty() && !diagnostics.knownHost) {
                diagnostics.remediationSteps.add("mkdir -p ~/.ssh && chmod 700 ~/.ssh && ssh-keyscan -H "
                        + shellQuote(diagnostics.host)
                        + " >> ~/.ssh/known_hosts  # verify the fingerprint first");
            }
            if (!diagnostics.sshAgentAvailable && diagnostics.identityFiles.isEmpty()) {
                diagnostics.remediationSteps.add("ssh-keygen -t ed25519 -C \"devtool-android\"");
            } else if (!diagnostics.sshAgentAvailable) {
                diagnostics.remediationSteps.add("eval \"$(ssh-agent -s)\" && ssh-add ~/.ssh/id_ed25519");
            }
        } else if (diagnostics.transport.equals("https")
                && diagnostics.credentialHelper.isEmpty() && !diagnostics.askPassConfigured) {
            diagnostics.remediationSteps.add("git config --global credential.helper 'cache --timeout=3600'");
        }
    }

    private List<GitSyncCommit> rangeCommits(RepositoryLocation location, String range, int limit) {
        String repo = commandPath(location);
        String format = "%H%x00%h%x00%an%x00%aI%x00%s%x00";
        String payload = "repo=" + shellQuote(repo) + "; git -C \"$repo\" log -n " + limit
                + " --date=iso-strict --pretty=format:" + shellQuote(format) + " " + shellQuote(range)
                + " 2>/dev/null || true";
        CommandResult result = execute(location, payload, TIMEOUT_SECONDS);
        if (result.exitCode != 0 || result.timedOut || result.cancelled || result.truncated) return new ArrayList<>();
        return GitSyncCommit.parse(result.bytes);
    }

    List<RepositoryLocation> discoverRepositories(String environment, String chrootName,
                                                   String linuxUser) {
        RepositoryLocation scope = environment.equals("chroot")
                ? RepositoryLocation.chroot(chrootName, linuxUser, "/")
                : RepositoryLocation.termux("~/");
        String payload;
        if (scope.environment.equals("chroot")) {
            payload = "for root in /home /root; do [ -d \"$root\" ] || continue; "
                    + "find \"$root\" -xdev -mindepth 2 -maxdepth 7 "
                    + "\\( -name .cache -o -name node_modules -o -name .gradle -o -name build -o -name obj -o -name bin \\) -prune -o "
                    + "-type d -name .git -print0 2>/dev/null; done";
        } else {
            payload = "find \"$HOME\" -xdev -mindepth 2 -maxdepth 7 "
                    + "\\( -name .cache -o -name node_modules -o -name .gradle -o -name build -o -name obj -o -name bin \\) -prune -o "
                    + "-type d -name .git -print0 2>/dev/null";
        }
        CommandResult result = execute(scope, payload, TIMEOUT_SECONDS);
        List<RepositoryLocation> found = new ArrayList<>();
        if (result.exitCode != 0 && result.bytes.length == 0) return found;
        Set<String> seen = new LinkedHashSet<>();
        for (String git : splitNull(result.bytes)) {
            String path = git.endsWith("/.git") ? git.substring(0, git.length() - 5) : git;
            if (!seen.add(path)) continue;
            if (scope.environment.equals("chroot")) {
                found.add(RepositoryLocation.chroot(chrootName,
                        RepositoryLocation.inferUser(path), path));
            } else {
                found.add(RepositoryLocation.termux(path));
            }
            if (found.size() >= 250) break;
        }
        return found;
    }

    List<String> listDirectories(RepositoryLocation location) {
        String path = commandPath(location);
        String payload = "path=" + shellQuote(path) + "; [ -d \"$path\" ] || exit 3; "
                + "find \"$path\" -mindepth 1 -maxdepth 1 -type d "
                + "! -name .git ! -name .cache ! -name node_modules -print0 2>/dev/null";
        CommandResult result = execute(location, payload, 15L);
        List<String> values = splitNull(result.bytes);
        values.sort(String.CASE_INSENSITIVE_ORDER);
        return values;
    }

    List<String> discoverChrootNames() {
        LinkedHashSet<String> names = new LinkedHashSet<>();
        names.add("androidos");
        String payload = "export HOME=" + shellQuote(TERMUX_HOME) + " PREFIX=" + shellQuote(TERMUX_PREFIX)
                + " PATH=" + shellQuote(TERMUX_HOME + "/.local/bin:" + TERMUX_PREFIX + "/bin:/system/bin:/system/xbin") + "; "
                + "ctl=$(command -v chrootctl 2>/dev/null || true); [ -n \"$ctl\" ] || exit 0; "
                + "($ctl list --json 2>/dev/null || $ctl list 2>/dev/null || true)";
        CommandResult result = executeRoot(payload, 12L);
        String text = result.text();
        java.util.regex.Matcher json = java.util.regex.Pattern
                .compile("\\\"(?:name|id)\\\"\\s*:\\s*\\\"([A-Za-z0-9._-]+)\\\"")
                .matcher(text);
        while (json.find()) names.add(json.group(1));
        for (String line : text.split("\\R")) {
            String clean = line.trim();
            if (clean.matches("[A-Za-z0-9._-]+")) names.add(clean);
        }
        return new ArrayList<>(names);
    }

    boolean operationProcessAlive(String requestedOperationId) {
        String marker = safeOperationId(requestedOperationId);
        if (marker.isEmpty()) return false;
        String script = "marker=" + shellQuote(marker) + "; "
                + "for env in /proc/[0-9]*/environ; do [ -r \"$env\" ] || continue; "
                + "tr '\\0' '\\n' < \"$env\" 2>/dev/null | grep -Fqx \"DEVTOOL_ANDROID_OPERATION_ID=$marker\" "
                + "&& exit 0; done; exit 1";
        CommandResult result = new RootCommandBroker(context).executeRoot(script, 8L);
        return result.exitCode == 0 && !result.timedOut;
    }

    boolean terminateOperationProcess(String requestedOperationId) {
        String marker = safeOperationId(requestedOperationId);
        if (marker.isEmpty()) return false;
        String script = "marker=" + shellQuote(marker) + "; pids=''; "
                + "for env in /proc/[0-9]*/environ; do [ -r \"$env\" ] || continue; "
                + "if tr '\\0' '\\n' < \"$env\" 2>/dev/null | grep -Fqx \"DEVTOOL_ANDROID_OPERATION_ID=$marker\"; then "
                + "pid=${env#/proc/}; pid=${pid%/environ}; [ \"$pid\" = 1 ] || pids=\"$pids $pid\"; fi; done; "
                + "[ -n \"$pids\" ] || exit 1; kill -TERM $pids 2>/dev/null || true; sleep 1; "
                + "for pid in $pids; do [ -d \"/proc/$pid\" ] && kill -KILL \"$pid\" 2>/dev/null || true; done; exit 0";
        CommandResult result = new RootCommandBroker(context).executeRoot(script, 12L);
        return result.exitCode == 0 && !result.timedOut;
    }

    CommandResult execute(RepositoryLocation location, String payload, long timeoutSeconds) {
        String marked = operationId.isEmpty() ? payload
                : "export DEVTOOL_ANDROID_OPERATION_ID=" + shellQuote(operationId) + "; " + payload;
        DebugRecorder.info(context, "repo", "REPO_EXECUTOR_SELECTED", "RootCommandBroker",
                "", location.id(), operationId, "", "Repository executor selected",
                DebugRecorder.details("environment", location.environment, "chroot", location.chrootName,
                        "linux_user", location.linuxUser, "path", commandPath(location),
                        "timeout_seconds", timeoutSeconds));
        if (location.environment.equals("chroot")) {
            DebugRecorder.info(context, "repo", "CHROOT_EXECUTOR_SELECTED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Chroot executor selected",
                    DebugRecorder.details("chroot", location.chrootName, "linux_user", location.linuxUser,
                            "path", commandPath(location)));
            RootExecutionPlan plan = selectChrootExecutionPlan(location);
            DebugRecorder.info(context, "repo", "ROOT_EXECUTION_PLAN_SELECTED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Root/chroot execution plan selected",
                    DebugRecorder.details("environment", plan.environment, "chroot", plan.chrootName,
                            "linux_user", plan.linuxUser, "selected_shell", plan.selectedShell,
                            "user_switch_method", plan.userSwitchMethod, "issue_key", plan.issueKey,
                            "direct_root_fallback", plan.directRootFallback,
                            "rejected_shell_candidates", plan.rejectedShellCandidates,
                            "rejected_user_switch_candidates", plan.rejectedUserSwitchCandidates));
            if (!operationId.isEmpty()) {
                GitOperationTrace.executionPlanBuilt(context, location, operationId, plan);
                CommandResult preflight = executeRoot(chrootShell(location,
                        GitOperationTrace.preflightPayload(commandPath(location)), plan), preflightTimeout(timeoutSeconds));
                if (GitOperationTrace.preflightFailed(preflight)) {
                    GitOperationTrace.executorPreflightFailed(context, location, operationId, preflight);
                    return preflight;
                }
                GitOperationTrace.pathConfirmed(context, location, operationId);
                GitOperationTrace.gitBinaryConfirmed(context, location, operationId);
                GitOperationTrace.commandStarted(context, location, operationId);
            }
            return executeRoot(chrootShell(location, marked, plan), timeoutSeconds);
        }
        if (location.environment.equals("native-termux")) {
            RootExecutionPlan plan = RootExecutionPlan.termux();
            DebugRecorder.info(context, "repo", "TERMUX_EXECUTOR_SELECTED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Termux executor selected",
                    DebugRecorder.details("path", commandPath(location), "selected_shell", plan.selectedShell,
                            "user_switch_method", plan.userSwitchMethod));
            if (!operationId.isEmpty()) {
                GitOperationTrace.executionPlanBuilt(context, location, operationId, plan);
                CommandResult preflight = executeRoot(termuxShell(
                        GitOperationTrace.preflightPayload(commandPath(location))), preflightTimeout(timeoutSeconds));
                if (GitOperationTrace.preflightFailed(preflight)) {
                    GitOperationTrace.executorPreflightFailed(context, location, operationId, preflight);
                    return preflight;
                }
                GitOperationTrace.pathConfirmed(context, location, operationId);
                GitOperationTrace.gitBinaryConfirmed(context, location, operationId);
                GitOperationTrace.commandStarted(context, location, operationId);
            }
            return executeRoot(termuxShell(marked), timeoutSeconds);
        }
        RootExecutionPlan plan = RootExecutionPlan.root();
        DebugRecorder.info(context, "repo", "ROOT_EXECUTION_PLAN_SELECTED", "RootCommandBroker",
                "", location.id(), operationId, "", "Root execution plan selected",
                DebugRecorder.details("environment", plan.environment, "selected_shell", plan.selectedShell,
                        "user_switch_method", plan.userSwitchMethod));
        if (!operationId.isEmpty()) {
            GitOperationTrace.executionPlanBuilt(context, location, operationId, plan);
            CommandResult preflight = executeRoot(GitOperationTrace.preflightPayload(commandPath(location)),
                    preflightTimeout(timeoutSeconds));
            if (GitOperationTrace.preflightFailed(preflight)) {
                GitOperationTrace.executorPreflightFailed(context, location, operationId, preflight);
                return preflight;
            }
            GitOperationTrace.pathConfirmed(context, location, operationId);
            GitOperationTrace.gitBinaryConfirmed(context, location, operationId);
            GitOperationTrace.commandStarted(context, location, operationId);
        }
        return executeRoot(marked, timeoutSeconds);
    }

    private static long preflightTimeout(long timeoutSeconds) {
        return Math.max(5L, Math.min(12L, timeoutSeconds));
    }

    CommandResult repositoryDoctor(RepositoryLocation location, String payload, long timeoutSeconds) {
        DebugRecorder.info(context, "repo", "REPO_DOCTOR_EXECUTOR_SELECTED", "RootCommandBroker",
                "", location.id(), operationId, "", "Repository doctor executor selected",
                DebugRecorder.details("environment", location.environment, "chroot", location.chrootName,
                        "linux_user", location.linuxUser, "path", commandPath(location),
                        "timeout_seconds", timeoutSeconds));
        if (location.environment.equals("chroot")) {
            DebugRecorder.info(context, "repo", "REPO_DOCTOR_CHROOT_ROOT_PROBE_SELECTED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Repository doctor will inspect chroot before user switching",
                    DebugRecorder.details("chroot", location.chrootName, "linux_user", location.linuxUser,
                            "path", commandPath(location)));
            return executeRoot(chrootDoctorShell(location, payload), timeoutSeconds);
        }
        if (location.environment.equals("native-termux")) {
            return executeRoot(termuxShell(payload), timeoutSeconds);
        }
        return executeRoot(payload, timeoutSeconds);
    }

    private String termuxShell(String payload) {
        String quotedPayload = shellQuote("export HOME=" + TERMUX_HOME + " PREFIX=" + TERMUX_PREFIX
                + " PATH=" + TERMUX_HOME + "/.local/bin:" + TERMUX_PREFIX + "/bin:/system/bin:/system/xbin; " + payload);
        return "uid=$(cmd package list packages -U com.termux 2>/dev/null | sed -n 's/^package:com\\.termux uid://p' | head -n 1); "
                + "[ -n \"$uid\" ] || { echo 'Termux UID unavailable'; exit 69; }; "
                + "shell=" + shellQuote(TERMUX_PREFIX + "/bin/sh") + "; "
                + "if su -h 2>&1 | grep -q -- '-s'; then su -s \"$shell\" \"$uid\" -c " + quotedPayload + "; "
                + "else su \"$uid\" -c \"$shell -c " + escapeForDoubleQuotes(quotedPayload) + "\"; fi";
    }

    private RootExecutionPlan selectChrootExecutionPlan(RepositoryLocation location) {
        CommandResult result = executeRoot(chrootPlannerProbe(location), 8L, null);
        RootExecutionPlan plan = RootExecutionPlan.fromProbe(location, result.text(), result.exitCode, result.timedOut);
        if (!plan.usable) {
            DebugRecorder.warning(context, "repo", "ROOT_EXECUTION_PLAN_PROBE_FAILED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Root/chroot execution planner could not prove a clean plan",
                    DebugRecorder.details("environment", "chroot", "chroot", location.chrootName,
                            "linux_user", location.linuxUser, "exit_code", result.exitCode,
                            "timed_out", result.timedOut, "output_preview", compact(result.text(), "")));
        } else if (plan.directRootFallback || plan.userSwitchMethod.equals(RootExecutionPlan.METHOD_SU)) {
            DebugRecorder.warning(context, "repo", "ROOT_EXECUTION_PLAN_FALLBACK_SELECTED", "RootCommandBroker",
                    "", location.id(), operationId, "", "Root/chroot execution planner selected a fallback",
                    DebugRecorder.details("environment", "chroot", "chroot", location.chrootName,
                            "linux_user", location.linuxUser, "selected_shell", plan.selectedShell,
                            "user_switch_method", plan.userSwitchMethod,
                            "rejected_user_switch_candidates", plan.rejectedUserSwitchCandidates));
        }
        return plan;
    }

    private String chrootPlannerProbe(RepositoryLocation location) {
        String user = RepositoryLocation.clean(location.linuxUser);
        String inside = "kv(){ printf '" + RootExecutionPlan.markerPrefix() + "%s=%s\\n' \"$1\" \"$2\"; }; "
                + "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH; "
                + "kv planner_started 1; rejected_shells=''; selected_shell=''; selected_shell_name=''; "
                + "for candidate in zsh bash sh; do if command -v \"$candidate\" >/dev/null 2>&1; then path=$(command -v \"$candidate\" 2>/dev/null | head -n 1); "
                + "[ -z \"$selected_shell\" ] && selected_shell=\"$path\" && selected_shell_name=\"$candidate\"; kv ${candidate}_available 1; "
                + "else kv ${candidate}_available 0; rejected_shells=\"${rejected_shells}${rejected_shells:+,}${candidate}:missing\"; fi; done; "
                + "[ -n \"$selected_shell\" ] || { selected_shell=/bin/sh; selected_shell_name=sh; rejected_shells=\"${rejected_shells}${rejected_shells:+,}fallback:/bin/sh\"; }; "
                + "kv selected_shell \"$selected_shell\"; kv selected_shell_name \"$selected_shell_name\"; kv rejected_shell_candidates \"$rejected_shells\"; "
                + "linux_user=" + shellQuote(user) + "; kv linux_user \"$linux_user\"; rejected_switches=''; "
                + "if [ -z \"$linux_user\" ] || [ \"$linux_user\" = root ]; then kv user_exists 1; kv runuser_available 0; kv runuser_works 0; kv su_available 0; kv su_works 0; kv user_switch_method root; "
                + "else id \"$linux_user\" >/dev/null 2>&1 && kv user_exists 1 || kv user_exists 0; "
                + "if command -v runuser >/dev/null 2>&1; then kv runuser_available 1; if runuser -u \"$linux_user\" -- \"$selected_shell\" -c ':' >/dev/null 2>&1; then kv runuser_works 1; kv user_switch_method runuser; kv rejected_user_switch_candidates \"$rejected_switches\"; exit 0; else kv runuser_works 0; rejected_switches=\"runuser:failed\"; fi; else kv runuser_available 0; rejected_switches=\"runuser:missing\"; fi; "
                + "if command -v su >/dev/null 2>&1; then kv su_available 1; if su - \"$linux_user\" -s \"$selected_shell\" -c ':' >/dev/null 2>&1; then kv su_works 1; kv user_switch_method su; kv rejected_user_switch_candidates \"$rejected_switches\"; exit 0; else kv su_works 0; rejected_switches=\"${rejected_switches}${rejected_switches:+,}su:failed\"; fi; else kv su_available 0; rejected_switches=\"${rejected_switches}${rejected_switches:+,}su:missing\"; fi; "
                + "kv user_switch_method direct-root; kv rejected_user_switch_candidates \"$rejected_switches\"; fi";
        return "export HOME=" + shellQuote(TERMUX_HOME) + " PREFIX=" + shellQuote(TERMUX_PREFIX)
                + " PATH=" + shellQuote(TERMUX_HOME + "/.local/bin:" + TERMUX_PREFIX + "/bin:/system/bin:/system/xbin") + "; "
                + "ctl=$(command -v chrootctl 2>/dev/null || true); "
                + "[ -n \"$ctl\" ] || { echo 'chrootctl unavailable'; exit 69; }; "
                + "\"$ctl\" --name " + shellQuote(location.chrootName)
                + " exec -- /bin/sh -c " + shellQuote(inside);
    }

    private String chrootShell(RepositoryLocation location, String payload, RootExecutionPlan plan) {
        String user = RepositoryLocation.clean(location.linuxUser);
        String shell = RepositoryLocation.clean(plan.selectedShell).isEmpty() ? "/bin/sh" : plan.selectedShell;
        String quotedPayload = shellQuote(payload);
        String inside;
        if (!user.isEmpty() && !user.equals("root") && plan.userSwitchMethod.equals(RootExecutionPlan.METHOD_RUNUSER)) {
            inside = "runuser -u " + shellQuote(user) + " -- " + shellQuote(shell) + " -c " + quotedPayload;
        } else if (!user.isEmpty() && !user.equals("root") && plan.userSwitchMethod.equals(RootExecutionPlan.METHOD_SU)) {
            inside = "su - " + shellQuote(user) + " -s " + shellQuote(shell) + " -c " + quotedPayload;
        } else if (plan.userSwitchMethod.equals(RootExecutionPlan.METHOD_DIRECT_ROOT)
                || plan.userSwitchMethod.equals(RootExecutionPlan.METHOD_ROOT)) {
            inside = shellQuote(shell) + " -c " + quotedPayload;
        } else {
            inside = shellQuote(shell) + " -c " + quotedPayload;
        }
        return "export HOME=" + shellQuote(TERMUX_HOME) + " PREFIX=" + shellQuote(TERMUX_PREFIX)
                + " PATH=" + shellQuote(TERMUX_HOME + "/.local/bin:" + TERMUX_PREFIX + "/bin:/system/bin:/system/xbin") + "; "
                + "ctl=$(command -v chrootctl 2>/dev/null || true); "
                + "[ -n \"$ctl\" ] || { echo 'chrootctl unavailable'; exit 69; }; "
                + "\"$ctl\" --name " + shellQuote(location.chrootName)
                + " exec -- " + shellQuote(shell) + " -c " + shellQuote(inside);
    }

    private String chrootDoctorShell(RepositoryLocation location, String payload) {
        String inside = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH; " + payload;
        return "export HOME=" + shellQuote(TERMUX_HOME) + " PREFIX=" + shellQuote(TERMUX_PREFIX)
                + " PATH=" + shellQuote(TERMUX_HOME + "/.local/bin:" + TERMUX_PREFIX + "/bin:/system/bin:/system/xbin") + "; "
                + "ctl=$(command -v chrootctl 2>/dev/null || true); "
                + "[ -n \"$ctl\" ] || { echo 'chrootctl unavailable'; exit 69; }; "
                + "\"$ctl\" --name " + shellQuote(location.chrootName)
                + " exec -- /bin/sh -c " + shellQuote(inside);
    }

    private RepositoryInspection parseInspection(CommandResult result) {
        RepositoryInspection inspection = new RepositoryInspection();
        String text = result.text();
        if (text.contains("__DEVTOOL_ERROR__\nmissing")) {
            inspection.status = "missing";
            inspection.error = "Repository directory does not exist";
            return inspection;
        }
        if (text.contains("__DEVTOOL_ERROR__\nnot_git")) {
            inspection.status = "not_git";
            inspection.error = "Directory is not a Git repository";
            return inspection;
        }
        if (result.exitCode != 0) {
            inspection.status = "error";
            inspection.error = compact(text, "Repository inspection failed");
            return inspection;
        }
        inspection.gitRoot = section(text, "__DEVTOOL_ROOT__", "__DEVTOOL_STATUS__").trim();
        String status = section(text, "__DEVTOOL_STATUS__", "__DEVTOOL_REMOTE__");
        inspection.remote = section(text, "__DEVTOOL_REMOTE__", "__DEVTOOL_PROJECT__").trim();
        inspection.projectType = after(text, "__DEVTOOL_PROJECT__").trim();
        for (String line : status.split("\\R")) {
            if (line.startsWith("# branch.head ")) inspection.branch = line.substring(14).trim();
            else if (line.startsWith("# branch.upstream ")) inspection.upstream = line.substring(18).trim();
            else if (line.startsWith("# branch.ab ")) {
                java.util.regex.Matcher matcher = java.util.regex.Pattern
                        .compile("\\+(\\d+)\\s+-(\\d+)").matcher(line);
                if (matcher.find()) {
                    inspection.ahead = Integer.parseInt(matcher.group(1));
                    inspection.behind = Integer.parseInt(matcher.group(2));
                }
            } else if (!line.trim().isEmpty() && !line.startsWith("#")) inspection.dirty = true;
        }
        inspection.status = inspection.dirty ? "modified" : "clean";
        inspection.success = true;
        return inspection;
    }

    private CommandResult executeRoot(String shell, long timeoutSeconds) {
        return executeRoot(shell, timeoutSeconds, cancellation);
    }

    private CommandResult executeRoot(String shell, long timeoutSeconds, CancellationToken token) {
        if (!operationId.isEmpty()) {
            shell = "export DEVTOOL_ANDROID_OPERATION_ID=" + shellQuote(operationId) + "; " + shell;
        }
        Process process = null;
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        AtomicBoolean truncated = new AtomicBoolean();
        Thread reader = null;
        try {
            process = new ProcessBuilder("su", "-c", shell).redirectErrorStream(true).start();
            Process running = process;
            reader = new Thread(() -> copyBounded(running.getInputStream(), output, truncated), "devtool-root-reader");
            reader.start();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(Math.max(1L, timeoutSeconds));
            boolean finished = false;
            boolean cancelled = false;
            while (System.nanoTime() < deadline) {
                if (token != null && token.cancelled()) {
                    cancelled = true;
                    break;
                }
                if (process.waitFor(100L, TimeUnit.MILLISECONDS)) {
                    finished = true;
                    break;
                }
            }
            if (!finished) {
                if (cancelled && !operationId.isEmpty()) terminateOperationProcess(operationId);
                process.destroy();
                if (!process.waitFor(1L, TimeUnit.SECONDS)) process.destroyForcibly();
                if (reader != null) reader.join(1000L);
                DebugRecorder.warning(context, "root", cancelled ? "ROOT_EXECUTION_CANCELLED" : "ROOT_EXECUTION_TIMEOUT",
                        "RootCommandBroker", "", "", operationId, "",
                        cancelled ? "Root command cancelled" : "Root command timed out",
                        DebugRecorder.details("timeout_seconds", timeoutSeconds, "output_bytes", output.size()));
                return new CommandResult(cancelled ? 130 : 124, output.toByteArray(), !cancelled,
                        truncated.get(), cancelled);
            }
            if (reader != null) reader.join(1500L);
            int exit = process.exitValue();
            if (exit != 0) {
                DebugRecorder.error(context, "root", "ROOT_EXECUTION_FAILED", "RootCommandBroker",
                        "", "", operationId, "", "Root command exited non-zero",
                        DebugRecorder.details("exit_code", exit, "output_preview", compact(new String(output.toByteArray(), StandardCharsets.UTF_8), "")));
            }
            return new CommandResult(exit, output.toByteArray(), false, truncated.get(), false);
        } catch (Exception error) {
            String message = String.valueOf(error.getMessage());
            DebugRecorder.error(context, "root", "ROOT_EXECUTION_FAILED", "RootCommandBroker",
                    "", "", operationId, "", message, DebugRecorder.details("exception", error.getClass().getSimpleName()));
            return new CommandResult(127,
                    message.getBytes(StandardCharsets.UTF_8), false, false);
        } finally {
            if (process != null) process.destroy();
        }
    }

    private static void copyBounded(InputStream input, ByteArrayOutputStream output,
                                    AtomicBoolean truncated) {
        byte[] buffer = new byte[8192];
        try (InputStream stream = input) {
            int read;
            while ((read = stream.read(buffer)) >= 0) {
                int remaining = MAX_OUTPUT - output.size();
                if (remaining > 0) output.write(buffer, 0, Math.min(read, remaining));
                if (read > remaining) truncated.set(true);
            }
        } catch (Exception ignored) {
        }
    }

    private static List<String> splitNull(byte[] bytes) {
        List<String> values = new ArrayList<>();
        int start = 0;
        for (int i = 0; i <= bytes.length; i++) {
            if (i == bytes.length || bytes[i] == 0) {
                if (i > start) {
                    String value = new String(bytes, start, i - start, StandardCharsets.UTF_8).trim();
                    if (!value.isEmpty()) values.add(value);
                }
                start = i + 1;
            }
        }
        return values;
    }


    private static String pathArguments(List<String> paths) {
        if (paths == null || paths.isEmpty()) return "";
        StringBuilder values = new StringBuilder();
        LinkedHashSet<String> unique = new LinkedHashSet<>();
        for (String candidate : paths) {
            String path = safeRelativePath(candidate);
            if (path.isEmpty() || !unique.add(path)) continue;
            if (values.length() > 0) values.append(' ');
            values.append(shellQuote(path));
        }
        return values.toString();
    }

    private static String safeOperationId(String value) {
        String marker = value == null ? "" : value.trim();
        return marker.matches("[A-Za-z0-9._-]{1,160}") ? marker : "";
    }

    private static String safeRelativePath(String value) {
        String path = value == null ? "" : value;
        if (path.isEmpty() || path.indexOf('\0') >= 0 || path.startsWith("/")) return "";
        for (String part : path.split("/", -1)) {
            if (part.equals("..") || part.isEmpty()) return "";
        }
        return path;
    }

    private static String safeCommitMessage(String value) {
        String message = value == null ? "" : value.trim();
        if (message.isEmpty() || message.length() > 16_384 || message.indexOf('\0') >= 0) return "";
        for (int index = 0; index < message.length(); index++) {
            char character = message.charAt(index);
            if (character < 0x20 && character != '\n' && character != '\r' && character != '\t') return "";
        }
        return message;
    }

    private static String safeStashMessage(String value) {
        String message = value == null ? "" : value.trim();
        if (message.length() > 512 || message.indexOf('\0') >= 0 || message.indexOf('\n') >= 0 || message.indexOf('\r') >= 0) return "";
        return message;
    }

    private static String safeRefName(String value) {
        String ref = value == null ? "" : value.trim();
        if (ref.isEmpty() || ref.length() > 255 || ref.indexOf('\0') >= 0 || ref.startsWith("-")
                || ref.contains(" ") || ref.contains("\\") || ref.contains("..") || ref.contains("@{")
                || ref.endsWith(".") || ref.endsWith("/") || ref.contains("//")) return "";
        for (int index = 0; index < ref.length(); index++) {
            char character = ref.charAt(index);
            if (character < 0x20 || character == 0x7f || character == '~' || character == '^'
                    || character == ':' || character == '?' || character == '*' || character == '[') return "";
        }
        return ref;
    }

    private static String safeStashReference(String value) {
        String reference = value == null ? "" : value.trim();
        return reference.matches("stash@\\{\\d+}") ? reference : "";
    }

    private static String cleanHash(String value) {
        String hash = value == null ? "" : value.trim();
        return hash.matches("[0-9a-fA-F]{7,64}") ? hash : "";
    }

    private static String commandPath(RepositoryLocation location) {
        if (location.environment.equals("native-termux") && !location.hostPath.isEmpty()) return location.hostPath;
        return location.path;
    }

    static String shellQuote(String value) {
        String clean = value == null ? "" : value;
        return "'" + clean.replace("'", "'\\''") + "'";
    }

    private static String escapeForDoubleQuotes(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("$", "\\$").replace("`", "\\`");
    }

    private static String section(String text, String startMarker, String endMarker) {
        int start = text.indexOf(startMarker);
        if (start < 0) return "";
        start += startMarker.length();
        if (start < text.length() && text.charAt(start) == '\n') start++;
        int end = text.indexOf(endMarker, start);
        return end < 0 ? text.substring(start) : text.substring(start, end);
    }

    private static String after(String text, String marker) {
        int start = text.indexOf(marker);
        if (start < 0) return "";
        start += marker.length();
        if (start < text.length() && text.charAt(start) == '\n') start++;
        return text.substring(start);
    }

    private static String remoteTransport(String remote) {
        String value = remote == null ? "" : remote.trim().toLowerCase(java.util.Locale.ROOT);
        if (value.startsWith("git@") || value.startsWith("ssh://")) return "ssh";
        if (value.startsWith("https://") || value.startsWith("http://")) return "https";
        if (value.startsWith("file://") || value.startsWith("/") || value.startsWith(".")) return "local";
        return value.isEmpty() ? "none" : "other";
    }

    private static String remoteHost(String remote) {
        String value = remote == null ? "" : remote.trim();
        String host = "";
        if (value.startsWith("git@")) {
            int colon = value.indexOf(':');
            if (colon > 4) host = value.substring(4, colon);
        } else if (value.startsWith("ssh://")) {
            try { host = android.net.Uri.parse(value).getHost(); } catch (RuntimeException ignored) { host = ""; }
        } else if (value.startsWith("https://") || value.startsWith("http://")) {
            try { host = android.net.Uri.parse(value).getHost(); } catch (RuntimeException ignored) { host = ""; }
        }
        return host != null && host.matches("[A-Za-z0-9._:-]+") ? host : "";
    }

    static final class CancellationToken {
        private final java.util.concurrent.atomic.AtomicBoolean value = new java.util.concurrent.atomic.AtomicBoolean();
        void cancel() { value.set(true); }
        boolean cancelled() { return value.get(); }
    }

    private static String compact(String value, String fallback) {
        String clean = value == null ? "" : value.trim().replace('\n', ' ').replace('\r', ' ');
        if (clean.isEmpty()) clean = fallback;
        return clean.length() > 300 ? clean.substring(0, 297) + "…" : clean;
    }

    static final class CommandResult {
        final int exitCode;
        final byte[] bytes;
        final boolean timedOut;
        final boolean truncated;
        final boolean cancelled;

        CommandResult(int exitCode, byte[] bytes, boolean timedOut) {
            this(exitCode, bytes, timedOut, false, false);
        }

        CommandResult(int exitCode, byte[] bytes, boolean timedOut, boolean truncated) {
            this(exitCode, bytes, timedOut, truncated, false);
        }

        CommandResult(int exitCode, byte[] bytes, boolean timedOut, boolean truncated, boolean cancelled) {
            this.exitCode = exitCode;
            this.bytes = bytes == null ? new byte[0] : bytes;
            this.timedOut = timedOut;
            this.truncated = truncated;
            this.cancelled = cancelled;
        }

        String text() { return new String(bytes, StandardCharsets.UTF_8); }

        static CommandResult error(String message) {
            return new CommandResult(64, String.valueOf(message).getBytes(StandardCharsets.UTF_8), false);
        }
    }
}
