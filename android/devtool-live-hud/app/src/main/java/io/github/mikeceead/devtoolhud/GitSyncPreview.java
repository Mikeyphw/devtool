package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class GitSyncPreview {
    String upstream = "";
    int ahead;
    int behind;
    String error = "";
    List<GitSyncCommit> outgoing = new ArrayList<>();
    List<GitSyncCommit> incoming = new ArrayList<>();

    boolean available() { return error.isEmpty() && !upstream.isEmpty(); }
}
