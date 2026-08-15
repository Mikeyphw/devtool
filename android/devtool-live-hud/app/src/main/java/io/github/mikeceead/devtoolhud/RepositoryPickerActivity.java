package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class RepositoryPickerActivity extends Activity {
    static final String EXTRA_MODE = "mode";
    static final String EXTRA_ENVIRONMENT = "environment";
    static final String EXTRA_CHROOT = "chroot";
    static final String EXTRA_USER = "linux_user";
    static final String EXTRA_PATH = "path";
    static final String MODE_ADD = "add";
    static final String MODE_BROWSE = "browse";

    private static final ExecutorService EXECUTOR = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private UiKit ui;
    private Spinner environment;
    private EditText chroot;
    private EditText user;
    private EditText path;
    private LinearLayout directoryList;
    private TextView state;
    private String mode;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ui = new UiKit(this);
        mode = getIntent().getStringExtra(EXTRA_MODE);
        if (mode == null || mode.isEmpty()) mode = MODE_ADD;
        getWindow().setStatusBarColor(ui.palette.background);
        getWindow().setNavigationBarColor(ui.palette.background);
        setContentView(build());
        refreshDirectories();
    }

    private View build() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(ui.palette.background);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(0, bars.top, 0, bars.bottom);
            }
            return insets;
        });

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(ui.dp(20), ui.dp(18), ui.dp(20), ui.dp(12));
        header.addView(ui.text(mode.equals(MODE_ADD) ? "Add repository" : "Browse repository",
                UiTypography.Role.PAGE_TITLE, ui.palette.text));
        header.addView(ui.text("Browse Termux, chroot and root-visible directories without granting broad storage access.",
                UiTypography.Role.BODY_SMALL, ui.palette.muted));
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(ui.dp(16), 0, ui.dp(16), ui.dp(24));

        LinearLayout location = ui.surface();
        environment = spinner(new String[]{"Termux", "Chroot", "Root"}, displayEnvironment());
        location.addView(label("Environment"));
        location.addView(environment);
        chroot = ui.input(value(EXTRA_CHROOT, "androidos"), "Chroot name", false);
        user = ui.input(value(EXTRA_USER, ""), "Linux user", false);
        path = ui.input(initialPath(), "Directory path", false);
        location.addView(chroot);
        location.addView(user);
        location.addView(path);
        location.addView(ui.secondaryButton("Open path", v -> refreshDirectories()));
        content.addView(location);

        state = ui.text("Reading directories…", UiTypography.Role.BODY_SMALL, ui.palette.muted);
        content.addView(state);
        directoryList = new LinearLayout(this);
        directoryList.setOrientation(LinearLayout.VERTICAL);
        content.addView(directoryList);
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setPadding(ui.dp(12), ui.dp(8), ui.dp(12), ui.dp(8));
        actions.setBackgroundColor(ui.palette.surface);
        Button up = ui.secondaryButton("Up", v -> goUp());
        Button copy = ui.secondaryButton("Copy path", v -> copyPath());
        Button choose = ui.button(mode.equals(MODE_ADD) ? "Add repository" : "Close", ui.palette.green,
                v -> finishSelection());
        actions.addView(up, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        actions.addView(copy, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        actions.addView(choose, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1.25f));
        root.addView(actions);

        environment.setOnItemSelectedListener(new SimpleItemSelectedListener(() -> {
            boolean isChroot = selectedEnvironment().equals("chroot");
            chroot.setVisibility(isChroot ? View.VISIBLE : View.GONE);
            user.setVisibility(isChroot ? View.VISIBLE : View.GONE);
        }));
        return root;
    }

    private void refreshDirectories() {
        RepositoryLocation location = location();
        state.setText(getString(R.string.repository_picker_reading_format, location.displayEnvironment(), location.path));
        directoryList.removeAllViews();
        EXECUTOR.execute(() -> {
            List<String> directories = new RepositoryScanner(this).directories(location);
            main.post(() -> showDirectories(location, directories));
        });
    }

    private void showDirectories(RepositoryLocation location, List<String> directories) {
        if (isFinishing()) return;
        directoryList.removeAllViews();
        if (directories.isEmpty()) {
            state.setText(getString(R.string.repository_picker_no_children));
            return;
        }
        state.setText(getResources().getQuantityString(R.plurals.repository_picker_directory_count, directories.size(), directories.size()));
        for (String directory : directories) {
            String shown = location.environment.equals("native-termux")
                    ? RepositoryLocation.termux(directory).path : directory;
            Button item = ui.quickTile(R.drawable.ic_repository, baseName(shown), shown,
                    ui.palette.cyan, v -> {
                        path.setText(shown);
                        refreshDirectories();
                    });
            directoryList.addView(item);
        }
    }

    private void goUp() {
        String current = path.getText().toString().trim();
        if (current.equals("~/") || current.equals("~") || current.equals("/")) return;
        int slash = current.lastIndexOf('/');
        String parent;
        if (current.startsWith("~/") && slash <= 1) parent = "~/";
        else parent = slash <= 0 ? "/" : current.substring(0, slash);
        path.setText(parent);
        refreshDirectories();
    }

    private void finishSelection() {
        if (mode.equals(MODE_ADD)) {
            RepositoryStore.addAndInspectAsync(this, location(), true);
            Toast.makeText(this, "Repository added; inspection is running", Toast.LENGTH_LONG).show();
        }
        finish();
    }

    private RepositoryLocation location() {
        String selected = selectedEnvironment();
        String value = path.getText().toString().trim();
        if (selected.equals("chroot")) return RepositoryLocation.chroot(
                chroot.getText().toString(), user.getText().toString(), value);
        if (selected.equals("root")) return RepositoryLocation.root(value);
        return RepositoryLocation.termux(value);
    }

    private String selectedEnvironment() {
        String value = String.valueOf(environment.getSelectedItem());
        if (value.equalsIgnoreCase("Chroot")) return "chroot";
        if (value.equalsIgnoreCase("Root")) return "root";
        return "native-termux";
    }

    private String displayEnvironment() {
        String value = value(EXTRA_ENVIRONMENT, "native-termux");
        if (value.equals("chroot")) return "Chroot";
        if (value.equals("root")) return "Root";
        return "Termux";
    }

    private String initialPath() {
        String existing = value(EXTRA_PATH, "");
        if (!existing.isEmpty()) return existing;
        return displayEnvironment().equals("Chroot") ? "/home" : (displayEnvironment().equals("Root") ? "/" : "~/Code");
    }

    private String value(String key, String fallback) {
        String value = getIntent().getStringExtra(key);
        return value == null || value.trim().isEmpty() ? fallback : value.trim();
    }

    private Spinner spinner(String[] values, String selected) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, values);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        for (int i = 0; i < values.length; i++) if (values[i].equalsIgnoreCase(selected)) spinner.setSelection(i);
        spinner.setMinimumHeight(ui.dp(48));
        return spinner;
    }

    private TextView label(String text) {
        TextView label = ui.text(text, UiTypography.Role.LABEL, ui.palette.muted);
        label.setPadding(0, ui.dp(8), 0, 0);
        return label;
    }

    private void copyPath() {
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText("Repository path", path.getText().toString()));
        Toast.makeText(this, "Path copied", Toast.LENGTH_SHORT).show();
    }

    private static final class SimpleItemSelectedListener implements android.widget.AdapterView.OnItemSelectedListener {
        private final Runnable callback;
        SimpleItemSelectedListener(Runnable callback) { this.callback = callback; }
        @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) { callback.run(); }
        @Override public void onNothingSelected(android.widget.AdapterView<?> parent) { }
    }

    private static String baseName(String path) {
        int slash = path.lastIndexOf('/');
        return slash >= 0 && slash + 1 < path.length() ? path.substring(slash + 1) : path;
    }
}
