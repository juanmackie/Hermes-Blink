package com.you.hermeswidget.widget

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The instrumentation that decides "did the press reach us?".
 *
 * Round 13's central error: the "fired" counter lived in `HermesWidgetReceiver`, which
 * never sees a Glance action broadcast — Glance delivers `actionRunCallback` to its own
 * merged `ActionCallbackBroadcastReceiver`. The counter could not increment, it read 0
 * after a real press, and that 0 was read as evidence. A measurement that cannot move is
 * not a measurement.
 *
 * These tests assert where the count is taken and that a throw is caught and recorded.
 * They cannot assert that the calls *execute* — no static check can distinguish a live call
 * from a disabled one — so the honest limit is stated rather than papered over: a device
 * press is what proves the count moves, and the counter is deliberately named `reached`
 * so that a zero is read as "not observed" rather than as "not fired".
 */
class ActionCallbackInstrumentationTest {

    private val source: String = locate("ActionCallbacks.kt")
    private val code: String = source
        .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
        .lines().filterNot { it.trimStart().startsWith("//") }
        .joinToString("\n")
        .let { Regex("""//.*""").replace(it, "") }

    /** Reads a source file from the app module, from either the widget or net package. */
    private fun locate(name: String): String {
        var dir: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        repeat(4) {
            val base = dir?.resolve("src/main/java/com/you/hermeswidget")
            for (pkg in listOf("widget", "net", "")) {
                val candidate = base?.resolve(pkg)?.resolve(name)
                if (candidate != null && candidate.isFile) return candidate.readText()
            }
            dir = dir?.parentFile
        }
        throw AssertionError("could not locate $name from ${System.getProperty("user.dir")}")
    }

    /** The body of a brace-delimited block that starts at `marker`, braces counted. */
    private fun blockStartingAt(marker: String): String = braceBlock(code, marker)

    private fun braceBlock(text: String, marker: String): String {
        val start = text.indexOf(marker)
        assertTrue("could not find `$marker`", start >= 0)
        val open = text.indexOf('{', start)
        assertTrue("`$marker` is not a block", open > start)
        var depth = 0
        var index = open
        while (index < text.length) {
            when (text[index]) {
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return text.substring(open, index)
                }
            }
            index++
        }
        throw AssertionError("unbalanced braces after `$marker`")
    }

    @Test
    fun `the counter is taken in the callback, not in the widget receiver`() {
        val onAction = blockStartingAt("override suspend fun onAction(")
        assertTrue(
            "the reached-count must be recorded in onAction, the only place in this app " +
                "that observes a Glance action dispatch",
            onAction.contains("recordActionReached"),
        )
        // And it must happen before the handler can return or throw.
        val recordAt = onAction.indexOf("recordActionReached")
        val handlerAt = onAction.indexOf("handle(")
        assertTrue("the count must precede the handler", recordAt in 0 until handlerAt)

        val receiver = locate("HermesWidgetReceiver.kt")
        assertTrue(
            "HermesWidgetReceiver must not count actions: Glance never delivers an action " +
                "broadcast there",
            !receiver.contains("recordActionReached") && !receiver.contains("ActionCallbackBroadcastReceiver:"),
        )
    }

    @Test
    fun `a throwing handler is caught and both facts are recorded`() {
        val catchBlock = blockStartingAt("catch (error: Throwable)")
        assertTrue(
            "a handler that throws must be counted, or it is indistinguishable from a " +
                "press that never arrived",
            catchBlock.contains("recordCallbackException"),
        )
        assertTrue(
            "the exception must also reach the outcome trail, which is what Diagnostics shows",
            catchBlock.contains("recordActionOutcome"),
        )
        assertTrue(
            "the exception should be logged as well, for logcat",
            catchBlock.contains("Log.e"),
        )
    }

    @Test
    fun `the counter is named for what it measures`() {
        // `fired` implied a broadcast was produced, which this counter never observed.
        // The name is the difference between "no press got here" and "no press happened".
        assertTrue("the counter must be named `reached`", code.contains("recordActionReached"))
        assertTrue("the old name must be gone", !code.contains("recordActionFired"))
    }

    @Test
    fun `the count and the exception count are recorded independently`() {
        // The store lives in Config, not in the callback.
        val config = locate("Config.kt")
            .replace(Regex("""/\*.*?\*/""", RegexOption.DOT_MATCHES_ALL), "")
            .lines().filterNot { it.trimStart().startsWith("//") }
            .joinToString("\n")
            .let { Regex("""//.*""").replace(it, "") }
        val record = braceBlock(config, "fun recordActionReached(")
        assertTrue("the count is a field", record.contains("count"))
        assertTrue(
            "exceptions must be carried forward rather than reset, so a throw is never " +
                "lost by the next ordinary press",
            record.contains("exceptions"),
        )
    }
}
