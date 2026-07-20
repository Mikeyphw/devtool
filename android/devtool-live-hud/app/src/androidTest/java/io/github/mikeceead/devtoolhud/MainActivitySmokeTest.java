package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

@RunWith(AndroidJUnit4.class)
public final class MainActivitySmokeTest {
    @Test
    public void testPackageAndMainActivityAreInstalled() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("io.github.mikeceead.devtoolhud.debug", context.getPackageName());
        ComponentName component = new ComponentName(context, MainActivity.class);
        ActivityInfo info = context.getPackageManager().getActivityInfo(component, 0);
        assertNotNull(info);
        assertTrue(info.exported);
    }

    @Test
    public void testMainActivityLaunchesAndStops() {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Intent intent = new Intent(Intent.ACTION_MAIN)
                .setClass(context, MainActivity.class)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = InstrumentationRegistry.getInstrumentation().startActivitySync(intent);
        assertNotNull(activity);
        assertEquals(MainActivity.class, activity.getClass());
        activity.finish();
    }
}
