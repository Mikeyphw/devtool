package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.test.InstrumentationTestCase;

public final class MainActivitySmokeTest extends InstrumentationTestCase {
    public void testPackageAndMainActivityAreInstalled() throws Exception {
        Context context = getInstrumentation().getTargetContext();
        assertEquals("io.github.mikeceead.devtoolhud.debug", context.getPackageName());
        ComponentName component = new ComponentName(context, MainActivity.class);
        ActivityInfo info = context.getPackageManager().getActivityInfo(component, 0);
        assertNotNull(info);
        assertTrue(info.exported);
    }

    public void testMainActivityLaunchesAndStops() {
        Context context = getInstrumentation().getTargetContext();
        Intent intent = new Intent(Intent.ACTION_MAIN)
                .setClass(context, MainActivity.class)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = getInstrumentation().startActivitySync(intent);
        assertNotNull(activity);
        assertEquals(MainActivity.class, activity.getClass());
        activity.finish();
    }
}
