package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class RemoteDiagnostics {
    String remote = "";
    String transport = "none";
    String host = "";
    String credentialHelper = "";
    String error = "";
    boolean gitAvailable;
    boolean sshAvailable;
    boolean sshAgentAvailable;
    boolean knownHost;
    boolean askPassConfigured;
    boolean ready;
    List<String> identityFiles = new ArrayList<>();
    List<String> remediationSteps = new ArrayList<>();

    String summary() {
        if (!error.isEmpty()) return error;
        if (remote.isEmpty()) return "No remote is configured.";
        if (transport.equals("ssh")) {
            if (!sshAvailable) return "SSH client is unavailable in this repository environment.";
            if (!knownHost) return "SSH host key is not present in known_hosts.";
            if (!sshAgentAvailable && identityFiles.isEmpty()) return "No SSH agent or readable identity file was detected.";
            return "SSH prerequisites are available. Authentication is tested only when a network action runs.";
        }
        if (transport.equals("https")) {
            if (credentialHelper.isEmpty() && !askPassConfigured) {
                return "No Git credential helper or askpass provider was detected.";
            }
            return "HTTPS credential plumbing is configured. Credentials are never displayed.";
        }
        return "Remote transport is not recognized by the passive diagnostic check.";
    }
}
