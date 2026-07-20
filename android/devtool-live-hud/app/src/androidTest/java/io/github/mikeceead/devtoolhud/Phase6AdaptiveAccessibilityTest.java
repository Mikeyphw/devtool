package io.github.mikeceead.devtoolhud;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

@RunWith(AndroidJUnit4.class)
public final class Phase6AdaptiveAccessibilityTest {
    @Test
    public void testAdaptivePolicyHandlesPhoneTabletFoldableAndLargeText() {
        Context target = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Configuration phoneConfig = new Configuration(target.getResources().getConfiguration());
        phoneConfig.screenWidthDp = 360;
        phoneConfig.fontScale = 1.0f;
        Context phone = target.createConfigurationContext(phoneConfig);
        assertFalse(AdaptiveLayout.wide(phone));
        assertTrue(AdaptiveLayout.verticalActions(phone));

        Configuration tabletConfig = new Configuration(phoneConfig);
        tabletConfig.screenWidthDp = 900;
        tabletConfig.fontScale = 1.0f;
        Context tablet = target.createConfigurationContext(tabletConfig);
        assertTrue(AdaptiveLayout.wide(tablet));
        assertTrue(AdaptiveLayout.twoPane(tablet));
        assertFalse(AdaptiveLayout.verticalActions(tablet));

        Configuration enlargedConfig = new Configuration(tabletConfig);
        enlargedConfig.fontScale = 1.8f;
        Context enlarged = target.createConfigurationContext(enlargedConfig);
        assertTrue(AdaptiveLayout.wide(enlarged));
        assertFalse(AdaptiveLayout.twoPane(enlarged));
        assertTrue(AdaptiveLayout.verticalActions(enlarged));
    }

    @Test
    public void testMainActivityButtonsHaveLabelsAndTouchTargets() {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Intent intent = new Intent(Intent.ACTION_MAIN)
                .setClass(context, MainActivity.class)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = InstrumentationRegistry.getInstrumentation().startActivitySync(intent);
        assertNotNull(activity);
        InstrumentationRegistry.getInstrumentation().waitForIdleSync();
        int[] counts = inspect(activity.getWindow().getDecorView(), context);
        assertTrue("Expected interactive buttons", counts[0] >= 5);
        assertEquals("Every button must have a content description", counts[0], counts[1]);
        assertEquals("Every button must meet the 48dp touch target", counts[0], counts[2]);
        activity.finish();
    }

    private static int[] inspect(View view, Context context) {
        int[] counts = new int[3];
        if (view instanceof Button) {
            counts[0]++;
            CharSequence description = view.getContentDescription();
            if (description != null && description.toString().trim().length() > 0) counts[1]++;
            int minimum = Math.round(48f * context.getResources().getDisplayMetrics().density);
            if (view.getMinimumHeight() >= minimum || view.getMeasuredHeight() >= minimum) counts[2]++;
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                int[] child = inspect(group.getChildAt(i), context);
                counts[0] += child[0];
                counts[1] += child[1];
                counts[2] += child[2];
            }
        }
        return counts;
    }
}
